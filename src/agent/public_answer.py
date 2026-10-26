"""A completed-turn boundary: internal reasoning and tools never enter public history."""
from __future__ import annotations

import re
from collections.abc import Iterable

from qwen_agent.llm.schema import Message

from src.agent.case_workflow import split_interaction
from src.agent.followups import fallback_questions, split_related_questions
from src.config.product import AGENT_NAME, DISCLAIMER, requested_draft_footer, strip_product_notices

INCOMPLETE_ANSWER = '暂时未能形成完整答复。请补充问题或材料后重试。'
_INTERNAL_BLOCK = re.compile(
    r'<(think|thinking|analysis|tool_call|function_call)\b[^>]*>.*?(?:</\1\s*>|\Z)',
    re.IGNORECASE | re.DOTALL,
)


def public_text(text: str, *, preserve_draft_footer: bool = False) -> str:
    """Remove embedded internal blocks and attach the footer exactly once."""
    text, _ = split_related_questions(_INTERNAL_BLOCK.sub('', text).rstrip())
    text, _ = split_interaction(text)
    text = strip_product_notices(text, preserve_draft_footer=preserve_draft_footer)
    return f'{text or INCOMPLETE_ANSWER}\n\n{DISCLAIMER}'


def _completed_text(messages: Iterable[Message | dict], agent_name: str = AGENT_NAME) -> str:
    """Only accept a final main-agent message after every tool result/call.

    If a run ends on a tool, a specialist, or reasoning alone, earlier planning
    text cannot be mistaken for its final answer. Call only after the generator ends.
    """
    frames = list(messages)
    if not frames:
        return ''
    last = frames[-1]
    if (last.get('role') != 'assistant' or last.get('function_call') or last.get('tool_calls')
            or (last.get('name') and last.get('name') not in {agent_name, '3wagent', '小池'})):
        return ''
    content = last.get('content')
    if isinstance(content, list):
        content = ''.join(str(item.get('text') or '') for item in content)
    return _INTERNAL_BLOCK.sub('', content if isinstance(content, str) else '')


def completed_answer(messages: Iterable[Message | dict], agent_name: str = AGENT_NAME, *,
                     preserve_draft_footer: bool = False) -> str:
    return public_text(_completed_text(messages, agent_name), preserve_draft_footer=preserve_draft_footer)


def completed_interaction(messages, agent_name=AGENT_NAME):
    return split_interaction(_completed_text(messages, agent_name))


def completed_questions(messages: Iterable[Message | dict], question: str,
                        agent_name: str = AGENT_NAME) -> list[str]:
    text = _completed_text(messages, agent_name)
    answer, questions = split_related_questions(text)
    return questions or fallback_questions(question, answer)


def legacy_public_history(messages: list[dict]) -> list[dict]:
    """Project old internal transcripts without exporting their tools or inlined files."""
    result, responses = [], []
    preserve_draft_footer = False

    def flush():
        if responses:
            result.append({'role': 'assistant', 'content': completed_answer(responses, preserve_draft_footer=preserve_draft_footer)})
        responses.clear()

    for message in messages:
        if message.get('role') == 'user':
            flush()
            content = message.get('content')
            preserve_draft_footer = requested_draft_footer(content)
            if isinstance(content, list):
                content = '\n'.join(str(item.get('text') or '') for item in content)
            # Old agents replaced uploaded files with complete private parsed bodies.
            content = re.sub(r'<uploaded_document\b.*?</uploaded_document>',
                             '[附件]', str(content or ''), flags=re.DOTALL)
            result.append({'role': 'user', 'content': content, 'attachments': []})
        elif message.get('role') in {'assistant', 'function', 'tool'}:
            responses.append(message)
    flush()
    return result
