import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from qwen_agent.llm.schema import Message
from src.agent.conversations import ConversationStore
from src.config.answer_mode import answer_mode, current_mode
from src.web.app import create_app


def test_modes_persist_and_rough_omits_internal_history(tmp_path, monkeypatch):
    calls, prewarm = [], []
    monkeypatch.setattr('src.web.voice.install_voice', lambda app, service: prewarm.append)

    class Agent:
        name = '小弛'

        def run(self, history):
            calls.append((current_mode(), history))
            yield [Message(role='assistant', content='INTERNAL_PLAN', name=self.name),
                   Message(role='assistant', content='最终答复', name=self.name)]

    store = ConversationStore(tmp_path / 'conversations')
    with TestClient(create_app(store=store, agent_factory=Agent, upload_root=tmp_path/'uploads')) as client:
        cid = client.post('/api/conversations').json()['id']
        url = f'/api/conversations/{cid}/messages'
        first = client.post(url, json={'message': '事实一', 'mode': 'precise'})
        assert first.status_code == 200
        second = client.post(url, json={'message': '继续', 'mode': 'rough'})
        assert second.status_code == 200
        assert len(prewarm) == 1  # Rough has no speculative narration model request.
        assert second.json()['mode'] == 'rough'
        assert second.json()['elapsed_seconds'] >= 0
        assert calls[1][0] == 'rough'
        assert 'INTERNAL_PLAN' not in str(calls[1][1])
        assert '事实一' in str(calls[1][1])
        assert 'INTERNAL_PLAN' in str(store.load(cid)['messages'])
        messages = client.get(f'/api/conversations/{cid}').json()['messages']
        assert [m['mode'] for m in messages if m['role'] == 'assistant'] == ['precise', 'rough']
        assert client.post(url, json={'message':'x', 'mode':'invalid'}).status_code == 422
        assert client.post(url, json={'message':'再核验', 'mode':'precise'}).status_code == 200
        assert calls[-1][0] == 'precise'
    assert current_mode() == 'precise'


@pytest.mark.parametrize('mode', ['rough', 'interactive'])
def test_mode_restored_after_failure(mode):
    with pytest.raises(RuntimeError), answer_mode(mode):
        assert current_mode() == mode
        raise RuntimeError()
    assert current_mode() == 'precise'


def test_no_voice_key_skips_narration_preparation(monkeypatch):
    from src.voice import prepared
    monkeypatch.setattr(prepared, 'api_key', lambda: '')
    monkeypatch.setattr(prepared, 'prepare', lambda *args: pytest.fail('Unneeded narration call'))
    prepared.prewarm_latest([{'role': 'user', 'content': 'hello'}])


@pytest.mark.parametrize('mode', ['rough', 'interactive'])
def test_lightweight_search_never_reviews_fetches_or_retries(monkeypatch, mode):
    import src.tools.web_search as module
    run = ['first']
    monkeypatch.setattr(module, 'get_run_id', lambda: run[0])
    monkeypatch.setattr(module, 'register_discovered_urls', lambda *a, **k: None)
    def forbidden(*args, **kwargs):
        pytest.fail('Source review or full-page retrieval ran in a lightweight mode')
    monkeypatch.setattr(module, 'load_jurisdiction_search_policy', forbidden)
    monkeypatch.setattr(module, 'OfficialSourceReader', forbidden)
    with answer_mode(mode):
        tool = module.WebSearchTool()
    monkeypatch.setattr(tool, '_judge_results', forbidden)
    assert tool.source_reader is None
    requests = []
    def search(query, **kwargs):
        requests.append(query)
        return SimpleNamespace(results=[SimpleNamespace(to_dict=lambda: {
            'title':'Unofficial', 'url':'https://example.com/article', 'snippet':'unverified text'})])
    monkeypatch.setattr(tool.client, 'search', search)
    data = json.loads(tool.call({'query':'tax'}))
    assert data['quality'] == 'unverified'
    assert data['validity_review'] == 'skipped'
    assert len(data['results']) == 1
    assert tool.settings.max_uses == 1 and tool.settings.timeout_seconds <= 20
    assert json.loads(tool.call({'query':'another'}))['error']['code'] == 'search_budget_exhausted'
    assert len(requests) == 1
    run[0] = 'second'
    assert json.loads(tool.call({'query':'another'}))['status'] == 'ok'
    assert len(requests) == 2


@pytest.mark.parametrize(('mode', 'label'), [('rough', '即时答复'), ('interactive', '互动研判')])
def test_lightweight_agent_has_no_specialist_and_does_not_mutate_config(monkeypatch, mode, label):
    from qwen_agent.agents import FnCallAgent
    from src.agent.main_agent import MainAgent
    config = {'model_server':'https://api.deepseek.com', 'generate_cfg':{}}
    captured = {}
    def init(self, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(FnCallAgent, '__init__', init)
    monkeypatch.setattr('src.agent.main_agent.case_context_prompt', lambda: 'CASE_CONTEXT_MARKER')
    with answer_mode(mode):
        agent = MainAgent(llm=config, suggest_followups=True)
    assert agent.specialists == {}
    assert captured['function_list'] == ['AttachmentReadTool', 'WebSearchTool', 'WriteResult']
    assert captured['llm']['generate_cfg']['reasoning_effort'] == 'low'
    assert config['generate_cfg'] == {}
    from src.config.product import LANGUAGE_STYLE
    assert LANGUAGE_STYLE in captured['system_message'], 'Lightweight modes must retain the shared regulatory style'
    assert label in captured['system_message']
    assert '<case_interaction>' in captured['system_message']
    assert 'CASE_CONTEXT_MARKER' in captured['system_message']
    assert '<related_questions>' not in captured['system_message']
    assert captured['llm']['generate_cfg']['request_timeout'] == 45


@pytest.mark.parametrize('mode', ['rough', 'interactive'])
def test_lightweight_tool_budget_forces_finalization(mode):
    from src.agent.main_agent import MainAgent
    from src.agent.tool_loop_guard import TerminalToolResult
    agent = object.__new__(MainAgent)
    agent.answer_mode = mode
    agent._lightweight_tool_calls = 3
    with pytest.raises(TerminalToolResult):
        agent._call_tool('AttachmentReadTool', '{}')


def test_legacy_xls_real_file_and_limits():
    pytest.importorskip('xlrd')
    from src.attachments.registry import get_parser
    from src.config.attachments import AttachmentSettings
    path = Path(__file__).parent / 'fixtures' / 'legacy_values.xls'
    parser_name, parse = get_parser('.xls')
    assert parser_name == 'legacy_excel'
    chunks, warnings, metadata = parse(path, AttachmentSettings())
    text = '\n'.join(c.text for c in chunks)
    assert '测试金额' in text and '1130' in text and '2026-10-23' in text
    assert 'True' in text
    assert metadata['sheets'] == 1 and metadata['nonempty_cells'] == 4
    assert warnings
    with pytest.raises(ValueError, match='单元格'):
        parse(path, AttachmentSettings(workbook_max_nonempty_cells=1))


def test_xls_upload_and_rough_followup_retain_material(tmp_path, monkeypatch):
    pytest.importorskip('xlrd')
    from src.config.attachments import AttachmentSettings
    monkeypatch.setattr('src.agent.attachments.ATTACHMENT_SETTINGS',
                        AttachmentSettings(storage_dir=str(tmp_path/'parsed')))
    monkeypatch.setattr('src.web.voice.install_voice', lambda *args: lambda *args: None)
    captured = []

    class Agent:
        name = '小弛'
        def run(self, history):
            captured.append(history)
            yield [Message(role='assistant', content='已读取测试材料', name=self.name)]

    with TestClient(create_app(store=ConversationStore(tmp_path/'conversations'),
                              agent_factory=Agent, upload_root=tmp_path/'uploads')) as client:
        cid = client.post('/api/conversations').json()['id']
        fixture = Path(__file__).parent/'fixtures/legacy_values.xls'
        upload = client.post(f'/api/conversations/{cid}/attachments',
                             files={'file': ('样本.xls', fixture.read_bytes(), 'application/vnd.ms-excel')})
        assert upload.status_code == 201
        url = f'/api/conversations/{cid}/messages'
        assert client.post(url, json={'message':'请读材料', 'mode':'precise',
                           'attachments':[upload.json()['id']]}).status_code == 200
        assert client.post(url, json={'message':'继续解释这个金额', 'mode':'rough'}).status_code == 200
        assert '测试金额' in str(captured[-1]) and '1130' in str(captured[-1])
        assert '<uploaded_document' in str(captured[-1])


def test_precise_agent_receives_same_turn_metadata_and_case_context(monkeypatch):
    from qwen_agent.agents import FnCallAgent
    from src.agent.main_agent import MainAgent
    captured = {}
    def init(self, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(FnCallAgent, '__init__', init)
    monkeypatch.setattr('src.agent.main_agent.case_context_prompt', lambda: 'ANALYZE_EXISTING_CONTEXT')
    with answer_mode('precise'):
        agent = MainAgent(llm=None, suggest_followups=True)
    assert agent.specialists
    assert '审慎研判' in captured['system_message']
    assert 'ANALYZE_EXISTING_CONTEXT' in captured['system_message']
    assert '<case_interaction>' in captured['system_message']
    assert '<related_questions>' not in captured['system_message']


def test_cli_prompt_does_not_emit_web_metadata(monkeypatch):
    from qwen_agent.agents import FnCallAgent
    from src.agent.main_agent import MainAgent
    captured = {}
    monkeypatch.setattr(FnCallAgent, '__init__', lambda self, **kwargs: captured.update(kwargs))
    with answer_mode('interactive'):
        MainAgent(llm=None)
    assert '<case_interaction>' not in captured['system_message']
