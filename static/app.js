const $ = id => document.getElementById(id);
const state = { documents: [], chats: [], active: null, selected: null, busy: false, uploading: false, configured: false, deleteAction: null, toastTimer: null, loading: false };
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
async function api(path, method = 'GET', body) {
  const response = await fetch('/api' + path, { method, headers: body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check your input and try again.');
  return data;
}
function toast(text) { clearTimeout(state.toastTimer); $('toast').textContent = text; $('toast').hidden = false; state.toastTimer = setTimeout(() => $('toast').hidden = true, 4500); }
function showError(text) { $('chatError').textContent = text; $('chatError').hidden = !text; }
function scrollBottom() { $('conversation').scrollTo({ top: $('conversation').scrollHeight, behavior: 'smooth' }); }
function updateScope() {
  const count = state.selected === null ? state.documents.length : state.selected.size;
  $('contextCount').textContent = `${count} document${count === 1 ? '' : 's'}`;
  $('scopeLabel').textContent = state.selected === null ? 'YOUR PDF LIBRARY' : 'SELECTED DOCUMENTS';
  $('sendButton').disabled = state.busy || state.uploading || state.loading || count === 0 || !$('question').value.trim();
}
async function refreshStatus() {
  const status = await api('/status'); state.configured = status.configured;
  $('keyDot').classList.toggle('ready', status.configured);
  $('settingsStatus').textContent = status.configured ? '● Connection configured' : 'No API key configured';
}
function dateGroup(timestamp) {
  const date = new Date(timestamp), now = new Date();
  if (date.toDateString() === now.toDateString()) return 'Today';
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
async function refreshChats() {
  state.chats = await api('/chats'); $('chatCount').textContent = state.chats.length;
  const list = $('chatList'); list.replaceChildren();
  if (!state.chats.length) { list.append(node('p', 'Your next discovery starts with a question. Conversations will be saved here.', 'chat-empty')); return; }
  let group = '';
  for (const chat of state.chats) {
    const label = dateGroup(chat.updated_at);
    if (label !== group) { list.append(node('div', label, 'chat-group')); group = label; }
    const row = node('div', undefined, 'chat-row' + (chat.id === state.active ? ' active' : ''));
    const open = node('button', undefined, 'chat-open'); open.title = chat.title; open.setAttribute('aria-label', 'Open conversation: ' + chat.title);
    open.append(node('span', '◌', 'chat-icon'), node('span', chat.title, 'title')); open.disabled = state.busy;
    open.onclick = () => openChat(chat.id);
    const remove = node('button', '⌫', 'delete-chat'); remove.setAttribute('aria-label', 'Delete conversation: ' + chat.title); remove.title = 'Delete conversation'; remove.disabled = state.busy;
    remove.onclick = () => confirmDelete('Delete conversation?', `“${chat.title}” and all its messages will be permanently removed. Your PDFs will stay in the library.`, async () => {
      await api('/chats/' + chat.id, 'DELETE');
      if (state.active === chat.id) resetConversation();
      await refreshChats(); toast('Conversation deleted');
    });
    row.append(open, remove); list.append(row);
  }
}
function resetConversation() {
  state.active = null; $('messages').replaceChildren(); $('welcome').hidden = false; $('chatTitle').textContent = 'New conversation';
  $('question').value = ''; $('question').style.height = ''; renderSources([]); showError(''); updateScope();
}
async function openChat(id) {
  if (state.busy || state.loading) return;
  state.loading = true; updateScope();
  try {
    const chat = await api('/chats/' + id); state.active = id; $('messages').replaceChildren(); $('welcome').hidden = chat.messages.length > 0;
    $('chatTitle').textContent = chat.title;
    let latest = [];
    for (const message of chat.messages) { renderMessage(message); if (message.role === 'assistant') latest = message.sources; }
    renderSources(latest); showError(''); await refreshChats(); $('sidebar').classList.remove('open'); scrollBottom();
  } catch (error) { showError(error.message); } finally { state.loading = false; updateScope(); }
}
function renderSources(sources, selectedCitation) {
  $('sourceCount').textContent = sources.length; const list = $('sourceList'); list.replaceChildren();
  if (!sources.length) {
    const empty = node('div', undefined, 'source-empty'); empty.append(node('span', '⌁'), node('p', 'A little less “trust me.”\nA little more “here’s the page.”'), node('small', 'Cited passages appear here as you chat.')); list.append(empty); return;
  }
  for (const source of sources) {
    const card = node('article', undefined, 'source-card' + (source.citation === selectedCitation ? ' selected' : ''));
    card.id = 'source-' + source.citation;
    const head = node('div', undefined, 'source-card-head');
    head.append(node('span', '[' + source.citation + ']', 'source-number'), node('span', source.name, 'source-name'), node('span', 'p. ' + source.page, 'source-page'));
    const link = node('a', 'Open original PDF ↗'); link.href = `/api/documents/${source.document_id}/pdf#page=${source.page}`; link.target = '_blank'; link.rel = 'noopener';
    card.append(head, node('blockquote', source.text), link); list.append(card);
  }
  if (selectedCitation) {
    $('library').classList.add('open');
    setTimeout(() => $('source-' + selectedCitation)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 50);
  }
}
function renderMessage(message, temporary = false) {
  const article = node('article', undefined, 'message message-' + message.role); if (temporary) article.dataset.temporary = 'true';
  const content = node('div', undefined, 'message-content');
  if (message.role === 'user') content.textContent = message.content;
  else {
    const meta = node('div', undefined, 'message-meta'); meta.append(node('span', '✳', 'assistant-icon'), node('strong', 'Knowledge Desk'), node('small', message.sources?.length ? 'GROQ · PDF GROUNDED' : 'WORKSPACE ASSISTANT')); article.append(meta);
    const sources = message.sources || [];
    const parts = message.content.split(/(\[\d+\])/g);
    for (const part of parts) {
      if (/^\[\d+\]$/.test(part) && sources.some(s => s.citation === Number(part.slice(1, -1)))) {
        const button = node('button', part, 'citation'); button.setAttribute('aria-label', 'View source ' + part); button.onclick = () => renderSources(sources, Number(part.slice(1, -1))); content.append(button);
      } else content.append(document.createTextNode(part));
    }
    article.append(content);
    const chips = node('div', undefined, 'message-sources');
    for (const source of sources) { const chip = node('button', `${source.name} · p. ${source.page}`, 'source-pill'); chip.title = source.name; chip.onclick = () => renderSources(sources, source.citation); chips.append(chip); }
    if (sources.length) article.append(chips);
    const actions = node('div', undefined, 'answer-actions'), copy = node('button', '▢ Copy answer');
    copy.onclick = async () => { try { await navigator.clipboard.writeText(message.content); toast('Answer copied'); } catch { toast('Copy is unavailable in this browser'); } };
    actions.append(copy); article.append(actions);
  }
  if (message.role === 'user') article.append(content);
  $('messages').append(article); return article;
}
async function refreshDocuments() {
  state.documents = await api('/documents');
  if (state.selected !== null) { const ids = new Set(state.documents.map(d => d.id)); state.selected = new Set([...state.selected].filter(id => ids.has(id))); }
  $('documentCounter').textContent = `${state.documents.length} / 20`; $('libraryCount').textContent = state.documents.length;
  $('miniDocuments').textContent = state.documents.length ? `${state.documents.length} PDF${state.documents.length === 1 ? '' : 's'} in your library` : 'Your library is empty';
  const list = $('documentList'); list.replaceChildren();
  if (!state.documents.length) list.append(node('p', 'A fresh page. Add a PDF to begin.', 'document-empty'));
  for (const document of state.documents) {
    const row = node('div', undefined, 'document-row');
    const check = node('input'); check.type = 'checkbox'; check.checked = state.selected === null || state.selected.has(document.id); check.setAttribute('aria-label', 'Use document ' + document.name);
    check.onchange = () => {
      if (state.selected === null) state.selected = new Set(state.documents.map(d => d.id));
      if (check.checked) state.selected.add(document.id); else state.selected.delete(document.id);
      updateScope();
    };
    const info = node('div', undefined, 'document-info'); info.append(node('strong', document.name), node('small', `${document.pages} pages · ${(document.bytes / 1024).toFixed(0)} KB · ${document.limited_text ? 'limited text — OCR recommended' : 'indexed'}`)); info.title = document.limited_text ? `${document.name}: little readable text was extracted. Run OCR for better answers.` : document.name;
    if (document.limited_text) info.classList.add('limited-text');
    const remove = node('button', 'Delete', 'document-delete'); remove.setAttribute('aria-label', 'Delete PDF ' + document.name); remove.title = 'Delete PDF from library'; remove.disabled = state.busy || state.uploading;
    remove.onclick = () => confirmDelete('Remove this PDF?', `“${document.name}” will be removed from your library and future searches. Existing chat messages and their saved excerpts will remain.`, async () => { await api('/documents/' + document.id, 'DELETE'); await refreshDocuments(); toast('Document removed'); });
    row.append(check, node('span', 'PDF', 'pdf-icon'), info, remove); list.append(row);
  }
  updateScope();
}
async function uploadFiles(files) {
  if (state.uploading || state.busy || !files.length) return;
  if (files.length > 20) { toast('Choose up to 20 PDFs per upload'); return; }
  if (files.some(file => !file.name.toLowerCase().endsWith('.pdf'))) { toast('Please choose PDF files only'); return; }
  if (files.some(file => file.size > 20 * 1024 * 1024)) { toast('Each PDF must be 20 MB or smaller'); return; }
  if (files.reduce((size, file) => size + file.size, 0) > 100 * 1024 * 1024) { toast('Combined upload size must be 100 MB or smaller'); return; }
  state.uploading = true; $('uploadProgress').hidden = false; $('dropzone').disabled = true; updateScope();
  const form = new FormData(); for (const file of files) form.append('files', file);
  try { const result = await api('/documents', 'POST', form); state.selected = null; await refreshDocuments(); toast(`${result.imported.length} PDF${result.imported.length === 1 ? '' : 's'} added${result.duplicates.length ? ` · ${result.duplicates.length} duplicate${result.duplicates.length === 1 ? '' : 's'} skipped` : ''}`); showError(''); }
  catch (error) { toast(error.message); }
  finally { state.uploading = false; $('uploadProgress').hidden = true; $('dropzone').disabled = false; $('fileInput').value = ''; updateScope(); }
}
function confirmDelete(title, description, action) { state.deleteAction = action; $('deleteTitle').textContent = title; $('deleteDescription').textContent = description; $('deleteDialog').showModal(); }
$('confirmDelete').onclick = async () => {
  $('confirmDelete').disabled = true;
  try { await state.deleteAction?.(); $('deleteDialog').close(); }
  catch (error) { toast(error.message); } finally { $('confirmDelete').disabled = false; }
};
document.querySelectorAll('[data-close]').forEach(button => button.onclick = () => $(button.dataset.close).close());
$('settingsButton').onclick = async () => { await refreshStatus(); $('settingsError').hidden = true; $('settingsDialog').showModal(); };
$('settingsDialog').addEventListener('close', () => { $('apiKey').value = ''; $('apiKey').type = 'password'; $('revealKey').textContent = 'Show'; });
$('revealKey').onclick = () => { const visible = $('apiKey').type === 'text'; $('apiKey').type = visible ? 'password' : 'text'; $('revealKey').textContent = visible ? 'Show' : 'Hide'; };
$('settingsForm').onsubmit = async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector('[type=submit]'); button.disabled = true;
  try { await api('/settings', 'PUT', { api_key: $('apiKey').value.trim() }); await refreshStatus(); $('settingsDialog').close(); toast('Groq connection saved for this server session'); showError(''); }
  catch (error) { $('settingsError').textContent = error.message; $('settingsError').hidden = false; } finally { button.disabled = false; }
};
function chooseFiles() { if (state.uploading || state.busy) return; $('fileInput').click(); }
for (const id of ['dropzone', 'welcomeUpload', 'composerUpload']) $(id).onclick = chooseFiles;
$('fileInput').onchange = event => uploadFiles([...event.target.files]);
$('dropzone').ondragover = event => { event.preventDefault(); $('dropzone').classList.add('dragging'); };
$('dropzone').ondragleave = () => $('dropzone').classList.remove('dragging');
$('dropzone').ondrop = event => { event.preventDefault(); $('dropzone').classList.remove('dragging'); uploadFiles([...event.dataTransfer.files]); };
$('selectAll').onclick = () => { state.selected = null; refreshDocuments().catch(error => toast(error.message)); };
$('newChat').onclick = () => { if (state.busy) return; resetConversation(); refreshChats(); $('sidebar').classList.remove('open'); $('question').focus(); };
$('menuToggle').onclick = () => $('sidebar').classList.toggle('open');
$('libraryToggle').onclick = () => $('library').classList.toggle('open');
$('closeLibrary').onclick = () => $('library').classList.remove('open');
document.addEventListener('click', event => { if (window.innerWidth < 650 && $('sidebar').classList.contains('open') && !$('sidebar').contains(event.target) && !$('menuToggle').contains(event.target)) $('sidebar').classList.remove('open'); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') { $('sidebar').classList.remove('open'); $('library').classList.remove('open'); } });
document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => { $('question').value = button.dataset.prompt; $('question').dispatchEvent(new Event('input')); $('question').focus(); });
$('question').oninput = () => { $('question').style.height = 'auto'; $('question').style.height = Math.min($('question').scrollHeight, 130) + 'px'; updateScope(); };
$('question').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$('sendButton').disabled) $('chatForm').requestSubmit(); } };
$('chatForm').onsubmit = async event => {
  event.preventDefault(); if (state.busy || state.uploading || state.loading) return;
  const question = $('question').value.trim(); if (!question) return;
  if (!state.documents.length) { toast('Add a PDF to start the conversation'); return; }
  if (state.selected !== null && state.selected.size === 0) { toast('Select at least one document, or choose Use all'); return; }
  state.busy = true; updateScope(); showError(''); $('newChat').disabled = true;
  let temporary, thinking;
  try {
    if (!state.active) { const chat = await api('/chats', 'POST'); state.active = chat.id; }
    await refreshChats(); $('welcome').hidden = true;
    temporary = renderMessage({ role: 'user', content: question }, true);
    thinking = node('article', undefined, 'message message-assistant');
    const meta = node('div', undefined, 'message-meta'); meta.append(node('span', '✳', 'assistant-icon'), node('strong', 'Reading your documents'));
    const dots = node('div', undefined, 'thinking'); dots.append(node('i'), node('i'), node('i'), node('span', 'Finding the right passages…')); thinking.append(meta, dots); $('messages').append(thinking); scrollBottom();
    const response = await api('/chats/' + state.active + '/messages', 'POST', { question, document_ids: state.selected === null ? [] : [...state.selected] });
    thinking.remove(); thinking = null; delete temporary.dataset.temporary;
    renderMessage(response); renderSources(response.sources); $('question').value = ''; $('question').style.height = '';
    const conversation = await api('/chats/' + state.active); $('chatTitle').textContent = conversation.title; scrollBottom();
  } catch (error) { thinking?.remove(); temporary?.remove(); $('welcome').hidden = $('messages').children.length > 0; showError(error.message); }
  finally { state.busy = false; $('newChat').disabled = false; updateScope(); await refreshChats(); $('question').focus(); }
};
async function initialize() { try { await Promise.all([refreshDocuments(), refreshChats(), refreshStatus()]); } catch (error) { showError('Could not load your workspace. ' + error.message); } updateScope(); }
initialize();
