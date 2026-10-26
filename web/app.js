'use strict';

const $ = id => document.getElementById(id);
const state = { me: null, people: [], selected: null, isNew: false, busy: false, version: null, since: 0, leftEvent: false };

const clean = v => String(v ?? '').trim();
const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const initials = name => clean(name).split(/\s+/).filter(Boolean).map(w => w[0]).filter((_, i, a) => i === 0 || i === a.length - 1).join('').toUpperCase();
const formatDate = iso => iso ? new Date(iso).toLocaleString('pt-PT', { dateStyle: 'short', timeStyle: 'short' }) : '';

/** GET quando não há corpo; POST JSON, ou binário quando o corpo é um ArrayBuffer. */
async function api(path, body, headers = {}) {
  const opts = {};
  if (body !== undefined) {
    const raw = body instanceof ArrayBuffer;
    opts.method = 'POST';
    opts.headers = { 'Content-Type': raw ? 'application/octet-stream' : 'application/json', ...headers };
    opts.body = raw ? body : JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || 'Erro desconhecido'), { status: res.status });
  return data;
}

function say(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle('ok', !!text && !isError);
  el.classList.toggle('error', !!text && isError);
}
const notify = (text, isError) => say($('status'), text, isError);

async function withBusy(button, fn) {
  button.disabled = true;
  try { await fn(); } finally { button.disabled = false; }
}

/* ── Ecrãs ───────────────────────────────────────────────────────────────── */

const SCREENS = ['login', 'picker', 'manage', 'app'];
function show(screen) {
  SCREENS.forEach(id => { $(id).hidden = id !== screen; });
  if (screen !== 'app') document.title = 'marcAsério | Check-in';
}

async function boot() {
  try {
    const s = await api('/api/session');
    state.me = s.me;
    if (!s.me) return showLogin(s);
    if (s.event) return showApp(s.event);
    await showPicker();
  } catch (e) {
    show('login');
    say($('loginStatus'), e.message, true);
  }
}

async function logout() {
  await api('/api/logout', {}).catch(() => {});
  state.me = null;
  boot();
}

/* ── Entrar ──────────────────────────────────────────────────────────────── */

function showLogin(s) {
  show('login');
  state.setup = s.setup_needed;
  $('googleLogin').hidden = !s.google || s.setup_needed;
  $('loginDivider').hidden = !s.google || s.setup_needed;
  $('googleLabel').textContent = `Entrar com Google (@${s.domain})`;
  $('loginTitle').textContent = s.setup_needed ? 'Primeira configuração' : 'Entrar';
  $('loginIntro').textContent = s.setup_needed
    ? 'Cria a conta de administração com o teu email e uma palavra-passe.'
    : 'Entra com a tua conta para fazer check-ins.';
  $('loginPassword2').hidden = $('loginPassword2Label').hidden = !s.setup_needed;
  $('loginPassword2').required = s.setup_needed;
  $('loginPassword').autocomplete = s.setup_needed ? 'new-password' : 'current-password';
  $('loginButton').textContent = s.setup_needed ? 'Criar conta de administração' : 'Entrar';
  const error = new URLSearchParams(location.search).get('erro');
  if (error) {
    say($('loginStatus'), error, true);
    history.replaceState(null, '', '/');
  }
}

$('loginForm').onsubmit = async e => {
  e.preventDefault();
  const body = { email: $('loginEmail').value, password: $('loginPassword').value };
  if (state.setup && body.password !== $('loginPassword2').value) {
    return say($('loginStatus'), 'As palavras-passe não coincidem.', true);
  }
  await withBusy($('loginButton'), async () => {
    try {
      await api(state.setup ? '/api/setup' : '/api/login', body);
      $('loginForm').reset();
      say($('loginStatus'), '');
      boot();
    } catch (err) { say($('loginStatus'), err.message, true); }
  });
};

/* ── Escolher o evento ───────────────────────────────────────────────────── */

async function showPicker() {
  show('picker');
  $('pickerUser').textContent = state.me.email;
  $('pickerRole').textContent = state.me.role_label;
  $('openManage').hidden = !state.me.can_manage;
  const { events } = await api('/api/my-events');
  // Uma hospedeira com um só evento entra diretamente.
  if (!state.me.can_manage && events.length === 1 && !state.leftEvent) return enterEvent(events[0].id);
  $('eventList').innerHTML = events.map(e => `
    <button type="button" class="event-choice" data-event="${escapeHTML(e.id)}">
      <b>${escapeHTML(e.name)}</b><small>${escapeHTML(e.event_date)}</small>
    </button>`).join('')
    || `<p class="empty muted small">${state.me.can_manage
      ? 'Ainda não existem eventos. Cria o primeiro em Gestão.'
      : 'Ainda não tens nenhum evento atribuído. Fala com quem te convidou.'}</p>`;
}

async function enterEvent(eventId) {
  try {
    const { event } = await api('/api/select-event', { event_id: eventId });
    showApp(event);
  } catch (err) { say($('pickerStatus'), err.message, true); }
}

$('eventList').onclick = e => {
  const choice = e.target.closest('[data-event]');
  if (choice) enterEvent(choice.dataset.event);
};
$('openManage').onclick = () => showManage();
$('logoutPicker').onclick = logout;

/* ── Gestão ──────────────────────────────────────────────────────────────── */

const manageSay = (text, isError) => say($('manageStatus'), text, isError);
const ROLE_HELP = {
  admin: 'Tudo: utilizadores, eventos, listas e registos.',
  editor: 'Eventos, listas e hospedeiras. Entra em todos os eventos.',
  hostess: 'Só faz check-ins nos eventos escolhidos abaixo.',
};

async function showManage(tab = 'tabEvents') {
  show('manage');
  manageSay('');
  $('manageUser').textContent = `${state.me.email} · ${state.me.role_label}`;
  $('logsTab').hidden = !state.me.is_admin;
  openTab(tab);
}

function openTab(tab) {
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tab-panel').forEach(p => { p.hidden = p.id !== tab; });
  ({ tabEvents: loadManageEvents, tabUsers: loadUsers, tabLogs: loadLogs })[tab]()
    .catch(err => manageSay(err.message, true));
}

document.querySelectorAll('.tab').forEach(b => { b.onclick = () => { manageSay(''); openTab(b.dataset.tab); }; });
$('manageBack').onclick = () => { state.leftEvent = true; showPicker(); };
$('logoutManage').onclick = logout;

async function loadManageEvents() {
  const { events } = await api('/api/manage/events');
  state.events = events;
  $('manageEvents').innerHTML = events.map(e => `
    <div class="row-item">
      <div class="info">
        <b>${escapeHTML(e.name)}</b>
        <div class="meta">${escapeHTML(e.event_date || 'Sem data')} · ${e.checked}/${e.total} check-ins</div>
      </div>
      <button type="button" class="btn small" data-open="${escapeHTML(e.id)}">Abrir</button>
    </div>`).join('') || '<p class="muted small">Ainda não existem eventos.</p>';
}

$('manageEvents').onclick = e => {
  const button = e.target.closest('[data-open]');
  if (button) enterEvent(button.dataset.open);
};

$('createEvent').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('/api/manage/events/create', { name: $('eventName').value, event_date: $('eventDate').value });
    $('createEvent').reset();
    await loadManageEvents();
    manageSay('Evento criado.');
  } catch (err) { manageSay(err.message, true); }
};

async function loadUsers() {
  const [{ users, roles }, { events }] = await Promise.all([api('/api/manage/users'), api('/api/manage/events')]);
  state.users = users;
  state.events = events;
  $('userRole').innerHTML = Object.entries(roles)
    .filter(([role]) => state.me.is_admin || role === 'hostess')
    .map(([role, label]) => `<option value="${role}">${label}</option>`).join('');
  $('usersNote').textContent = state.me.is_admin
    ? 'As contas @marcaserio.pt que entram com o Google ficam automaticamente como editores.'
    : 'Como editor, podes convidar e gerir hospedeiras.';
  renderUsers();
  resetUserForm();
}

function renderUsers() {
  const q = clean($('userSearch').value).toLowerCase();
  const editable = u => state.me.is_admin || u.role === 'hostess';
  $('userList').innerHTML = state.users.filter(u => u.email.includes(q)).map(u => `
    <div class="row-item">
      <div class="info">
        <b>${escapeHTML(u.email)}</b> <span class="tag ${u.role}">${escapeHTML(u.role_label)}</span>
        <div class="meta">${[
          u.role === 'hostess' ? escapeHTML(u.event_names.join(', ') || 'sem eventos') : 'todos os eventos',
          [u.google && 'Google', u.has_password && 'palavra-passe'].filter(Boolean).join(' + ') || 'sem forma de entrar',
          u.last_login ? 'última entrada ' + escapeHTML(formatDate(u.last_login)) : 'nunca entrou',
        ].join(' · ')}</div>
      </div>
      ${editable(u) ? `<div class="row">
        <button type="button" class="btn small" data-edit="${escapeHTML(u.email)}">Editar</button>
        ${u.email === state.me.email ? '' : `<button type="button" class="btn small danger" data-remove="${escapeHTML(u.email)}">Remover</button>`}
      </div>` : ''}
    </div>`).join('') || '<p class="muted small">Sem resultados.</p>';
}

function resetUserForm(user) {
  state.editingUser = user || null;
  $('userForm').reset();
  $('userEmail').value = user ? user.email : '';
  $('userEmail').readOnly = !!user;
  $('userRole').value = user ? user.role : 'hostess';
  $('userFormTitle').textContent = user ? 'Editar utilizador' : 'Convidar utilizador';
  $('userSave').textContent = user ? 'Guardar alterações' : 'Criar utilizador';
  $('userFormReset').hidden = !user;
  $('userEvents').innerHTML = (state.events || []).map(e => `
    <label class="check"><input type="checkbox" value="${escapeHTML(e.id)}" ${user && user.events.includes(e.id) ? 'checked' : ''}>
      ${escapeHTML(e.name)} <small>${escapeHTML(e.event_date)}</small></label>`).join('')
    || '<p class="muted small">Ainda não existem eventos.</p>';
  syncUserForm();
}

function syncUserForm() {
  const role = $('userRole').value, editing = state.editingUser;
  $('roleHelp').textContent = ROLE_HELP[role] || '';
  $('userEventsBox').hidden = role !== 'hostess';
  $('userPasswordLabel').textContent = editing ? 'Nova palavra-passe (opcional)' : 'Palavra-passe';
  $('passwordHelp').textContent = editing
    ? 'Se a mudares, as sessões abertas desta pessoa terminam. Envia-lhe a nova palavra-passe.'
    : 'Envia o email e a palavra-passe à pessoa. Pode entrar logo a seguir.';
}

$('userRole').onchange = syncUserForm;
$('userSearch').oninput = renderUsers;
$('userFormReset').onclick = () => resetUserForm();

$('userList').onclick = async e => {
  const edit = e.target.closest('[data-edit]'), remove = e.target.closest('[data-remove]');
  if (edit) {
    resetUserForm(state.users.find(u => u.email === edit.dataset.edit));
    $('userForm').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else if (remove && confirm(`Remover ${remove.dataset.remove}? Deixa de conseguir entrar.`)) {
    try {
      const d = await api('/api/manage/users/delete', { email: remove.dataset.remove });
      await loadUsers();
      manageSay(d.message);
    } catch (err) { manageSay(err.message, true); }
  }
};

$('userForm').onsubmit = async e => {
  e.preventDefault();
  const body = {
    email: $('userEmail').value,
    role: $('userRole').value,
    password: $('userPassword').value,
    events: [...$('userEvents').querySelectorAll('input:checked')].map(i => i.value),
  };
  await withBusy($('userSave'), async () => {
    try {
      const d = await api('/api/manage/users/save', body);
      await loadUsers();
      manageSay(d.message);
    } catch (err) { manageSay(err.message, true); }
  });
};

async function loadLogs() {
  const { logs, limit } = await api('/api/manage/logs');
  state.logs = logs;
  $('logsNote').textContent = `Os ${limit} registos mais recentes. Ficam guardados durante 12 meses.`;
  renderLogs();
}

function renderLogs() {
  const q = clean($('logSearch').value).toLocaleLowerCase('pt');
  const rows = state.logs.filter(l => [l.actor, l.action, l.event_name, l.detail].join(' ').toLocaleLowerCase('pt').includes(q));
  $('logRows').innerHTML = rows.map(l => `
    <tr class="${/falhad|Erro/.test(l.action) ? 'warn' : ''}">
      <td class="nowrap">${escapeHTML(formatDate(l.at))}</td>
      <td>${escapeHTML(l.actor || '—')}</td>
      <td class="nowrap"><b>${escapeHTML(l.action)}</b></td>
      <td>${escapeHTML(l.event_name)}</td>
      <td>${escapeHTML(l.detail)}${l.ip ? ` <small class="muted">${l.detail ? '· ' : ''}${escapeHTML(l.ip)}</small>` : ''}</td>
    </tr>`).join('') || '<tr><td colspan="5" class="muted">Sem registos.</td></tr>';
}

$('logSearch').oninput = renderLogs;
$('logsReload').onclick = () => loadLogs().catch(err => manageSay(err.message, true));

/* ── Evento ──────────────────────────────────────────────────────────────── */

function showApp(event) {
  show('app');
  $('eventTitle').textContent = event.name;
  $('logoutApp').title = state.me ? state.me.email : '';
  document.title = `${event.name} | marcAsério Check-in`;
  state.selected = null;
  state.isNew = false;
  state.version = null;
  refresh().catch(e => notify(e.message, true));
}

$('leaveEvent').onclick = async () => {
  await api('/api/leave-event', {}).catch(() => {});
  state.leftEvent = true;
  showPicker().catch(err => say($('pickerStatus'), err.message, true));
};
$('logoutApp').onclick = logout;

const currentPerson = () => state.people.find(p => p.id === state.selected);
const formValues = () => ({ name: clean($('name').value), role: clean($('role').value), company: clean($('company').value) });

const byName = (a, b) => a.name.localeCompare(b.name, 'pt', { sensitivity: 'base' }) || (a.id < b.id ? -1 : 1);

/** Atualiza lista, contadores e impressora sem tocar no formulário.
 *  Depois da primeira vez, só pede o que mudou (online, poupa leituras ao Firestore). */
async function loadPeople() {
  const query = state.version ? `?v=${encodeURIComponent(state.version)}&since=${state.since}` : '';
  const d = await api('/api/people' + query);
  if (d.full) {
    state.people = d.people.sort(byName);
  } else if (d.people.length) {
    const changed = new Set(d.people.map(p => p.id));
    state.people = state.people.filter(p => !changed.has(p.id)).concat(d.people).sort(byName);
  }
  state.version = d.version;
  state.since = d.now - 30;  // margem para escritas de outros contentores ainda a caminho

  const total = state.people.length, checked = state.people.filter(p => p.checked_at).length;
  const pct = total ? Math.round(checked / total * 100) : 0;
  $('total').textContent = total;
  $('checked').textContent = checked;
  $('pending').textContent = total - checked;
  $('percent').textContent = total ? `· ${pct}%` : '';
  $('progress').style.width = pct + '%';
  showPrinter(d.printer);
  // Hospedeiras: só exportar; importar e substituir listas é para editores e administradores.
  document.querySelectorAll('.editor-only').forEach(el => { el.hidden = !d.can_import; });
  $('importSummary').textContent = d.can_import ? 'Importar inscrições' : 'Exportar';
  state.canImport = d.can_import;
  return total;
}

/** Modo online: o estado vem do agente de impressão no local do evento. */
function showPrinter(p) {
  if (!p || p.mode === 'local') return;
  const queued = p.pending ? ` · ${p.pending} em fila` : '';
  $('printer').classList.toggle('off', !p.online || !!p.error);
  $('printer').title = p.error || '';
  $('printerText').textContent = !p.online ? 'Impressora desligada' + queued
    : p.error ? 'Erro na impressora: ' + p.error
    : 'Impressora ligada' + queued;
}

async function refresh() {
  if (!await loadPeople() && state.canImport) $('importBox').open = true;

  if (currentPerson()) select(state.selected);
  else if (!state.isNew && state.people.length) select(state.people[0].id);
  else newPerson();
}

function render() {
  const q = clean($('search').value).toLocaleLowerCase('pt');
  const filter = $('filter').value;
  const shown = state.people.filter(p =>
    (filter === 'all' || (filter === 'checked') === !!p.checked_at)
    && [p.name, p.role, p.company].join(' ').toLocaleLowerCase('pt').includes(q));

  $('count').textContent = shown.length === state.people.length ? `${shown.length} participantes` : `${shown.length} de ${state.people.length}`;
  $('list').innerHTML = shown.map(p => `
    <div class="person ${p.checked_at ? 'checked' : ''} ${state.selected === p.id ? 'active' : ''}" data-id="${escapeHTML(p.id)}">
      <span class="avatar">${escapeHTML(initials(p.name))}</span>
      <div class="info">
        <div class="name">${escapeHTML(p.name)}</div>
        <div class="meta">${escapeHTML([p.role, p.company].filter(Boolean).join(' · ')) || '&nbsp;'}</div>
      </div>
      <span class="tag ${p.checked_at ? 'ok' : ''}">${p.checked_at ? '✓ Entrou' : 'Pendente'}</span>
    </div>`).join('')
    || `<div class="empty">${state.people.length ? 'Sem resultados.'
      : state.canImport ? 'Ainda não existem participantes. Importa uma lista abaixo.' : 'Ainda não existem participantes.'}</div>`;
}

function fillEditor(p) {
  $('name').value = p?.name ?? '';
  $('role').value = p?.role ?? '';
  $('company').value = p?.company ?? '';
  $('editTitle').textContent = p ? 'Participante' : 'Novo participante (walk-in)';
  notify('');
  preview();
  showPersonState();
  render();
}

/** Estado de check-in do participante selecionado (pode mudar noutro dispositivo). */
function showPersonState() {
  const p = currentPerson();
  $('editTag').hidden = !p;
  if (p) {
    $('editTag').textContent = p.checked_at ? '✓ Check-in efetuado' : 'Por chegar';
    $('editTag').className = 'tag' + (p.checked_at ? ' ok' : '');
  }
  $('detail').textContent = p?.checked_at ? 'Entrada registada: ' + formatDate(p.checked_at) : '';
  syncButtons();
}

function select(id) {
  state.selected = id;
  state.isNew = false;
  fillEditor(currentPerson());
}

function newPerson() {
  state.selected = null;
  state.isNew = true;
  fillEditor(null);
  $('name').focus();
}

function syncButtons() {
  const p = currentPerson();
  $('save').disabled = state.busy;
  $('checkin').disabled = state.busy || !!p?.checked_at;
  $('reprint').disabled = state.busy || !p;
  $('undo').hidden = !p?.checked_at;
  $('undo').disabled = state.busy;
}

function preview() {
  const v = formValues();
  const set = (id, value, fallback) => {
    $(id).textContent = value || fallback;
    $(id).classList.toggle('placeholder', !value);
  };
  set('prevName', v.name, 'NOME APELIDO');
  set('prevRole', v.role, 'Função');
  set('prevCompany', v.company, 'Empresa');
  const n = v.name.length;
  $('prevName').style.fontSize = (n > 32 ? 19 : n > 24 ? 23 : n > 17 ? 26 : 32) + 'px';
}

const CONFIRM = {
  checkin: 'Confirmar check-in e enviar 1 badge para a impressora?',
  reprint: 'Reimprimir 1 badge?',
  undo: 'Anular o check-in deste participante?',
};

async function act(kind) {
  if (state.busy) return;
  const values = formValues();
  if (kind !== 'undo' && !values.name) return notify('O nome é obrigatório.', true);
  if (CONFIRM[kind] && !confirm(CONFIRM[kind])) return;
  state.busy = true;
  syncButtons();
  try {
    const d = await api('/api/' + kind, { ...values, id: state.selected });
    if (d.id) state.selected = d.id;
    state.isNew = false;
    await refresh();
    notify(d.message);
  } catch (e) {
    notify(e.message, true);
  } finally {
    state.busy = false;
    syncButtons();
  }
}

['name', 'role', 'company'].forEach(id => $(id).addEventListener('input', preview));
$('personForm').onsubmit = e => { e.preventDefault(); act('save'); };
['save', 'checkin', 'reprint', 'undo'].forEach(kind => { $(kind).onclick = () => act(kind); });
$('new').onclick = newPerson;
$('search').oninput = render;
$('filter').onchange = render;
$('list').onclick = e => {
  const row = e.target.closest('.person');
  if (row) select(row.dataset.id);
};

// «/» foca a pesquisa; Esc limpa-a.
document.addEventListener('keydown', e => {
  if ($('app').hidden) return;
  if (e.key === '/' && !e.target.matches('input, select, textarea')) {
    e.preventDefault();
    $('search').focus();
  } else if (e.key === 'Escape' && e.target === $('search')) {
    $('search').value = '';
    render();
  }
});

// Várias hospedeiras podem estar a fazer check-in ao mesmo tempo: atualiza a cada 10 s.
setInterval(async () => {
  if ($('app').hidden || document.hidden || state.busy) return;
  try {
    await loadPeople();
    render();
    showPersonState();
  } catch (e) {
    if (e.status === 403) boot();
  }
}, 10000);

/* ── Importação ──────────────────────────────────────────────────────────── */

$('importForm').onsubmit = async e => {
  e.preventDefault();
  const file = $('file').files[0];
  if (!confirm('Importar participantes? Os check-ins existentes serão preservados.')) return;
  await withBusy(e.submitter, async () => {
    try {
      const d = await api('/api/import?filename=' + encodeURIComponent(file.name), await file.arrayBuffer());
      $('importForm').reset();
      await refresh();
      notify(d.message);
    } catch (err) { notify(err.message, true); }
  });
};

$('toggleReplace').onclick = () => { $('replaceForm').hidden = !$('replaceForm').hidden; };

$('replaceForm').onsubmit = async e => {
  e.preventDefault();
  const file = $('replacementFile').files[0];
  const total = state.people.length, checked = state.people.filter(p => p.checked_at).length;
  if (!confirm(`ATENÇÃO: Vais substituir TODOS os ${total} participantes deste evento, incluindo ${checked} check-ins já efetuados. Os outros eventos não serão alterados. Continuar?`)) return;
  if (prompt('Para confirmar, escreve SUBSTITUIR (em maiúsculas):') !== 'SUBSTITUIR') return;
  await withBusy(e.submitter, async () => {
    try {
      const d = await api('/api/replace?filename=' + encodeURIComponent(file.name), await file.arrayBuffer());
      $('replaceForm').reset();
      $('replaceForm').hidden = true;
      state.selected = null;
      state.isNew = false;
      state.version = null;
      await refresh();
      notify(d.message);
    } catch (err) { notify(err.message, true); }
  });
};

boot();
