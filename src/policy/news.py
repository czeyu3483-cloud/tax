"""Conversation-scoped discovery cache, independent from the policy library.

Only coarse public business topics are searched. Result URLs are not fetched.
Provider publication dates are not verified effective dates.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.policy.urls import public_http_url

DOMAINS = {
    'forex': ('外汇管理', 'safe.gov.cn', ('外汇', '外债', '结汇', '跨境', '资本项目', '经常项目')),
    'tax': ('税收', 'chinatax.gov.cn', ('税', '发票', '扣除', '征管')),
    'commerce': ('商务', 'mofcom.gov.cn', ('商务', '贸易', '进出口', '外商投资')),
    'development': ('发展改革', 'ndrc.gov.cn', ('发改', '发展改革', '投资项目', '项目备案')),
}
CATEGORIES = {'announcement', 'amendment', 'interpretation', 'news', 'upcoming', 'other', 'all'}
TOPICS = ('外债', '结售汇', '结汇', '资本项目', '经常项目', '跨境担保', '境外投资',
          '货物贸易', '服务贸易', '外商投资', '税收优惠', '增值税', '企业所得税', '个人所得税')
REGIONS = ('北京', '天津', '河北', '山西', '内蒙古', '辽宁', '吉林', '黑龙江', '上海',
           '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南',
           '广东', '广西', '海南', '重庆', '四川', '贵州', '云南', '西藏', '陕西',
           '甘肃', '青海', '宁夏', '新疆', '香港', '澳门', '台湾')


def _default_search_factory():
    # Configuration and credentials resolve only when search is requested.
    from src.config.websearch import DeepSeekSearchSettings
    from src.websearch.deepseek import DeepSeekSearchClient
    settings = DeepSeekSearchSettings.from_env()
    return DeepSeekSearchClient(replace(settings, max_uses=1, max_results=8,
                                       max_tokens=min(settings.max_tokens, 2048),
                                       timeout_seconds=min(settings.timeout_seconds, 25)))


def _valid_id(value: str) -> None:
    if not re.fullmatch(r'[0-9a-f]{32}', value or ''):
        raise ValueError('Invalid conversation ID')


def _published_date(value: Any) -> str | None:
    if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[T ])', value):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _classify(text: str) -> tuple[str, str]:
    domain = next((key for key, (_, _, words) in DOMAINS.items()
                   if any(word in text for word in words)), 'other')
    category = ('interpretation' if any(word in text for word in ('解读', '答记者问', '问答'))
                else 'amendment' if any(word in text for word in ('废止', '修订', '修改', '失效'))
                else 'announcement' if any(word in text for word in ('公告', '通知', '发布'))
                else 'news')
    return domain, category


def _region(text: str) -> str:
    for region in REGIONS:
        if region in text:
            return region
    # An official hostname identifies the publisher, not nationwide applicability.
    return 'CN' if '全国' in text else 'unknown'


def _effective_date(raw: dict) -> tuple[str | None, str | None, str]:
    """Keep only explicit dates, and abstain when metadata/excerpts conflict."""
    found = []
    for key in ('effective_at', 'effective_date', 'effective_on'):
        value = _published_date(raw.get(key))
        if value:
            found.append((value, key + ': ' + str(raw[key]), 'metadata_unverified'))
    snippet = str(raw.get('snippet') or '')
    pattern = re.compile(r'自\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*起施行')
    for match in pattern.finditer(snippet):
        try:
            value = date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            continue
        excerpt = snippet[max(0, match.start() - 35):min(len(snippet), match.end() + 35)]
        found.append((value, excerpt, 'excerpt_unverified'))
    if len({item[0] for item in found}) > 1:
        return None, None, 'conflicting_dates'
    if found:
        return found[-1]
    return None, None, 'unknown'


def _options(question: str, domain: str, region: str, category: str) -> dict:
    if domain not in {*DOMAINS, 'all'} or category not in CATEGORIES:
        raise ValueError('Unknown policy news filter')
    if region not in {'all', 'CN', *REGIONS}:
        raise ValueError('Unknown policy news region')
    inferred, _ = _classify(question)
    search_domain = domain if domain != 'all' else inferred
    label, host, _ = DOMAINS.get(search_domain, ('政务政策', 'gov.cn', ()))
    topics = [word for word in TOPICS if word in question][:3]
    category_text = {'amendment': '修订 废止', 'interpretation': '政策解读',
                     'announcement': '公告 通知', 'news': '政策动态', 'upcoming': '即将施行 生效'}.get(category, '政策动态 发布 修订')
    place = region if region not in {'all', 'CN'} else ''
    query = ' '.join(part for part in [f'site:{host}', label, *topics, place, category_text] if part)
    return {'query': query, 'domain': domain, 'region': region, 'category': category}


class NewsService:
    """Nonblocking discovery with isolated, bounded in-memory conversation caches."""

    def __init__(self, search_factory: Callable | None = None, *,
                 refresh_interval: float | None = None, max_items: int = 100,
                 max_conversations: int = 200, max_parallel: int = 2,
                 clock: Callable[[], datetime] | None = None):
        if refresh_interval is not None and refresh_interval <= 0:
            raise ValueError('refresh_interval must be positive')
        if max_items < 1 or max_conversations < 1 or max_parallel < 1:
            raise ValueError('Cache limits must be positive')
        self.search_factory = search_factory or _default_search_factory
        self.refresh_interval = refresh_interval
        self.max_items, self.max_conversations = max_items, max_conversations
        self.clock = clock or (lambda: datetime.now(UTC))
        self._states: dict[str, dict] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._pending: dict[str, dict] = {}
        self._periodic: asyncio.Task | None = None
        self._closed = False
        self._search_slots = asyncio.Semaphore(max_parallel)

    def _now(self) -> str:
        return self.clock().astimezone(UTC).isoformat()

    def _state(self, conversation_id: str) -> dict:
        _valid_id(conversation_id)
        if conversation_id not in self._states:
            if len(self._states) >= self.max_conversations:
                idle = next((key for key in self._states if key not in self._tasks), None)
                if idle is None:
                    raise ValueError('Policy news cache is busy')
                self._states.pop(idle)
            self._states[conversation_id] = {
                'items': [], 'last_attempt': None, 'last_success': None, 'error': None,
                'running': False, 'query': None, 'options': None,
                'rejected_links': 0, 'partial_failure_count': 0,
            }
        return self._states[conversation_id]

    def snapshot(self, conversation_id: str, *, date_from: str | None = None,
                 date_to: str | None = None, category: str | None = None,
                 domain: str | None = None, region: str | None = None) -> dict:
        _valid_id(conversation_id)
        for value in (date_from, date_to):
            if value is not None and _published_date(value) != value:
                raise ValueError('Date filters must use YYYY-MM-DD')
        if date_from and date_to and date_from > date_to:
            raise ValueError('Start date must not exceed end date')
        if category and category not in CATEGORIES:
            raise ValueError('Unknown policy news category')
        if domain and domain not in {*DOMAINS, 'all', 'other'}:
            raise ValueError('Unknown policy news domain')
        if region and region not in {'all', 'CN', 'unknown', *REGIONS}:
            raise ValueError('Unknown policy news region')
        state = self._states.get(conversation_id) or {
            'items': [], 'last_attempt': None, 'last_success': None, 'error': None,
            'running': False, 'query': None, 'rejected_links': 0, 'partial_failure_count': 0,
        }
        items = []
        for item in state['items']:
            published = item['published_at']
            if (date_from or date_to) and not published:
                continue
            if date_from and published < date_from or date_to and published > date_to:
                continue
            if any(value and value != 'all' and item[key] != value
                   for key, value in (('category', category), ('domain', domain), ('region', region))):
                continue
            items.append(copy.deepcopy(item))
        return {'conversation_id': conversation_id, 'items': items,
                'status': {key: state[key] for key in (
                    'last_attempt', 'last_success', 'error', 'running', 'query',
                    'rejected_links', 'partial_failure_count')},
                'notice': '政策动态仅为检索线索，来源、发布日期及政策效力尚未经逐项核验。'}

    async def trigger(self, conversation_id: str, question: str = '', *,
                      domain: str = 'all', region: str = 'all', category: str = 'all',
                      force: bool = False) -> dict:
        if self._closed:
            raise RuntimeError('Policy news service is closed')
        options = _options(question, domain, region, category)
        state = self._state(conversation_id)
        if not question and force and state['options'] and (domain, region, category) == ('all',)*3:
            options = state['options']
        if conversation_id in self._tasks:
            if options != state['options']:
                self._pending[conversation_id] = options
            else:
                self._pending.pop(conversation_id, None)
            return self.snapshot(conversation_id)
        if not force and options == state['options'] and state['last_success']:
            return self.snapshot(conversation_id)
        self._schedule(conversation_id, options)
        return self.snapshot(conversation_id)

    def _schedule(self, conversation_id: str, options: dict) -> None:
        self._pending[conversation_id] = options
        state = self._state(conversation_id)
        state['running'] = True
        state['last_attempt'] = self._now()
        self._tasks[conversation_id] = asyncio.create_task(self._work(conversation_id))

    async def refresh(self, conversation_id: str, question: str = '', **filters) -> dict:
        await self.trigger(conversation_id, question, force=True, **filters)
        task = self._tasks.get(conversation_id)
        if task is not None:
            await asyncio.shield(task)
        return self.snapshot(conversation_id)

    def _search(self, query: str):
        return self.search_factory().search(query, limit=8)

    async def _work(self, conversation_id: str) -> None:
        state = self._states[conversation_id]
        try:
            while conversation_id in self._pending and not self._closed:
                options = self._pending.pop(conversation_id)
                state.update(options=options, query=options['query'], last_attempt=self._now())
                try:
                    async with self._search_slots:
                        response = await asyncio.to_thread(self._search, options['query'])
                    self._merge(state, response)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - provider errors may contain credentials
                    state['error'] = {'code': 'news_search_failed',
                                      'message': '政策动态更新未完成，已保留此前结果，请稍后刷新。',
                                      'type': type(exc).__name__}
        finally:
            state['running'] = False
            self._tasks.pop(conversation_id, None)

    def _merge(self, state: dict, response: Any) -> None:
        now = self._now()
        existing = {item['url']: item for item in state['items']}
        rejected = 0
        for result in response.results:
            raw = result.to_dict() if hasattr(result, 'to_dict') else result
            if not isinstance(raw, dict) or not public_http_url(str(raw.get('url', ''))):
                rejected += 1
                continue
            url = raw['url']
            title, snippet = str(raw.get('title') or '')[:500], str(raw.get('snippet') or '')[:4000]
            domain, category = _classify(title + '\n' + snippet)
            effective_at, effective_excerpt, effective_status = _effective_date(raw)
            today = datetime.fromisoformat(now).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
            if effective_at and effective_at > today:
                category = 'upcoming'
            existing[url] = {
                'id': hashlib.sha256(url.encode('utf-8')).hexdigest()[:24],
                'title': title, 'url': url, 'summary': snippet,
                'published_at': _published_date(raw.get('published_at')),
                'published_at_status': 'provider_metadata_unverified',
                'effective_at': effective_at, 'effective_date_status': effective_status,
                'effective_evidence_excerpt': effective_excerpt,
                'first_seen_at': existing.get(url, {}).get('first_seen_at', now), 'last_seen_at': now,
                'category': category, 'domain': domain, 'region': _region(title + snippet),
                'classification_status': 'inferred', 'source_type': 'discovery_lead',
                'source_verified': False, 'validity_status': 'unverified',
                'formal_source_url': None,
            }
        state['items'] = sorted(existing.values(),
                                key=lambda item: (item['published_at'] or '', item['last_seen_at']),
                                reverse=True)[:self.max_items]
        failures = len(getattr(response, 'partial_failures', []) or [])
        state.update(last_success=now, rejected_links=rejected, partial_failure_count=failures,
                     error=({'code': 'news_partial_failure',
                             'message': '部分检索未完成，以下动态可能不完整。'} if failures else None))

    def start(self) -> None:
        """Enable optional incremental checks; an empty cache causes no searches."""
        if self._closed:
            raise RuntimeError('Policy news service is closed')
        if self.refresh_interval is not None and self._periodic is None:
            self._periodic = asyncio.create_task(self._periodic_loop())

    async def _periodic_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self.refresh_interval)
            for key, state in list(self._states.items()):
                if state['options'] and key not in self._tasks:
                    self._schedule(key, state['options'])

    async def close(self) -> None:
        self._closed = True
        tasks = [*self._tasks.values(), *([self._periodic] if self._periodic else [])]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._periodic = None
