'use strict';

const $ = id => document.getElementById(id);
const state = { people: [], selected: null, isNew: false, busy: false, chosenEvent: null };

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
const authSay = (text, isError = true) => say($('authStatus'), text, isError);

async function withBusy(button, fn) {
  button.disabled = true;
  try { await fn(); } finally { button.disabled = false; }
}

/* ── Entrada e administração ─────────────────────────────────────────────── */

async function boot() {
  try {
    const session = await api('/api/session');
    if (session.event) showApp(session.event);
    else await showAuth(session);
  } catch (e) { authSay(e.message); }
}

async function showAuth(session) {
  $('app').hidden = true;
  $('auth').hidden = false;
  state.chosenEvent = null;
  $('eventLogin').hidden = true;
  const firstRun = !session.admin_configured;
  $('setupAdmin').hidden = !firstRun;
  $('adminSection').hidden = firstRun;
  $('authTitle').textContent = firstRun ? 'Primeira configuração' : 'Escolher evento';
  $('authIntro').textContent = firstRun
    ? 'Define uma palavra-passe de administração para gerir os eventos.'
    : 'Escolhe o evento e introduz a respetiva palavra-passe.';
  await loadEvents();
  if (session.is_admin) await showAdminTools();
}

async function loadEvents() {
  const { events } = await api('/api/events');
  $('eventList').innerHTML = events.map(e => `
    <button type="button" class="event-choice" data-event="${escapeHTML(e.id)}">
      <b>${escapeHTML(e.name)}</b><small>${escapeHTML(e.event_date)}</small>
    </button>`).join('')
    || '<p class="empty muted small">Ainda não existem eventos. Entra em Administração de eventos para criar o primeiro.</p>';
}

$('eventList').onclick = e => {
  const choice = e.target.closest('[data-event]');
  if (!choice) return;
  state.chosenEvent = choice.dataset.event;
  document.querySelectorAll('[data-event]').forEach(b => b.classList.toggle('selected', b === choice));
  $('eventLogin').hidden = false;
  $('eventPassword').focus();
  authSay('');
};

$('eventLogin').onsubmit = async e => {
  e.preventDefault();
  try {
    const { event } = await api('/api/login-event', { event_id: state.chosenEvent, password: $('eventPassword').value });
    $('eventPassword').value = '';
    authSay('');
    showApp(event);
  } catch (err) { authSay(err.message); }
};

$('setupAdmin').onsubmit = async e => {
  e.preventDefault();
  if ($('setupPassword').value !== $('setupPassword2').value) return authSay('As palavras-passe não coincidem.');
  try {
    await api('/api/setup-admin', { password: $('setupPassword').value });
    $('setupAdmin').reset();
    await showAuth({ admin_configured: true, is_admin: true });
    authSay('Administração configurada. Cria o primeiro evento.', false);
  } catch (err) { authSay(err.message); }
};

$('toggleAdmin').onclick = () => {
  $('adminLogin').hidden = !$('adminLogin').hidden;
  if (!$('adminLogin').hidden) $('adminPassword').focus();
};

$('adminLogin').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('/api/login-admin', { password: $('adminPassword').value });
    $('adminLogin').reset();
    $('adminLogin').hidden = true;
    authSay('');
    await showAdminTools();
  } catch (err) { authSay(err.message); }
};

$('adminLogout').onclick = async () => {
  await api('/api/logout', {});
  $('adminTools').hidden = true;
  $('toggleAdmin').hidden = false;
  authSay('');
};

async function showAdminTools() {
  $('adminTools').hidden = false;
  $('toggleAdmin').hidden = true;
  const { events } = await api('/api/admin/events');
  $('adminEventList').innerHTML = events.map(e => `
    <div class="admin-event">
      <div class="row between">
        <b>${escapeHTML(e.name)}</b>
        ${e.has_password ? `<span class="tag">${e.checked}/${e.total} check-ins</span>` : '<span class="tag warn">Sem palavra-passe</span>'}
      </div>
      <div class="small muted">${escapeHTML(e.event_date || 'Sem data')}</div>
      <form data-id="${escapeHTML(e.id)}">
        <input type="password" placeholder="Nova palavra-passe (mín. 8)" autocomplete="new-password" required>
        <button class="btn small">${e.has_password ? 'Alterar' : 'Definir'}</button>
      </form>
    </div>`).join('') || '<p class="muted small">Ainda não existem eventos.</p>';
}

$('adminEventList').onsubmit = async e => {
  e.preventDefault();
  const form = e.target, input = form.querySelector('input');
  if (!confirm('Alterar a palavra-passe deste evento?')) return;
  try {
    const d = await api('/api/admin/change-password', { event_id: form.dataset.id, password: input.value });
    await showAdminTools();
    authSay(d.message, false);
  } catch (err) { authSay(err.message); }
};

$('createEvent').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('/api/admin/create-event', {
      name: $('eventName').value, event_date: $('eventDate').value, password: $('newEventPassword').value,
    });
    $('createEvent').reset();
    await Promise.all([loadEvents(), showAdminTools()]);
    authSay('Evento criado com sucesso.', false);
  } catch (err) { authSay(err.message); }
};

/* ── Evento ──────────────────────────────────────────────────────────────── */

function showApp(event) {
  $('auth').hidden = true;
  $('app').hidden = false;
  $('eventTitle').textContent = event.name;
  document.title = `${event.name} | marcAsério Check-in`;
  state.selected = null;
  state.isNew = false;
  refresh().catch(e => notify(e.message, true));
}

$('leaveEvent').onclick = async () => {
  await api('/api/logout', {});
  document.title = 'marcAsério | Check-in';
  boot();
};

const currentPerson = () => state.people.find(p => p.id === state.selected);
const formValues = () => ({ name: clean($('name').value), role: clean($('role').value), company: clean($('company').value) });

/** Atualiza lista, contadores e impressora sem tocar no formulário. */
async function loadPeople() {
  const d = await api('/api/people');
  state.people = d.people;
  const pct = d.total ? Math.round(d.checked / d.total * 100) : 0;
  $('total').textContent = d.total;
  $('checked').textContent = d.checked;
  $('pending').textContent = d.total - d.checked;
  $('percent').textContent = d.total ? `· ${pct}%` : '';
  $('progress').style.width = pct + '%';
  showPrinter(d.printer);
  return d;
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
  const d = await loadPeople();
  if (!d.total) $('importBox').open = true;

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
    || `<div class="empty">${state.people.length ? 'Sem resultados.' : 'Ainda não existem participantes. Importa uma lista abaixo.'}</div>`;
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
      const d = await api('/api/replace?filename=' + encodeURIComponent(file.name), await file.arrayBuffer(),
        { 'X-Admin-Password': $('replaceAdminPassword').value });
      $('replaceForm').reset();
      $('replaceForm').hidden = true;
      state.selected = null;
      state.isNew = false;
      await refresh();
      notify(d.message);
    } catch (err) { notify(err.message, true); }
  });
};

boot();
