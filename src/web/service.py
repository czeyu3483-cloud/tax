"""Serialized agent execution and separate public/private conversation histories."""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from qwen_agent.llm.schema import ContentItem, Message

from src.agent.attachments import inline_uploaded_files
from src.agent.case_workflow import (
    apply_submission,
    case_turn,
    new_state,
    normalize_citations,
    normalize_clarifications,
    remove_inline_questions,
    replace_unproven_citation_links,
    submitted_source_urls,
)
from src.agent.conversations import ConversationStore, conversation_title
from src.agent.progress import progress_sink, report_progress
from src.agent.public_answer import (
    completed_answer,
    completed_interaction,
    legacy_public_history,
    public_text,
)
from src.attachments.storage import register_session_document, reset_session_documents
from src.config.answer_mode import AnswerMode, answer_mode, current_mode
from src.config.product import AGENT_NAME
from src.web.progress import ProgressTracker

# Existing runtime run IDs and attachment-session state are process globals.
# A single process executes one question at a time until these become request-scoped.
_RUN_LOCK = threading.Lock()


class BusyError(RuntimeError):
    pass


class ChatService:
    def __init__(self, store: ConversationStore, agent_factory: Callable,
                 upload_root: Path):
        self.store, self.agent_factory, self.upload_root = store, agent_factory, upload_root
        self.progress = ProgressTracker()

    def public_record(self, conversation_id: str) -> dict:
        record = self.store.load(conversation_id)
        display = record.get('public_messages')
        if display is None:
            display = legacy_public_history(record['messages'])
        # Explicit allowlist; never send the raw store record to an API response.
        messages = []
        for item in display:
            role = item.get('role')
            if role not in {'user', 'assistant'}:
                continue
            text = str(item.get('content') or '')
            if role == 'assistant':
                text = public_text(text)
            messages.append({
                'role': role, 'content': text,
                'attachments': [str(name) for name in item.get('attachments', [])]
                if role == 'user' else [],
                **({'related_questions': [],
                    'clarification_questions': item.get('clarification_questions', [])[:2],
                    'citations': item.get('citations', []),
                    'mode': item.get('mode') if item.get('mode') in ('rough', 'interactive', 'precise') else 'precise',
                    'elapsed_seconds': item.get('elapsed_seconds')}
                   if role == 'assistant' else {}),
            })
        return {**{key: record[key] for key in ('id', 'title', 'created_at', 'updated_at')},
                'title': conversation_title(messages), 'messages': messages,
                'case_state': new_state(record.get('case_state'))}

    def attachment(self, conversation_id: str, upload_id: str) -> dict:
        if not re.fullmatch(r'[0-9a-f]{32}', upload_id):
            raise ValueError('Invalid attachment ID')
        directory = self.upload_root / conversation_id / upload_id
        metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
        path = (directory / 'file' / metadata['name']).resolve()
        path.relative_to(directory.resolve())
        if not path.is_file():
            raise FileNotFoundError('Attachment unavailable')
        return {'name': metadata['name'], 'path': path}

    def change_history(self, conversation_id: str, *, restore: bool = False) -> None:
        # A running answer must finish saving before its history can be removed.
        if not _RUN_LOCK.acquire(blocking=False):
            raise BusyError('当前咨询正在处理，请待答复完成后再管理咨询记录。')
        try:
            if restore:
                self.store.restore(conversation_id)
            else:
                self.store.delete(conversation_id)
        finally:
            _RUN_LOCK.release()

    def answer(self, conversation_id: str, text: str, attachment_ids: list[str],
               request_id: str | None = None, mode: AnswerMode = 'precise', *,
               answers: list[dict] | None = None, tendency: str | None = None,
               tendency_note: str | None = None, action: str = 'respond') -> dict:
        if not _RUN_LOCK.acquire(blocking=False):
            raise BusyError('当前有其他咨询正在处理，请稍后重试。')
        failed = True
        try:
            if request_id:
                self.progress.start(request_id, conversation_id)
            with answer_mode(mode), progress_sink(
                (lambda stage: self.progress.update(request_id, stage)) if request_id else None
            ):
                result = self._answer(conversation_id, text, attachment_ids, answers=answers or [],
                                      tendency=tendency, tendency_note=tendency_note, action=action)
            failed = False
            return result
        finally:
            if request_id:
                self.progress.finish(request_id, failed=failed)
            reset_session_documents()
            _RUN_LOCK.release()

    def _answer(self, conversation_id: str, text: str, attachment_ids: list[str], *,
                answers: list[dict], tendency: str | None, tendency_note: str | None, action: str) -> dict:
        started = time.monotonic()
        mode = current_mode()
        record = self.store.load(conversation_id)
        public = self.public_record(conversation_id)['messages']
        files = [self.attachment(conversation_id, item) for item in attachment_ids]
        state = new_state(record.get('case_state'))
        if action == 'analyze_existing' and not (record['messages'] or text or files):
            raise ValueError('No case information to analyze')
        text = apply_submission(state, text, [item['name'] for item in files], answers=answers,
                                tendency=tendency, tendency_note=tendency_note, action=action)
        history = [Message(**item) for item in copy.deepcopy(record['messages'])]
        if files:
            content = [ContentItem(text=text)] if text else []
            content += [ContentItem(file=str(item['path'])) for item in files]
        else:
            content = text
        history.append(Message(role='user', content=content))
        reset_session_documents()
        for message in history:
            body = message.content
            if isinstance(body, list):
                body = '\n'.join(item.text or '' for item in body)
            for document_id in re.findall(r'<uploaded_document\s+id="([0-9a-f]+)"', body):
                register_session_document(document_id)
        report_progress('reading' if files else 'preparing')
        resolution = inline_uploaded_files(history[-1])
        final_frame = []
        # Do not yield frames or store them publicly during execution.
        if resolution.should_block:
            answer = public_text("暂时无法读取该附件，请补充具体咨询事项或重新上传可读取的文件。")
            final_frame = [Message(role="assistant", content=answer, name=AGENT_NAME)]
        else:
            report_progress('thinking')
            with case_turn(state, action):
                agent = self.agent_factory()
            # Keep complete storage intact. Rough inference omits old tool traces,
            # while retaining user material and public answers in the same order.
            model_history = history
            if mode in ('rough', 'interactive'):
                model_history = []
                users = iter(message for message in history[:-1] if message.role == 'user')
                for item in public:
                    if item['role'] == 'user':
                        original = next(users, None)
                        model_history.append(copy.deepcopy(original) if original is not None
                                             else Message(role='user', content=item['content']))
                    else:
                        model_history.append(Message(role='assistant', content=item['content'],
                                                     name=AGENT_NAME))
                model_history.append(history[-1])
            with case_turn(state, action):
                for frame in agent.run(model_history):
                    final_frame = frame
            answer = completed_answer(final_frame, getattr(agent, "name", AGENT_NAME))
        body, metadata = completed_interaction(final_frame)
        questions = ([] if mode == 'rough' or action == 'analyze_existing' else
                     normalize_clarifications(metadata.get('questions'), state['revision']))
        already_answered = {a['prompt'] for turn in state['submissions'] for a in turn['answers']}
        questions = [q for q in questions if q['prompt'] not in already_answered]
        # Questions are only displayed in bounded cards. No fabricated generic fallback questions.
        clean_body = remove_inline_questions(
            body, question_prompts=[q['prompt'] for q in normalize_clarifications(
                metadata.get('questions'), state['revision'])],
            allow_questions=mode != 'rough' and action != 'analyze_existing')
        if not clean_body and body.strip():
            clean_body = ('请按下方选项补充已掌握的情况，也可直接按现有信息分析。' if questions else
                          '根据现有信息，尚不足以形成确定判断。未明确的条件保留为未知，可按不同情形分析。')
        if clean_body:
            answer = public_text(clean_body)
        citations = normalize_citations(
            metadata.get('citations'), [*history, *final_frame], mode=mode, body=body,
            allowed_source_urls=submitted_source_urls(history),
        )
        source_checked_body = replace_unproven_citation_links(
            clean_body, metadata.get('citations'),
            allowed_source_urls=[item['url'] for item in citations],
        )
        if source_checked_body != clean_body:
            answer = public_text(source_checked_body)
        state['pending_questions'] = questions
        state['question_history'].extend(questions)
        state['stage'] = 'clarify' if questions else 'consolidate'
        if not questions:
            state['analysis_versions'].append({'revision': state['revision'], 'mode': mode,
                                                'message_index': len(public)+1,
                                                'status': 'advisory_not_verified_facts'})
        report_progress('writing')
        history.extend(final_frame)
        elapsed = round(time.monotonic() - started, 2)
        public += [
            {'role': 'user', 'content': text, 'attachments': [item['name'] for item in files]},
            {'role': 'assistant', 'content': answer, 'related_questions': [],
             'clarification_questions': questions, 'citations': citations,
             'mode': mode, 'elapsed_seconds': elapsed},
        ]
        self.store.save(conversation_id, history, public_messages=public, case_state=state)
        return {'conversation_id': conversation_id, 'answer': answer, 'related_questions': [],
                'clarification_questions': questions, 'citations': citations, 'case_state': state,
                'mode': mode, 'elapsed_seconds': elapsed}
