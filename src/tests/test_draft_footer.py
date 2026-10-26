"""Fixed draft tails disappear without changing business caveats or literal requests."""
import json
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from qwen_agent.llm.schema import ContentItem, Message
from src.agent.conversations import ConversationStore
from src.agent.public_answer import public_text
from src.config.product import DISCLAIMER, LEGACY_DISCLAIMERS, requested_draft_footer
from src.voice import client, narration, prepared
from src.web.service import ChatService

BODY = '材料尚未审核；政策效力待核验。应依据实际业务条件判断。'
TAIL = '草稿，供审核。'


@pytest.mark.parametrize('tail', [TAIL, '草稿供审核', '草稿未审核', '草稿，未审核。',
    '草稿，尚未审核。', '草稿未经审核。', '**草稿，供审核。**', '（草稿，供审核）'])
def test_only_fixed_standalone_tail_is_removed_and_ai_notice_stays(tail):
    text = f'{BODY}\n\n{tail}\n{LEGACY_DISCLAIMERS[0]}\n{DISCLAIMER}'
    assert public_text(text) == f'{BODY}\n\n{DISCLAIMER}'
    assert public_text(public_text(text)) == public_text(text)


@pytest.mark.parametrize('content', [
    '这份材料是草稿，供审核。', '草稿，供审核。\n随后说明适用条件。',
    '材料尚未审核，政策效力待核验。', '“草稿，供审核。”',
    '> 草稿，供审核。', '正文\n    草稿，供审核。',
    '```text\n草稿，供审核。\n```', '```text\n草稿，供审核。',
    '`草稿，供审核。`', '原文：\n草稿，供审核。',
    '引用原文：\n草稿，供审核。', '《草稿，供审核》',
])
def test_original_quotes_code_and_business_wording_survive(content):
    assert public_text(content) == f'{content}\n\n{DISCLAIMER}'


@pytest.mark.parametrize(('user_request', 'expected'), [
    ('请在结尾保留“草稿，供审核”。', True),
    ('请原样输出以下内容：\n事项需补充说明。\n草稿，供审核。', True),
    ('请逐字输出“草稿未审核”。', True),
    ('不要在结尾加“草稿，供审核”。', False),
    ('请不要保留草稿未审核。', False),
    ('请原样说明政策，但不要添加草稿，供审核。', False),
    ('请解释“草稿，供审核”的含义。', False),
    ('有人说草稿未审核，我需要业务分析。', False),
])
def test_explicit_request_detection_does_not_invert_negative_instructions(user_request, expected):
    assert requested_draft_footer(user_request) is expected
    assert requested_draft_footer([ContentItem(text=user_request)]) is expected


def test_history_projection_is_turn_scoped_and_does_not_rewrite_raw_records(tmp_path):
    store = ConversationStore(tmp_path / 'history')
    cid = store.create()
    messages = [
        {'role':'user', 'content':'请在结尾保留“草稿，供审核”。'},
        {'role':'assistant', 'content':f'{BODY}\n\n{TAIL}\n{DISCLAIMER}'},
        {'role':'user', 'content':'这次不要添加草稿，供审核。'},
        {'role':'assistant', 'content':f'{BODY}\n\n{TAIL}\n{DISCLAIMER}'},
    ]
    service = ChatService(store, lambda: None, tmp_path / 'uploads')
    for legacy in (True, False):
        if legacy:
            store.save(cid, messages)
        else:
            store.save(cid, [], public_messages=messages)
        snapshot = store.load(cid)
        displayed = service.public_record(cid)['messages']
        assert TAIL in displayed[1]['content']
        assert TAIL not in displayed[3]['content']
        assert BODY in displayed[3]['content'] and displayed[3]['content'].endswith(DISCLAIMER)
        assert store.load(cid) == snapshot


def test_new_answers_copy_content_and_later_history_use_the_same_clean_text(tmp_path):
    class Agent:
        name = '小弛'
        def run(self, history):
            yield [Message(role='assistant', name=self.name, content=f'{BODY}\n\n{TAIL}')]
    store = ConversationStore(tmp_path / 'history')
    cid = store.create()
    service = ChatService(store, Agent, tmp_path / 'uploads')
    first = service.answer(cid, '分析业务', [], mode='rough')
    assert first['answer'] == f'{BODY}\n\n{DISCLAIMER}'
    assert service.public_record(cid)['messages'][-1]['content'] == first['answer']
    explicit = service.answer(cid, '请在结尾保留“草稿，供审核”。', [], mode='rough')
    assert TAIL in explicit['answer']
    ordinary = service.answer(cid, '继续说明事实限制，不要添加草稿，供审核。', [], mode='rough')
    assert TAIL not in ordinary['answer']
    assert TAIL in store.load(cid)['messages'][-1]['content']


def test_voice_normalizes_before_removing_quote_markup_and_respects_explicit_request():
    text = f'{BODY}\n\n{TAIL}\n{DISCLAIMER}'
    history = [{'role':'user', 'content':'分析业务'}, {'role':'assistant', 'content':text}]
    assert client.final_answer(history, 0, conclusion_only=False) == BODY
    assert ''.join(client.speech_chunks(text)) == BODY
    assert client.plain_text('> '+TAIL) == TAIL
    history[0]['content'] = '请在结尾保留“草稿，供审核”。'
    assert client.final_answer(history, 0, conclusion_only=False).endswith(TAIL)
    assert history[1]['content'] == text


def test_narration_and_prepared_speech_drop_automatic_tail_but_keep_intended_literal(tmp_path, monkeypatch):
    config = {'model_server':'https://api.deepseek.com', 'model':'test', 'api_key':'test'}
    monkeypatch.setattr(narration, 'WORK', tmp_path)
    monkeypatch.setattr(narration, 'get_active_llm_config', lambda: config)
    monkeypatch.setattr(prepared, 'get_active_llm_config', lambda: config)
    monkeypatch.setattr(prepared, 'settings', lambda: {'voice_id':'test', 'voice_name':'test'})
    monkeypatch.setattr(prepared, '_pending', OrderedDict())
    monkeypatch.setattr(prepared, 'synthesize_chunk', lambda text, voice: b'audio')
    model = MagicMock()
    model.__enter__.return_value = model
    model.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason='stop', message=SimpleNamespace(content=json.dumps({'speech':f'{BODY}\n\n{TAIL}\n{DISCLAIMER}'}, ensure_ascii=False)))])
    monkeypatch.setattr(narration, 'OpenAI', lambda **kwargs: model)
    history = [{'role':'user', 'content':'分析业务'},
               {'role':'assistant', 'content':f'{BODY}\n\n{TAIL}\n{DISCLAIMER}'}]
    chunks, _, _ = prepared.prepare(history, 0)
    assert ''.join(chunks) == BODY
    request = model.chat.completions.create.call_args.kwargs
    assert json.loads(request['messages'][1]['content'])['answer'] == BODY
    history[0]['content'] = '请在结尾保留“草稿，供审核”。'
    chunks, _, _ = prepared.prepare(history, 0)
    assert ''.join(chunks).endswith(TAIL)
    request = model.chat.completions.create.call_args.kwargs
    assert json.loads(request['messages'][1]['content'])['answer'].endswith(TAIL)
