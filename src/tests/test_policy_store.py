from __future__ import annotations

import sqlite3

import pytest
from src.policy.store import PolicyStore
from src.policy.urls import public_http_url


def document(identity='fx-v1', **changes):
    return {
        'id': identity, 'family_id': 'fx', 'version': identity,
        'title': '公开测试资金管理规定', 'document_number': '汇发〔2025〕10号',
        'authority': '测试机关', 'source_url': 'https://www.safe.gov.cn/example/test.html',
        'domain': 'forex', 'region': 'CN', 'source_type': 'normative_document', **changes,
    }


def clause(text='企业办理跨境担保应提供相关材料。', locator='第十条'):
    return {'locator': locator, 'text': text}


def evidence(status):
    return {'status': status, 'source_url': 'https://www.safe.gov.cn/example/status.html',
            'locator': '附件第一项', 'excerpt': '这是离线测试效力材料，不是真实政策。',
            'checked_at': '2026-10-25T00:00:00+00:00'}


def test_initialization_is_empty_and_idempotent(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    store.initialize()
    assert store.stats() == {
        'documents': 0, 'clauses': 0, 'clause_vectors': 0, 'validity_evidence': 0,
        'version_relations': 0, 'update_records': 0,
        'semantic_available': False, 'embedding_model': None}
    result = store.search('外汇')
    assert result['results'] == []
    assert result['retrieval']['semantic'] == 'unavailable'


def test_exact_number_chinese_keyword_and_filters(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    store.upsert_document(document(), [clause()])
    result = store.search('汇发[2025]10号')
    assert result['results'][0]['match_type'] == 'exact_number'
    assert result['results'][0]['locator'] == '第十条'
    assert store.search('担保')['results'][0]['id'] == 'fx-v1'
    assert store.search('担保', domain='tax')['results'] == []
    assert store.search('担保', region='HK')['results'] == []
    assert store.search('担保', source_type='law')['results'] == []
    assert store.search('', document_number='汇发（2025）10号')['results']
    assert store.search('公开测试资金管理规定')['results'][0]['match_type'] == 'exact_title'


def test_dates_not_invented_from_retrieval_and_unverified_default(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    store.upsert_document(document(), [clause()])
    record = store.get_document('fx-v1')
    assert record['published_on'] is None
    assert record['effective_on'] is None
    assert record['validity'] == 'unverified'
    assert record['verified_at'] is None
    assert record['retrieved_at']
    assert store.search('担保', as_of='2020-01-01')['results'][0]['applicability'] == 'unknown'


def test_historical_version_selection_and_lineage(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    store.upsert_document(document(validity='repealed', effective_on='2020-01-01',
                                   valid_until='2025-12-31',
                                   validity_evidence=[evidence('repealed')]), [clause()])
    store.upsert_document(document('fx-v2', document_number='汇发〔2026〕11号',
                                   effective_on='2026-01-01', relations=[{
                                       'related_document_id': 'fx-v1', 'relation': 'replaces',
                                       'related_title': '公开测试资金管理规定',
                                       'source_url': 'https://www.safe.gov.cn/example/new.html',
                                       'excerpt': '替代旧版，测试用。'}]), [clause()])
    assert {x['id'] for x in store.search('担保', as_of='2023-01-01')['results']} == {'fx-v1'}
    assert {x['id'] for x in store.search('担保', as_of='2026-10-01')['results']} == {'fx-v2'}
    store.upsert_document(document(), [clause('新版索引文字')])
    assert store.get_document('fx-v2')['version_relations'][0]['related_document_id'] == 'fx-v1'


def test_verified_validity_requires_specific_evidence(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    with pytest.raises(ValueError, match='matching official evidence'):
        store.upsert_document(document(validity='effective'), [clause()])
    with pytest.raises(ValueError, match='matching official evidence'):
        store.upsert_document(document(validity='effective',
                                       validity_evidence=[evidence('expired')]), [clause()])
    store.upsert_document(document(validity='effective',
                                   validity_evidence=[evidence('effective')]), [clause()])
    assert store.get_document('fx-v1')['validity_evidence'][0]['locator'] == '附件第一项'
    assert store.search('担保', validity='effective')['results'][0]['validity_evidence'][0]['excerpt']
    assert store.stats()['documents'] == 1


def test_replace_delete_remove_old_keyword_and_vector_indexes(tmp_path):
    def embed(texts):
        return [[1.0, 0.0] if '苹果' in text else [0.0, 1.0] for text in texts]

    store = PolicyStore(tmp_path / 'policy.sqlite', embedding=embed, embedding_model='offline-test-v1')
    store.upsert_document(document(), [clause('苹果', '第一条'), clause('苹果', '第二条')])
    assert len(store.search('苹果', semantic=False)['results']) == 2
    store.upsert_document(document(), [clause('香蕉', '第三条')])
    assert not store.search('苹果', semantic=False)['results']
    assert not store.search('苹果')['results']  # removed vectors cannot survive
    assert store.stats()['clauses'] == store.stats()['clause_vectors'] == 1
    assert store.search('香蕉')['results'][0]['locator'] == '第三条'
    assert store.delete_document('fx-v1')
    assert not store.delete_document('fx-v1')
    assert not store.search('香蕉')['results']
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT count(*) FROM clause_fts').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM clause_vectors').fetchone()[0] == 0
    assert store.stats()['update_records'] == 3


def test_semantic_available_only_with_provider_and_model(tmp_path):
    def embed(texts):
        return [[1, 0] if text in {'跨境担保', '境外保证'} else [0, 1] for text in texts]

    store = PolicyStore(tmp_path / 'policy.sqlite', embedding=embed, embedding_model='test-model')
    store.upsert_document(document(), [clause('跨境担保')])
    result = store.search('境外保证')
    assert result['retrieval']['semantic'] == 'applied'
    assert result['results'][0]['semantic_score'] == pytest.approx(1.0)
    no_model = PolicyStore(store.path)
    assert no_model.search('境外保证')['retrieval']['semantic'] == 'unavailable'
    with pytest.raises(ValueError, match='embedding_model'):
        PolicyStore(tmp_path / 'bad.sqlite', embedding=embed)


def test_embedding_failure_does_not_destroy_old_version(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    store.upsert_document(document(), [clause('原始材料')])
    store.embedding_model = 'failing'
    store.embedding = lambda texts: [[float('nan')]] * len(texts)
    with pytest.raises(ValueError, match='finite'):
        store.upsert_document(document(), [clause('替换材料')])
    assert store.search('原始材料', semantic=False)['results']
    assert store.get_document('fx-v1')['clauses'][0]['text'] == '原始材料'
    result = store.search('原始材料')
    assert result['results']
    assert result['retrieval']['semantic'] == 'failed'
    assert result['retrieval']['semantic_error'] == 'ValueError'


def test_invalid_relationship_rolls_back_document_and_fts(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    store.upsert_document(document(), [clause('旧内容')])
    broken = document(relations=[{'related_document_id': 'missing', 'relation': 'replaces',
                                  'related_title': '不存在',
                                  'source_url': 'https://www.safe.gov.cn/example/ref.html',
                                  'excerpt': '测试'}])
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_document(broken, [clause('新内容')])
    assert store.get_document('fx-v1')['clauses'][0]['text'] == '旧内容'
    assert not store.search('新内容', semantic=False)['results'] or all(
        x['text'] == '旧内容' for x in store.search('新内容', semantic=False)['results'])


def test_news_and_nonofficial_sources_are_not_policy_documents(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    with pytest.raises(ValueError, match='source type'):
        store.upsert_document(document(source_type='news'), [clause()])
    with pytest.raises(ValueError, match='official'):
        store.upsert_document(document(source_url='https://example.com/test'), [clause()])
    with pytest.raises(ValueError, match='official'):
        store.upsert_document(document(source_url='https://safe.gov.cn.evil.com/test'), [clause()])
    assert store.stats()['documents'] == 0


@pytest.mark.parametrize('value', [
    'file:///C:/test', 'javascript:alert(1)', 'http://localhost/test', 'http://127.0.0.1',
    'http://10.1.1.1', 'http://[::1]/', 'http://169.254.169.254/', 'http://2130706433/',
    'http://0x7f000001/', 'http://intranet/', 'https://test.local/', 'https://user:pass@safe.gov.cn/',
    'https://safe.gov.cn:22/', 'https://safe.gov.cn\\@evil.com/', 'https://safe.gov.cn/\nfoo',
])
def test_unsafe_public_links_rejected(value):
    assert not public_http_url(value)


def test_public_official_url_allowed():
    assert public_http_url('https://www.safe.gov.cn/example/article.html')
    assert public_http_url('https://www.gov.cn/test?a=1#chapter')


def test_explicit_update_failure_has_no_fake_success(tmp_path):
    store = PolicyStore(tmp_path / 'policy.sqlite')
    store.record_update(operation='check', succeeded=False, error='source_unavailable')
    with sqlite3.connect(store.path) as connection:
        row = connection.execute('SELECT attempted_at,succeeded_at,error FROM update_records').fetchone()
    assert row[0] and row[1] is None and row[2] == 'source_unavailable'
    assert store.stats()['documents'] == 0
