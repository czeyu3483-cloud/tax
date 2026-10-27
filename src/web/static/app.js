'use strict';
const $ = (id) => document.getElementById(id);
const state = {id: null, messages: [], attachments: [], busy: false, caseState: null, answerDrafts: {}, tendencyDirty: false, epoch: 0, creating: null, composerCollapsed: false};
const modePicker = $('answer-mode');
const modeNames = {rough:'即时答复', interactive:'互动研判', precise:'审慎研判'};
try {
  const stored = localStorage.getItem('xiaochi-answer-mode');
  modePicker.value = Object.hasOwn(modeNames, stored) ? stored : 'interactive';
} catch (_) {modePicker.value = 'interactive';}
function updateModeHint() {
  $('answer-mode-hint').textContent = {
    rough:'直接按现有信息答复，不追问、不核验政策依据',
    interactive:'逐步补充事实与疑点，不逐条核验政策依据',
    precise:'逐步补充事实，并核查引用原文、适用条件与效力'
  }[modePicker.value];
}
modePicker.addEventListener('change', () => {
  updateModeHint(); renderMessages(false, {preserveScroll:true});
  try {localStorage.setItem('xiaochi-answer-mode', modePicker.value);} catch (_) {}
});
updateModeHint();
let agentName = '小弛';
let pendingDelete = null;
let lastDeleted = null;
let historyNoticeTimer = null;
function hideHistoryNotice() {
  clearTimeout(historyNoticeTimer); historyNoticeTimer = null;
  lastDeleted = null; $('history-notice').hidden = true;
}
function showHistoryNotice(item) {
  hideHistoryNotice(); lastDeleted = item; $('history-notice').hidden = false;
  historyNoticeTimer = setTimeout(hideHistoryNotice, 8000);
}
async function request(path, options = {}) {
  try {return await fetch(path, options);} catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('服务连接中断，请检查网络及服务运行状态后重试。');
  }
}
async function responseData(response) {
  try {return await response.json();} catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('服务响应异常，请稍后重试；如问题持续，请联系系统维护人员。');
  }
}
async function api(path, options = {}) {
  const response = await request(path, options);
  const data = await responseData(response);
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : '请求未完成，请检查输入后重试。');
  return data;
}
function showError(message = '') { $('error').textContent = message; $('error').hidden = !message; }
function updateActionAvailability() {
  $('analyze-existing').disabled = state.busy || !(
    state.messages.some(item => item.role === 'user') || $('question').value.trim() || state.attachments.length);
}
function setBusy(value) {
  state.busy = value;
  $('brand-mascot').dataset.state = value ? 'busy' : 'idle';
  for (const button of document.querySelectorAll('button')) button.disabled = value;
  for (const control of document.querySelectorAll('.clarification-panel input, #tendency, #tendency-note')) control.disabled = value || control.dataset.skipped === 'true';
  $('question').readOnly = value;
  modePicker.disabled = value;
  $('send-button').textContent = value ? '处理中' : '提交';
  $('composer-toggle').disabled = false;
  updateActionAvailability(); updateComposerSummary();
}
function resizeQuestion() {
  if (state.composerCollapsed) return;
  const question = $('question');
  question.style.height = 'auto';
  question.style.height = `${Math.min(question.scrollHeight, 240)}px`;
}
function updateComposer() {
  const compact = state.messages.some(item => item.role === 'assistant');
  $('chat-form').classList.toggle('compact', compact);
  $('question').rows = compact ? 2 : 4;
  resizeQuestion(); updateComposerSummary();
}
function updateComposerSummary() {
  const pending = $('question').value.trim() || state.attachments.length || state.tendencyDirty || submittedAnswers().length;
  $('composer-summary').textContent = state.composerCollapsed
    ? `${modeNames[modePicker.value]} · ${state.busy ? '正在处理' : pending ? '未提交内容已保留' : '展开后可继续补充'}`
    : '咨询输入';
}
function setComposerCollapsed(collapsed, focus = false) {
  state.composerCollapsed = collapsed;
  $('composer-body').hidden = collapsed;
  $('chat-form').classList.toggle('collapsed', collapsed);
  const label = collapsed ? '展开输入区' : '收起输入区';
  $('composer-toggle').setAttribute('aria-label', label);
  $('composer-toggle').setAttribute('title', label);
  $('composer-toggle').setAttribute('aria-expanded', String(!collapsed));
  updateComposer();
  if (focus) (collapsed ? $('composer-toggle') : $('question')).focus({preventScroll:true});
}
$('composer-toggle').addEventListener('click', () => setComposerCollapsed(!state.composerCollapsed, true));
function nearConversationEnd(workspace) {
  return workspace.scrollHeight - workspace.scrollTop - workspace.clientHeight < 80;
}
function scrollAnswerEnd(answer, workspace = document.querySelector('.workspace')) {
  const viewport = workspace.getBoundingClientRect();
  const composer = document.querySelector('.composer-area') || $('chat-form');
  const visibleBottom = Math.min(viewport.bottom, composer.getBoundingClientRect().top);
  // Follow the currently visible text and its small action row, not later question forms.
  const distance = answer.getBoundingClientRect().bottom + 40 - visibleBottom;
  if (distance > 0) workspace.scrollTop += distance;
}
let activeAnswerReveal = null;
function finishAnswerReveal() {
  if (activeAnswerReveal) activeAnswerReveal.finish();
}
function revealAnswer(answer, follow = true) {
  // Animate only the already completed, server-rendered answer. Never parse partial HTML.
  const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  if (document.hidden || motion?.matches) return;
  const textNodes = [], blocks = [];
  let length = 0;
  function collect(node) {
    if (node.nodeType === 3) {
      const characters = Array.from(node.nodeValue || '');
      textNodes.push({node, characters, start:length}); length += characters.length;
      return;
    }
    if (node !== answer && /^(P|DIV|UL|OL|LI|DL|DT|DD|H[1-6]|PRE|BLOCKQUOTE|TABLE|THEAD|TBODY|TFOOT|TR|TH|TD|HR)$/.test(node.tagName)) {
      blocks.push({node, start:length, hidden:node.hidden});
    }
    for (const child of node.childNodes) collect(child);
  }
  collect(answer);
  if (!length) return;
  const workspace = document.querySelector('.workspace');
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'answer-reveal-button'; button.textContent = '立即显示';
  button.setAttribute('aria-label', '立即显示完整答复');
  answer.after(button);
  answer.setAttribute('aria-busy', 'true');
  let frame = null, stopped = false, lastTop = workspace.scrollTop;
  const started = performance.now(), duration = Math.min(4500, Math.max(2000, length * 9));
  function show(count) {
    for (const {node, characters, start} of textNodes) {
      const value = characters.slice(0, Math.max(0, count - start)).join('');
      if (node.nodeValue !== value) node.nodeValue = value;
    }
    for (const block of blocks) block.node.hidden = block.hidden || count <= block.start;
  }
  function onScroll() {
    if (workspace.scrollTop < lastTop - 2) follow = false;
    else if (nearConversationEnd(workspace)) follow = true;
    lastTop = workspace.scrollTop;
  }
  const reveal = {finish() {
    if (stopped) return;
    stopped = true; cancelAnimationFrame(frame);
    workspace.removeEventListener('scroll', onScroll);
    document.removeEventListener('visibilitychange', onVisibility);
    motion?.removeEventListener?.('change', onMotion);
    show(length);
    for (const block of blocks) block.node.hidden = block.hidden;
    answer.setAttribute('aria-busy', 'false'); button.hidden = true;
    if (activeAnswerReveal === reveal) activeAnswerReveal = null;
  }};
  function onVisibility() {if (document.hidden) reveal.finish();}
  function onMotion(event) {if (event.matches) reveal.finish();}
  document.addEventListener('visibilitychange', onVisibility);
  motion?.addEventListener?.('change', onMotion);
  activeAnswerReveal = reveal;
  button.addEventListener('click', () => {
    const shouldFollow = follow;
    reveal.finish();
    if (shouldFollow) scrollAnswerEnd(answer, workspace);
  });
  show(0);
  if (follow) scrollAnswerEnd(answer, workspace);
  lastTop = workspace.scrollTop;
  workspace.addEventListener('scroll', onScroll, {passive:true});
  function advance(now) {
    if (stopped || activeAnswerReveal !== reveal) return;
    const fraction = Math.min(1, (now - started) / duration);
    show(Math.ceil(length * fraction));
    if (follow) {scrollAnswerEnd(answer, workspace); lastTop = workspace.scrollTop;}
    if (fraction >= 1) reveal.finish();
    else frame = requestAnimationFrame(advance);
  }
  frame = requestAnimationFrame(advance);
  return true;
}
let conversationScrollFrame = null;
function scrollEnd() {
  const workspace = document.querySelector('.workspace');
  cancelAnimationFrame(conversationScrollFrame);
  conversationScrollFrame = requestAnimationFrame(() => {conversationScrollFrame = null; workspace.scrollTop = workspace.scrollHeight;});
}
function closeMenu() { $('sidebar').classList.remove('open'); $('scrim').classList.remove('open'); $('menu-button').setAttribute('aria-expanded', 'false'); }
function assistantLabel(item = {}) {
  const label = document.createElement('div'); label.className = 'assistant-label';
  const name = document.createElement('span'); name.textContent = agentName;
  const note = document.createElement('span'); note.className = 'answer-caption';
  note.textContent = (modeNames[item.mode] || '审慎研判') + ' · ' + (item.mode === 'precise' || !item.mode ? '供工作参考' : '政策依据未经逐条核验');
  if (Number.isFinite(item.elapsed_seconds)) note.textContent += ` · ${item.elapsed_seconds.toFixed(1)}秒`;
  label.append(name, note); return label;
}
function renderMessages(waiting = false, {revealIndex = -1, preserveScroll = false} = {}) {
  const workspace = document.querySelector('.workspace');
  const follow = nearConversationEnd(workspace), previousTop = workspace.scrollTop;
  cancelAnimationFrame(conversationScrollFrame); conversationScrollFrame = null;
  finishAnswerReveal();
  let newAnswer = null;
  $('messages').replaceChildren();
  $('welcome').hidden = state.messages.length > 0 || waiting;
  let voiceTurn = -1;
  for (const [messageIndex, item] of state.messages.entries()) {
    const message = document.createElement('article'); message.className = `message ${item.role}`;
    if (item.role === 'user') {
      voiceTurn++;
      if (item.content) {const bubble = document.createElement('div'); bubble.className = 'user-bubble'; bubble.textContent = item.content; message.append(bubble);}
      for (const name of item.attachments || []) {const file = document.createElement('div'); file.className = 'uploaded'; file.textContent = `▤ ${name}`; message.append(file);}
    } else {
      message.append(assistantLabel(item));
      const answer = document.createElement('div'); answer.className = 'answer';
      // Only server-rendered Markdown (HTML disabled) is accepted here.
      if (item.html) answer.innerHTML = item.html; else answer.textContent = item.content;
      for (const link of answer.querySelectorAll('a')) {const href = safeHref(link.getAttribute('href')); if (href) link.href = href; else link.removeAttribute('href'); link.target = '_blank'; link.rel = 'noopener noreferrer';}
      message.append(answer);
      if (messageIndex === revealIndex) newAnswer = answer;
      const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'copy-button'; copy.textContent = '复制答复';
      copy.addEventListener('click', async () => {try {await navigator.clipboard.writeText(item.content); copy.textContent = '已复制';} catch (_) {showError('浏览器未允许复制，请选择答复文字手动复制。');}});
      message.append(copy, voiceButton(state.id, voiceTurn));
      if (Array.isArray(item.citations) && item.citations.length) message.append(renderCitations(item.citations));
      const questions = Array.isArray(item.clarification_questions) ? item.clarification_questions.slice(0, 2) : [];
      if (questions.length) {
        const active = messageIndex === state.messages.length - 1 && !waiting && modePicker.value !== 'rough';
        message.append(renderClarifications(questions, active));
      }
    }
    $('messages').append(message);
  }
  if (waiting) {
    const article = document.createElement('article'); article.className = 'message assistant'; article.append(assistantLabel({mode:modePicker.value}));
    const text = document.createElement('div'); text.className = 'waiting'; text.setAttribute('role', 'status');
    const phase = document.createElement('span'); phase.id = 'progress-text'; phase.textContent = `${agentName}正在分析咨询事项……`;
    const elapsed = document.createElement('small'); elapsed.id = 'progress-elapsed'; elapsed.className = 'waiting-elapsed'; elapsed.setAttribute('aria-hidden', 'true');
    elapsed.textContent = '已用时 0 秒';
    text.append(phase, elapsed); article.append(text); $('messages').append(article);
  }
  updateActionAvailability(); updateComposer();
  if (preserveScroll) workspace.scrollTop = previousTop;
  if (newAnswer) {if (!revealAnswer(newAnswer, follow) && follow) scrollAnswerEnd(newAnswer, workspace);}
  else if (!preserveScroll) scrollEnd();
}

function watchProgress(conversationId, requestId) {
  let stopped = false, polling = false;
  const started = performance.now();
  const tick = async () => {
    const elapsed = $('progress-elapsed');
    if (elapsed) elapsed.textContent = `已用时 ${Math.floor((performance.now() - started) / 1000)} 秒`;
    if (stopped || polling) return;
    polling = true;
    try {
      const snapshot = await api(`/api/conversations/${conversationId}/progress/${requestId}`);
      if (!stopped && snapshot.status === 'running' && $('progress-text')) $('progress-text').textContent = snapshot.message;
    } catch (_) {
      // A missing snapshot does not retry the answer request.
    } finally {polling = false;}
  };
  const timer = setInterval(tick, 1000);
  return () => {stopped = true; clearInterval(timer);};
}
function renderAttachments() {
  $('attachments').replaceChildren();
  state.attachments.forEach((item, index) => {
    const chip = document.createElement('div'); chip.className = 'attachment-chip';
    const name = document.createElement('span'); name.textContent = `▤ ${item.name}`;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', `移除 ${item.name}`);
    remove.addEventListener('click', () => {if (state.busy) return; state.attachments.splice(index, 1); renderAttachments();});
    chip.append(name, remove); $('attachments').append(chip);
  });
}
async function refreshHistory() {
  const {conversations} = await api('/api/conversations');
  $('history-count').textContent = conversations.length;
  $('history').replaceChildren();
  if (!conversations.length) {const note = document.createElement('p'); note.className = 'history-empty'; note.textContent = '暂无咨询记录'; $('history').append(note);}
  for (const item of conversations) {
    const row = document.createElement('div'); row.className = `history-row${item.id === state.id ? ' active' : ''}`;
    const button = document.createElement('button'); button.className = `history-item${item.id === state.id ? ' active' : ''}`; button.title = item.title; button.disabled = state.busy;
    const label = document.createElement('span'); label.className = 'label'; label.textContent = item.title; button.append(label);
    button.addEventListener('click', () => selectConversation(item.id));
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'history-delete'; remove.disabled = state.busy;
    remove.title = '删除记录'; remove.setAttribute('aria-label', `删除记录：${item.title}`);
    remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7M14 10v7"/></svg>';
    remove.addEventListener('click', () => {
      if (state.busy) return;
      pendingDelete = item; $('delete-title').textContent = item.title; $('delete-dialog').showModal();
    });
    row.append(button, remove); $('history').append(row);
  }
}
async function selectConversation(id) {
  if (state.busy) return;
  finishAnswerReveal();
  hideHistoryNotice();
  stopVoice();
  setBusy(true); showError();
  try {
    const record = await api(`/api/conversations/${id}`);
    state.id = record.id; state.messages = record.messages; state.attachments = []; state.answerDrafts = {}; state.caseState = record.case_state || null; syncTendency();
    localStorage.setItem('xiaochi-conversation', state.id);
    $('question').value = '';
    renderAttachments(); renderMessages(); closeMenu(); startNewsPolling(); await refreshHistory();
  } catch (error) {showError(error.message);} finally {setBusy(false);}
}
async function ensureConversation() {
  if (state.id) return state.id;
  if (state.creating) return state.creating;
  const epoch = state.epoch;
  const creating = api('/api/conversations', {method:'POST'}).then(record => {
    if (state.epoch !== epoch) throw new Error('咨询已切换，请在当前咨询中重试。');
    state.id = record.id;
    try {localStorage.setItem('xiaochi-conversation', state.id);} catch (_) {}
    return state.id;
  }).finally(() => {if (state.creating === creating) state.creating = null;});
  state.creating = creating; return creating;
}
function resetConversation() {
  finishAnswerReveal();
  hideHistoryNotice();
  $('tendency-control').open = false;
  stopVoice();
  stopNewsPolling(); state.epoch++; state.creating = null; state.id = null; state.messages = []; state.attachments = []; state.answerDrafts = {}; state.caseState = null; syncTendency(); renderNews(null); localStorage.removeItem('xiaochi-conversation');
  $('question').value = ''; $('question').style.height = 'auto';
  setComposerCollapsed(false);
  showError(); renderAttachments(); renderMessages(); closeMenu();
}
$('new-chat').addEventListener('click', () => {
  if (state.busy) return;
  resetConversation();
  refreshHistory().catch(error => showError(error.message)); $('question').focus();
});
$('delete-cancel').addEventListener('click', () => $('delete-dialog').close());
$('delete-dialog').addEventListener('close', () => {pendingDelete = null;});
$('delete-confirm').addEventListener('click', async () => {
  if (state.busy || !pendingDelete) return;
  const item = pendingDelete; $('delete-dialog').close(); setBusy(true); showError();
  try {
    await api(`/api/conversations/${item.id}`, {method: 'DELETE'});
    if (state.id === item.id) resetConversation();
    showHistoryNotice(item);
    await refreshHistory();
  } catch (error) {showError(error.message);} finally {setBusy(false);}
});
$('undo-delete').addEventListener('click', async () => {
  if (state.busy || !lastDeleted) return;
  const item = lastDeleted;
  clearTimeout(historyNoticeTimer); historyNoticeTimer = null;
  let restored = false;
  setBusy(true); showError();
  try {
    await api(`/api/conversations/${item.id}/restore`, {method: 'POST'});
    restored = true;
    hideHistoryNotice(); await refreshHistory();
  } catch (error) {if (!restored) showHistoryNotice(item); showError(error.message);} finally {setBusy(false);}
});
$('menu-button').addEventListener('click', () => {
  const open = $('sidebar').classList.toggle('open'); $('scrim').classList.toggle('open', open);
  $('menu-button').setAttribute('aria-expanded', String(open));
});
$('scrim').addEventListener('click', closeMenu);
$('mic-button').addEventListener('click', toggleMicrophone);
$('attach-button').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', async () => {
  if (state.busy) return;
  const files = [...$('file-input').files]; $('file-input').value = '';
  if (files.length + state.attachments.length > 5) {showError('每次咨询最多上传 5 份材料。'); return;}
  if (files.some(file => file.size > 25 * 1024 * 1024)) {showError('单份材料不得超过 25 MB。'); return;}
  setBusy(true); showError();
  try {
    await ensureConversation();
    for (const file of files) {const body = new FormData(); body.append('file', file); state.attachments.push(await api(`/api/conversations/${state.id}/attachments`, {method:'POST', body})); renderAttachments();}
  } catch (error) {showError(error.message);} finally {setBusy(false);}
});
$('question').addEventListener('keydown', event => {if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {event.preventDefault(); $('chat-form').requestSubmit();}});
$('question').addEventListener('input', () => {updateActionAvailability(); resizeQuestion();});
$('chat-form').addEventListener('submit', event => {event.preventDefault(); submitConsultation();});
$('analyze-existing').addEventListener('click', () => submitConsultation('analyze_existing'));
async function submitConsultation(action = 'respond') {
  if (state.busy) return;
  stopVoice();
  const draft = $('question').value; const draftHeight = $('question').style.height;
  const text = draft.trim(); const answers = submittedAnswers();
  const tendency = state.tendencyDirty && $('tendency').value ? $('tendency').value : undefined;
  const tendencyNote = tendency ? $('tendency-note').value.trim() : undefined;
  if (!text && !state.attachments.length && !answers.length && !tendency && action !== 'analyze_existing') {$('question').focus(); return;}
  if (action === 'analyze_existing' && !text && !state.attachments.length && !state.messages.some(item => item.role === 'user')) {showError('请先提供需要分析的案例情况。'); return;}
  hideHistoryNotice(); closeMenu();
  let committed = false; let stopProgress = () => {};
  const previous = [...state.messages]; const files = [...state.attachments];
  const selectedMode = modePicker.value;
  const visible = [text];
  const questions = latestQuestions();
  for (const answer of answers) {
    const question = questions.find(item => item.id === answer.question_id);
    visible.push((question?.prompt || '补充信息') + '：' + (answer.skipped ? '暂不补充' : [answer.selected_option, answer.text].filter(Boolean).join('；')));
  }
  if (tendency) visible.push('初步处理倾向：' + {oppose:'倾向不予支持',support:'倾向予以支持',undecided:'尚未确定',custom:'其他考虑'}[tendency] + (tendencyNote ? '；' + tendencyNote : ''));
  if (action === 'analyze_existing') visible.push('请按现有信息分析，本轮不再追问；未提供的事实保留为未知。');
  setBusy(true); showError();
  $('question').value = ''; $('question').style.height = 'auto';
  state.messages.push({role:'user', content:visible.filter(Boolean).join('\n'), attachments:files.map(item => item.name)}); renderMessages(true);
  try {
    await ensureConversation();
    const requestId = crypto.randomUUID();
    stopProgress = watchProgress(state.id, requestId);
    const pending = api('/api/conversations/' + state.id + '/messages', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:text, attachments:files.map(item => item.id), request_id:requestId, mode:selectedMode, answers, tendency, tendency_note:tendencyNote, action})});
    startNewsPolling(true);
    const response = await pending;
    committed = true;
    state.caseState = response.case_state || state.caseState;
    state.messages.push({role:'assistant', content:response.answer, html:response.answer_html, clarification_questions:response.clarification_questions, citations:response.citations, mode:response.mode, elapsed_seconds:response.elapsed_seconds});
    state.answerDrafts = {}; state.attachments = []; syncTendency(); $('tendency-control').open = false; renderAttachments();
    renderMessages(false, {revealIndex:state.messages.length - 1, preserveScroll:true}); startNewsPolling(true);
    await refreshHistory();
  } catch (error) {
    if (!committed) {state.messages = previous; $('question').value = draft; $('question').style.height = draftHeight;}
    renderMessages(); showError(error.message);
  }
  finally {stopProgress(); setBusy(false); if (!state.composerCollapsed) $('question').focus({preventScroll:true});}
}
function useQuestion(text) {
  if (state.busy) return;
  setComposerCollapsed(false);
  $('question').value = text;
  $('question').dispatchEvent(new Event('input'));
  $('question').focus();
}
for (const button of document.querySelectorAll('[data-question]')) {
  button.addEventListener('click', () => useQuestion(button.dataset.question));
}
async function initialize() {
  try {
    const config = await api('/api/config'); agentName = config.name;
    const taskIcons = [
      '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5M8 8h5M8 11h3"/>',
      '<path d="M6 3h8l4 4v14H6V3Zm8 0v5h4M9 12h6M9 16h5"/>',
      '<path d="M12 4H5v16h15v-7M11 14l1-4 7-7 3 3-7 7-4 1Z"/>',
      '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h1m6 0h1M8 16h1m6 0h1"/>',
    ];
    const categories = config.business_categories?.length ? config.business_categories
      : [{title: '税收业务', questions: config.quick_questions}];
    function selectCategory(selected) {
      const category = categories[selected];
      for (const [index, button] of [...$('business-tabs').children].entries()) button.setAttribute('aria-pressed', String(index === selected));
      $('suggestions').replaceChildren(); $('suggestions').setAttribute('aria-label', `${category.title}事项`);
      category.questions.forEach((item, index) => {
        const button = document.createElement('button'); button.className = 'suggestion'; button.type = 'button'; button.disabled = state.busy;
        const icon = document.createElement('span'); icon.className = 'suggestion-icon'; icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = `<svg viewBox="0 0 24 24">${taskIcons[index % taskIcons.length]}</svg>`;
        const copy = document.createElement('span'); copy.className = 'suggestion-copy';
        const title = document.createElement('strong'); title.textContent = item.title; const caption = document.createElement('small'); caption.textContent = item.caption; copy.append(title, caption);
        button.append(icon, copy);
        button.addEventListener('click', () => useQuestion(item.text)); $('suggestions').append(button);
      });
    }
    categories.forEach((category, index) => {
      const button = document.createElement('button'); button.className = 'business-tab'; button.type = 'button';
      button.textContent = category.title; button.setAttribute('aria-pressed', 'false');
      button.addEventListener('click', () => {if (!state.busy) selectCategory(index);}); $('business-tabs').append(button);
    });
    selectCategory(0);
    await refreshHistory();
    const remembered = localStorage.getItem('xiaochi-conversation'); if (remembered) await selectConversation(remembered);
  } catch (error) {showError('暂时无法连接服务，请确认服务已启动后刷新页面。');}
}

function safeHref(value) {
  if (typeof value !== 'string' || !value || /[\u0000-\u0020\u007f\\]/.test(value)) return '';
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || (url.port && !['80','443'].includes(url.port))) return '';
    if (!host.includes('.') || host.includes(':') || /^\d+(?:\.\d+){3}$/.test(host) || host === 'localhost' || /\.(localhost|local|internal|lan|home|test|invalid)$/.test(host)) return '';
    return url.href;
  } catch (_) {return '';}
}
function linkNode(title, url) {
  const href = safeHref(url);
  const node = document.createElement(href ? 'a' : 'span'); node.textContent = title || '未注明标题';
  if (href) {node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer';}
  return node;
}
function detailLine(parent, label, value) {
  if (!value) return;
  const line = document.createElement('p'); const title = document.createElement('strong'); title.textContent = label + '：';
  line.append(title, document.createTextNode(String(value))); parent.append(line);
}
function renderCitations(citations) {
  const box = document.createElement('details'); box.className = 'citation-panel';
  const heading = document.createElement('summary'); heading.textContent = '政策引用与核验情况（' + citations.length + '）'; box.append(heading);
  const labels = {pending:'效力待核验',effective:'现行有效',not_yet_effective:'尚未施行',repealed:'已废止',expired:'已失效',amended:'已修改或替代'};
  for (const citation of citations) {
    const item = document.createElement('section'); item.className = 'citation-item';
    item.append(linkNode(citation.title || citation.document_number, citation.url));
    const status = document.createElement('span'); status.className = 'citation-status'; status.textContent = labels[citation.validity] || '效力待核验'; item.append(status);
    detailLine(item, '发布机关', citation.issuer); detailLine(item, '文号 / 条款', [citation.document_number, citation.article].filter(Boolean).join(' / '));
    detailLine(item, '发布日期', citation.published_at || '未明确'); detailLine(item, '施行日期', citation.effective_at || '未明确');
    detailLine(item, '原文取得情况', citation.source_status === 'original_read' ? '已取得原文，仍须核对具体适用条件' : '仅作检索线索，原文尚待核实');
    detailLine(item, '效力说明', citation.validity_note); detailLine(item, '最近核验', citation.checked_at || '尚未核验');
    if (citation.validity_evidence_url || citation.validity_evidence_excerpt) {
      const evidence = document.createElement('div'); evidence.className = 'validity-evidence';
      evidence.append(linkNode('效力核验依据', citation.validity_evidence_url));
      detailLine(evidence, '相关摘录', citation.validity_evidence_excerpt); item.append(evidence);
    }
    box.append(item);
  }
  return box;
}
function latestQuestions() {
  const last = state.messages[state.messages.length - 1];
  return last?.role === 'assistant' && Array.isArray(last.clarification_questions) ? last.clarification_questions.slice(0, 2) : [];
}
function submittedAnswers() {
  if (modePicker.value === 'rough') return [];
  return latestQuestions().flatMap(question => {
    const draft = state.answerDrafts[question.id];
    if (!draft || !(draft.skipped || draft.selected_option || draft.text?.trim())) return [];
    return [{question_id:question.id, selected_option:draft.skipped ? '' : draft.selected_option || '', text:draft.skipped ? '' : (draft.text || '').trim(), skipped:!!draft.skipped}];
  });
}
function renderClarifications(questions, active) {
  const panel = document.createElement('section'); panel.className = 'clarification-panel';
  const heading = document.createElement('h3'); heading.textContent = active ? '补充案例情况' : '本轮提出的待确认事项'; panel.append(heading);
  const note = document.createElement('p'); note.className = 'clarification-note';
  note.textContent = active ? '可选择、自由补充或跳过；点击提交后才会记入咨询。也可直接按现有信息分析。' : '历史问题仅供回查；请在最新一轮继续补充。';
  panel.append(note);
  for (const question of questions) {
    if (!active) {const text = document.createElement('p'); text.textContent = question.prompt; panel.append(text); continue;}
    const draft = state.answerDrafts[question.id] ||= {selected_option:'',text:'',skipped:false};
    const field = document.createElement('fieldset'); const legend = document.createElement('legend'); legend.textContent = question.prompt; field.append(legend);
    const choices = document.createElement('div'); choices.className = 'clarification-options';
    const optionButtons = [];
    for (const option of question.options || []) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'clarification-option'; button.textContent = option; button.disabled = state.busy;
      button.setAttribute('aria-pressed', String(draft.selected_option === option && !draft.skipped)); optionButtons.push(button);
      button.addEventListener('click', () => {
        if (state.busy) return;
        draft.selected_option = draft.selected_option === option ? '' : option; draft.skipped = false; sync();
      }); choices.append(button);
    }
    const input = document.createElement('input'); input.type = 'text'; input.className = 'clarification-free-text'; input.placeholder = '也可直接输入补充说明'; input.maxLength = 2000;
    input.setAttribute('aria-label', question.prompt + '：自由补充'); input.value = draft.text;
    input.addEventListener('input', () => {draft.text = input.value; draft.skipped = false; sync();});
    input.addEventListener('keydown', event => {if (event.key === 'Enter' && !event.isComposing) {event.preventDefault(); submitConsultation();}});
    const skip = document.createElement('button'); skip.type = 'button'; skip.className = 'clarification-skip';
    skip.addEventListener('click', () => {if (state.busy) return; draft.skipped = !draft.skipped; if (draft.skipped) {draft.selected_option = ''; draft.text = ''; input.value = '';} sync();});
    function sync() {
      optionButtons.forEach(button => button.setAttribute('aria-pressed', String(button.textContent === draft.selected_option && !draft.skipped)));
      skip.textContent = draft.skipped ? '已选择跳过 · 撤销' : '暂不补充此项'; skip.setAttribute('aria-pressed', String(draft.skipped));
      input.disabled = state.busy || draft.skipped; input.dataset.skipped = String(draft.skipped);
      updateComposerSummary();
    }
    sync(); field.append(choices, input, skip); panel.append(field);
  }
  if (active) {
    const submit = document.createElement('button'); submit.type = 'button'; submit.className = 'clarification-submit'; submit.textContent = '提交补充信息'; submit.disabled = state.busy;
    submit.addEventListener('click', () => submitConsultation()); panel.append(submit);
  }
  return panel;
}
function syncTendency() {
  const data = state.caseState;
  $('tendency').value = data?.tendency_provided ? data.tendency : '';
  $('tendency-note').value = data?.tendency_note || '';
  $('tendency-note').hidden = !$('tendency').value;
  state.tendencyDirty = false;
}
$('tendency').addEventListener('change', () => {state.tendencyDirty = true; $('tendency-note').hidden = !$('tendency').value;});
$('tendency-note').addEventListener('input', () => {state.tendencyDirty = true;});
let newsGeneration = 0, newsTimer = null, newsScopedId = null;
function stopNewsPolling() {newsGeneration++; clearTimeout(newsTimer); newsTimer = null;}
function policyDateRange(period, now = new Date()) {
  if (period === 'all') return {};
  const today = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit'}).format(now);
  const start = new Date(today + 'T00:00:00Z');
  if (period === 'week') start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
  else start.setUTCDate(1);
  return {date_from:start.toISOString().slice(0, 10), date_to:today};
}
function newsFilters() {
  return {category:$('news-category').value, domain:$('news-domain').value, region:$('news-region').value,
    ...policyDateRange($('news-period').value)};
}

function renderNews(data, error = '') {
  const items = data?.items || []; const status = data?.status || {};
  $('news-count').textContent = items.length; $('news-items').replaceChildren();
  const pieces = [];
  if (error) pieces.push(error);
  if (status.running) pieces.push('正在检索动态，不影响当前咨询。');
  if (status.error) pieces.push(typeof status.error === 'string' ? status.error : status.error.message || '本次更新未完成。');
  if (status.last_attempt) pieces.push('最近尝试：' + formatNewsTime(status.last_attempt));
  if (status.last_success) pieces.push('最近成功：' + formatNewsTime(status.last_success));
  if (!pieces.length) pieces.push(state.id ? '尚未检索，可手动刷新政策动态。' : '咨询后自动检索，也可手动刷新。');
  if (!items.length && status.last_success) pieces.push('当前筛选条件下暂无动态。日期筛选不含发布日期未明确的条目。');
  $('news-status').textContent = pieces.join('\n');
  const labels = {announcement:'政策发布',amendment:'修订与废止',upcoming:'即将施行（待核实）',interpretation:'政策解读',news:'新闻资讯',other:'其他'};
  for (const entry of items.slice(0, 20)) {
    const item = document.createElement('article'); item.className = 'news-item'; item.append(linkNode(entry.title, entry.url));
    const meta = document.createElement('p'); meta.className = 'news-meta';
    meta.textContent = (entry.published_at || '发布日期未明确') + ' · ' + (labels[entry.category] || '政策线索'); item.append(meta);
    const summary = document.createElement('p'); summary.className = 'news-summary'; summary.textContent = '系统归纳：' + (entry.summary || '暂无摘要'); item.append(summary);
    const note = document.createElement('small'); note.textContent = '来源及效力待核实；发布日期来自检索元数据。'; item.append(note);
    if (entry.effective_at) {
      const effective = document.createElement('p'); effective.className = 'news-meta';
      effective.textContent = '施行日期线索：' + entry.effective_at + '（待核实）'; item.append(effective);
      if (entry.effective_evidence_excerpt) {
        const evidence = document.createElement('details'); evidence.className = 'news-date-evidence';
        const heading = document.createElement('summary'); heading.textContent = '日期来源';
        const text = document.createElement('p'); text.textContent = entry.effective_evidence_excerpt;
        evidence.append(heading, text); item.append(evidence);
      }
    }
    const source = safeHref(entry.url);
    if (source) {
      const review = document.createElement('button'); review.type = 'button'; review.className = 'news-review';
      review.textContent = '核查与本案的关系'; review.disabled = state.busy;
      review.title = '仅填入输入框，不自动提交；需要逐条核验时请选择审慎研判。';
      review.addEventListener('click', () => {
        useQuestion('请结合当前案例，核查以下政策动态是否影响已有意见，并说明相关正式文件、适用条件与效力方面尚待核实的事项。\n'
          + (entry.title || '政策动态') + '\n' + source); closeMenu();
      });
      item.append(review);
    }
    $('news-items').append(item);
  }
}
function formatNewsTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未明确' : date.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false});
}
function startNewsPolling(waitForStart = false) {
  stopNewsPolling();
  const id = state.id, generation = newsGeneration;
  if (!id) {newsScopedId = null; renderNews(null); return;}
  if (newsScopedId !== id) {newsScopedId = id; renderNews(null);}
  let attempts = 0;
  const poll = async () => {
    if (generation !== newsGeneration || state.id !== id) return;
    try {
      const params = new URLSearchParams(newsFilters());
      const data = await api('/api/conversations/' + id + '/policy-news?' + params);
      if (generation !== newsGeneration || state.id !== id) return;
      renderNews(data); attempts++;
      if (data.status?.running || (waitForStart && attempts < 3)) newsTimer = setTimeout(poll, 1500);
      else if ($('news-card').open) newsTimer = setTimeout(poll, 60000);
    } catch (error) {
      if (generation === newsGeneration && state.id === id) renderNews(null, error.message);
    }
  };
  poll();
}
$('news-card').addEventListener('toggle', () => {if ($('news-card').open) startNewsPolling();});
for (const id of ['news-category','news-period','news-domain','news-region']) $(id).addEventListener('change', () => startNewsPolling());
for (const region of ['北京','天津','河北','山西','内蒙古','辽宁','吉林','黑龙江','上海','江苏','浙江','安徽','福建','江西','山东','河南','湖北','湖南','广东','广西','海南','重庆','四川','贵州','云南','西藏','陕西','甘肃','青海','宁夏','新疆','香港','澳门','台湾']) {
  const option = document.createElement('option'); option.value = region; option.textContent = region; $('news-region').append(option);
}
$('news-refresh').addEventListener('click', async () => {
  if (state.busy) return;
  const button = $('news-refresh'); button.disabled = true;
  let id, generation; const epoch = state.epoch;
  try {
    id = await ensureConversation(); if (epoch !== state.epoch) return; stopNewsPolling(); generation = newsGeneration;
    const latest = [...state.messages].reverse().find(item => item.role === 'user')?.content || '';
    const filters = newsFilters();
    await api('/api/conversations/' + id + '/policy-news/refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:latest, category:filters.category, domain:filters.domain, region:filters.region})});
    if (state.id === id && generation === newsGeneration) startNewsPolling(true);
  } catch (error) {
    if (epoch === state.epoch && (!id || (state.id === id && generation === newsGeneration))) renderNews(null, error.message);
  } finally {button.disabled = state.busy;}
});

// Pause decorative sprite work when this page is in the background.
function syncMascotVisibility() {
  $('brand-mascot').dataset.paused = String(document.hidden);
}
document.addEventListener('visibilitychange', syncMascotVisibility);
syncMascotVisibility();
initialize();
