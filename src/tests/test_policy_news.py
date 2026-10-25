"""Offline tests for asynchronous policy news, isolation and evidence boundaries."""
import asyncio
import threading
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.policy.news import NewsService
from src.policy.news_routes import build_news_router, news_lifespan
from src.websearch.protocol import SearchResponse, SearchResult

ONE, TWO = 'a' * 32, 'b' * 32


def result(url, title='外汇政策通知', published_at=None, snippet='政策事项'):
    return SearchResult(title, url, snippet, 'fake', 'fake', published_at)


class FakeSearch:
    def __init__(self, items=()):
        self.items = list(items)
        self.calls = []
        self.failure = None
        self.partial = []
        self.entered = threading.Event()
        self.release = None

    def search(self, query, *, limit):
        self.calls.append(query)
        self.entered.set()
        if self.release:
            assert self.release.wait(2)
        if self.failure:
            raise self.failure
        return SearchResponse(query, ['fake'], self.items, self.partial)


def test_empty_initialization_is_lazy_and_snapshot_is_isolated():
    made = []
    service = NewsService(lambda: made.append(True))
    assert service.snapshot(ONE)['items'] == []
    assert service.snapshot(TWO)['status']['last_attempt'] is None
    assert made == []


def test_trigger_is_nonblocking_and_does_not_send_case_details():
    async def scenario():
        search = FakeSearch([result('https://www.safe.gov.cn/item')])
        search.release = threading.Event()
        service = NewsService(lambda: search)
        pending = await service.trigger(ONE, '甲公司账号123456789的跨境外债要如何办理？')
        assert pending['status']['running'] is True
        await asyncio.to_thread(search.entered.wait, 1)
        assert not service.snapshot(ONE)['items']
        assert service.snapshot(TWO)['status']['running'] is False
        assert '甲公司' not in search.calls[0] and '123456789' not in search.calls[0]
        assert '外债' in search.calls[0]
        search.release.set()
        await service.refresh(ONE, '跨境外债')
        assert len(service.snapshot(ONE)['items']) == 1
        assert not service.snapshot(TWO)['items']
        await service.close()
    asyncio.run(scenario())


def test_untrusted_dates_links_and_validity_are_not_upgraded():
    async def scenario():
        search = FakeSearch([
            result('https://www.safe.gov.cn/a', published_at='2026-10-20'),
            result('https://www.safe.gov.cn/b', published_at='yesterday'),
            result('http://127.0.0.1/private'), result('javascript:alert(1)'),
            result('http://192.168.1.1/private'), result('https://localhost/private'),
        ])
        service = NewsService(lambda: search, clock=lambda: datetime(2026, 10, 25, tzinfo=UTC))
        response = await service.refresh(ONE, '外汇')
        assert response['status']['rejected_links'] == 4
        by_url = {item['url']: item for item in response['items']}
        assert by_url['https://www.safe.gov.cn/b']['published_at'] is None
        assert all(item['validity_status'] == 'unverified' and not item['source_verified']
                   for item in response['items'])
        assert all(item['source_type'] == 'discovery_lead' for item in response['items'])
        assert response['status']['last_success'].startswith('2026-10-25')
        await service.close()
    asyncio.run(scenario())


def test_filters_use_publication_date_and_inferred_fields():
    async def scenario():
        search = FakeSearch([
            result('https://www.safe.gov.cn/a', '上海外债政策修订', '2026-10-21'),
            result('https://www.safe.gov.cn/b', '北京外汇政策解读', '2026-10-23'),
            result('https://www.chinatax.gov.cn/c', '增值税公告', '2026-10-24'),
            result('https://www.safe.gov.cn/d', '上海外债政策修订'),
        ])
        service = NewsService(lambda: search)
        await service.refresh(ONE, '外汇')
        filtered = service.snapshot(ONE, date_from='2026-10-20', date_to='2026-10-22',
                                    category='amendment', domain='forex', region='上海')
        assert [item['url'] for item in filtered['items']] == ['https://www.safe.gov.cn/a']
        assert len(service.snapshot(ONE, domain='tax')['items']) == 1
        with pytest.raises(ValueError):
            service.snapshot(ONE, date_from='2026-11-01', date_to='2026-10-01')
        await service.close()
    asyncio.run(scenario())


def test_incremental_merge_and_failure_visibility_keep_last_success():
    async def scenario():
        times = iter(datetime(2026, 10, day, tzinfo=UTC) for day in (20, 20, 20, 21, 21, 21, 22, 22))
        search = FakeSearch([result('https://www.safe.gov.cn/a')])
        service = NewsService(lambda: search, clock=lambda: next(times))
        first = await service.refresh(ONE, '外汇')
        search.items = [result('https://www.safe.gov.cn/a', '外汇修订'),
                        result('https://www.safe.gov.cn/b')]
        second = await service.refresh(ONE)
        assert len(second['items']) == 2
        current_a = next(item for item in second['items'] if item['url'].endswith('/a'))
        assert current_a['first_seen_at'] == first['items'][0]['first_seen_at']
        search.failure = RuntimeError('secret-api-key must never appear')
        failed = await service.refresh(ONE)
        assert len(failed['items']) == 2
        assert failed['status']['last_success'] == second['status']['last_success']
        assert failed['status']['last_attempt'] != failed['status']['last_success']
        assert failed['status']['error']['code'] == 'news_search_failed'
        assert 'secret-api-key' not in str(failed)
        await service.close()
    asyncio.run(scenario())


def test_same_topic_reuses_cache_but_manual_refresh_rechecks():
    async def scenario():
        search = FakeSearch([result('https://www.safe.gov.cn/a')])
        service = NewsService(lambda: search)
        await service.refresh(ONE, '外汇')
        await service.trigger(ONE, '请分析外汇')
        assert len(search.calls) == 1
        search.partial = [{'untrusted': 'secret must not appear'}]
        refreshed = await service.refresh(ONE)
        assert len(search.calls) == 2
        assert refreshed['status']['partial_failure_count'] == 1
        assert refreshed['status']['error']['code'] == 'news_partial_failure'
        assert 'secret' not in str(refreshed)
        await service.close()
    asyncio.run(scenario())


def test_periodic_checks_only_existing_subscriptions_and_close():
    async def scenario():
        loop = asyncio.get_running_loop()
        repeated_search = asyncio.Event()

        class ObservedSearch(FakeSearch):
            def search(self, query, *, limit):
                response = super().search(query, limit=limit)
                if len(self.calls) > 1:
                    loop.call_soon_threadsafe(repeated_search.set)
                return response

        search = ObservedSearch([result('https://www.safe.gov.cn/a')])
        service = NewsService(lambda: search, refresh_interval=0.01)
        service.start()
        periodic = service._periodic
        try:
            await asyncio.sleep(0.025)
            assert search.calls == []
            await service.refresh(ONE, '外汇')
            # Observe the background thread's actual search, rather than assuming
            # a fixed number of event-loop ticks also schedules executor threads.
            await asyncio.wait_for(repeated_search.wait(), timeout=5)
            assert len(search.calls) > 1
            assert not service.snapshot(TWO)['status']['running']
            assert TWO not in service._states
        finally:
            await service.close()
        assert periodic.done()
        assert not service._tasks
        calls = len(search.calls)
        await asyncio.sleep(0.025)
        assert len(search.calls) == calls
        with pytest.raises(RuntimeError):
            await service.trigger(ONE, '外汇')
    asyncio.run(scenario())


def test_router_access_filters_and_async_refresh():
    search = FakeSearch([result('https://www.safe.gov.cn/a')])
    service = NewsService(lambda: search)
    app = FastAPI(lifespan=news_lifespan(service))
    app.include_router(build_news_router(service, conversation_exists=lambda value: value == ONE))
    with TestClient(app) as client:
        prefix = f'/api/conversations/{ONE}/policy-news'
        assert client.get(prefix).json()['items'] == []
        assert client.get(f'/api/conversations/{TWO}/policy-news').status_code == 404
        assert client.get('/api/conversations/invalid/policy-news').status_code == 422
        assert client.get(prefix, params={'date_from': 'bad-date'}).status_code == 422
        assert client.get(prefix, params={'domain': 'bad-domain'}).status_code == 400
        assert client.post(prefix + '/refresh', json={'question': '外汇'}).status_code == 202
        assert client.post(prefix + '/refresh', json={},
                           headers={'origin': 'https://foreign.example'}).status_code == 403


def test_router_fails_closed_without_conversation_check():
    app = FastAPI()
    app.include_router(build_news_router(NewsService(lambda: FakeSearch())))
    with TestClient(app) as client:
        assert client.get(f'/api/conversations/{ONE}/policy-news').status_code == 503



def test_explicit_future_effective_date_preserves_excerpt_and_upcoming_filter():
    service = NewsService(lambda: FakeSearch(), clock=lambda: datetime(2026, 10, 25, tzinfo=UTC))
    state = service._state(ONE)
    response = SearchResponse('offline', [], [
        result('https://www.safe.gov.cn/future', '外汇管理通知',
               published_at='2026-10-20', snippet='本规定自2027年1月1日起施行。请遵照执行。'),
        result('https://www.safe.gov.cn/no-date', '外汇公告', published_at='2026-11-01'),
    ], [])
    service._merge(state, response)
    future = service.snapshot(ONE, category='upcoming')['items']
    assert len(future) == 1
    assert future[0]['effective_at'] == '2027-01-01'
    assert '自2027年1月1日起施行' in future[0]['effective_evidence_excerpt']
    assert future[0]['effective_date_status'] == 'excerpt_unverified'
    assert future[0]['validity_status'] == 'unverified'
    unspecified = next(x for x in service.snapshot(ONE)['items'] if x['url'].endswith('/no-date'))
    assert unspecified['effective_at'] is None


def test_explicit_effective_metadata_is_kept_but_not_verified():
    service = NewsService(lambda: FakeSearch(), clock=lambda: datetime(2026, 10, 25, tzinfo=UTC))
    service._merge(service._state(ONE), SearchResponse('offline', [], [{
        'title': '外汇管理通知', 'url': 'https://www.safe.gov.cn/meta', 'snippet': '',
        'published_at': '2026-10-01', 'effective_at': '2026-12-15',
    }], []))
    entry = service.snapshot(ONE)['items'][0]
    assert entry['effective_at'] == '2026-12-15'
    assert entry['effective_date_status'] == 'metadata_unverified'
    assert entry['effective_evidence_excerpt'] == 'effective_at: 2026-12-15'
    assert entry['category'] == 'upcoming'
    assert entry['region'] == 'unknown'


def test_ambiguous_invalid_and_relative_effective_dates_are_not_guessed():
    from src.policy.news import _effective_date
    for snippet in (
        '自2027年2月30日起施行。',
        '自发布之日起施行。',
        '本通知自2027年1月1日起施行，旧规定自2020年1月1日起施行。',
    ):
        assert _effective_date({'snippet': snippet})[0] is None
    assert _effective_date({'effective_at': '2028-01-01',
                            'snippet': '自2027年1月1日起施行。'})[0] is None
    assert _effective_date({'published_at': '2027-01-01'})[0] is None


def test_official_publisher_does_not_establish_nationwide_applicability():
    service = NewsService(lambda: FakeSearch())
    service._merge(service._state(ONE), SearchResponse('offline', [], [
        result('https://www.safe.gov.cn/a', '外汇管理公告'),
        result('https://www.safe.gov.cn/b', '全国外汇业务通知'),
        result('https://www.safe.gov.cn/c', '上海外汇业务通知'),
    ], []))
    rows = {item['url']: item for item in service.snapshot(ONE)['items']}
    assert rows['https://www.safe.gov.cn/a']['region'] == 'unknown'
    assert rows['https://www.safe.gov.cn/b']['region'] == 'CN'
    assert rows['https://www.safe.gov.cn/c']['region'] == '上海'
    assert all(x['classification_status'] == 'inferred' for x in rows.values())


def test_upcoming_is_an_allowed_discovery_category():
    from src.policy.news import _options
    options = _options('企业账号不得外发', 'forex', 'all', 'upcoming')
    assert '即将施行' in options['query']
    assert '企业账号' not in options['query']



def test_latest_topic_cancels_obsolete_pending_refresh():
    async def scenario():
        search = FakeSearch([result('https://www.safe.gov.cn/a')])
        search.release = threading.Event()
        service = NewsService(lambda: search, max_parallel=1)
        await service.trigger(ONE, '外汇')
        await asyncio.to_thread(search.entered.wait, 1)
        await service.trigger(ONE, '税收')
        await service.trigger(ONE, '外汇')
        search.release.set()
        await service.refresh(ONE, '外汇')
        assert len(search.calls) == 1
        assert '外汇管理' in search.calls[0]
        await service.close()
    asyncio.run(scenario())
