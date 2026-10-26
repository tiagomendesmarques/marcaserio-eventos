#!/usr/bin/env python3
"""marcAsério | Check-in — acreditação de eventos, multi-evento.

Cada evento tem a sua palavra-passe; a administração (criar eventos, alterar
palavras-passe, substituir listas) tem uma palavra-passe própria.

Corre de duas formas, escolhidas por variáveis de ambiente:
  • local (por omissão): num Mac ligado à Honeywell, dados em SQLite, como até aqui;
  • online: no Cloud Run, com MA_STORE=firestore e MA_PRINT_MODE=agente;
    as etiquetas ficam numa fila que o agente_impressao.py, no Mac do evento, imprime.

O bloco de impressão Direct Protocol (ascii_text, badge_layout, dp_commands,
print_badge) está preservado literalmente da v2.6 aprovada. Não alterar.
"""
import base64
import csv
import getpass
import hashlib
import hmac
import io
import json
import os
import posixpath
import re
import secrets
import subprocess
import sys
import threading
import time
import unicodedata
import webbrowser
import zipfile
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from store_sqlite import AlreadyCheckedIn, SqliteStore

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / 'web'

# Configuração por variáveis de ambiente; os valores por omissão são os do modo local.
HOST = os.environ.get('MA_HOST', '127.0.0.1')
PORT = int(os.environ.get('MA_PORT') or os.environ.get('PORT') or 8770)  # PORT: definido pelo Cloud Run
PUBLIC_URL = os.environ.get('MA_PUBLIC_URL', '').rstrip('/')  # ex.: https://eventos.marcaserio.pt
STORE_KIND = os.environ.get('MA_STORE', 'sqlite')              # 'sqlite' ou 'firestore'
FIRESTORE_DB = os.environ.get('MA_FIRESTORE_DB', 'marcaserio-eventos')
PRINT_MODE = os.environ.get('MA_PRINT_MODE', 'local')          # 'local' (lp neste computador) ou 'agente'
TRUST_PROXY = os.environ.get('MA_TRUST_PROXY', '')             # '', 'google' (Cloud Run) ou 'cloudflare'
OPEN_BROWSER = os.environ.get('MA_OPEN_BROWSER', '1') == '1'
DATA_DIR = Path(os.environ.get('MA_DATA_DIR', APP_DIR))

PRINTER = 'Honeywell_PC42d_203_FP'
WIDTH, HEIGHT = 560, 320

LOCAL_ORIGIN = f'http://127.0.0.1:{PORT}'
SECURE_COOKIE = PUBLIC_URL.startswith('https://')
NO_PASSWORD = '!password-not-set!'
SESSION_COOKIE = 'ma_session'
SESSION_TTL = 12 * 3600
MAX_UPLOAD = 12_000_000
MAX_ROWS = 20_000
MAX_NAME, MAX_TEXT = 150, 180
ADMIN_MIN_PASSWORD, EVENT_MIN_PASSWORD = 10, 8
LOGIN_WINDOW, LOGIN_MAX_FAILURES = 15 * 60, 10

STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/logo.png': ('logo_marcaserio.png', 'image/png'),
}

STORE = None              # definido em open_store()
IMPORT_LOCK = threading.Lock()
_SECRET = None


def open_store():
    global STORE
    if STORE_KIND == 'firestore':
        if PRINT_MODE == 'local':
            raise SystemExit('Com MA_STORE=firestore, usa MA_PRINT_MODE=agente.')
        from store_firestore import FirestoreStore  # só é preciso online
        STORE = FirestoreStore(os.environ.get('GOOGLE_CLOUD_PROJECT'), FIRESTORE_DB, NO_PASSWORD)
    else:
        STORE = SqliteStore(DATA_DIR / 'marcaserio_checkin.sqlite3', DATA_DIR / 'checkin_people_finance.sqlite3',
                            DATA_DIR / 'copias_seguranca', NO_PASSWORD)
    STORE.init()
    return STORE


def public_event(event):
    return {'id': event['id'], 'name': event['name'], 'event_date': event.get('event_date', '')}


# ── Palavras-passe e sessões ─────────────────────────────────────────────────

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 240000)
    return salt.hex() + ':' + digest.hex()


def check_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split(':', 1)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), 240000)
        return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def admin_hash():
    return STORE.get_setting('admin_password')


def require_admin_password(password, error='Palavra-passe de administração incorreta.'):
    stored = admin_hash()
    if not stored or not check_password(password, stored):
        raise PermissionError(error)


def set_admin_password(password, only_if_absent=False):
    if len(password) < ADMIN_MIN_PASSWORD:
        raise ValueError(f'A palavra-passe de administração deve ter pelo menos {ADMIN_MIN_PASSWORD} caracteres.')
    return STORE.set_setting('admin_password', hash_password(password), only_if_absent=only_if_absent)


def check_attempts(ip):
    if STORE.recent_failures(ip, LOGIN_WINDOW) >= LOGIN_MAX_FAILURES:
        raise PermissionError('Demasiadas tentativas falhadas. Tenta novamente dentro de 15 minutos.')


# As sessões são cookies assinados (nada fica em memória), para funcionarem com
# vários contentores. Levam a impressão digital da palavra-passe: mudá-la termina-as.

def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _fingerprint(password_hash):
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def session_secret():
    global _SECRET
    if not _SECRET:
        STORE.set_setting('session_secret', secrets.token_hex(32), only_if_absent=True)
        _SECRET = STORE.get_setting('session_secret').encode()
    return _SECRET


def _signature(payload):
    return _b64(hmac.new(session_secret(), payload.encode(), 'sha256').digest())


def new_session(kind, password_hash, event_id=None):
    payload = _b64(json.dumps({'k': kind, 'e': event_id, 'x': int(time.time() + SESSION_TTL),
                               'f': _fingerprint(password_hash)}).encode())
    return f'{payload}.{_signature(payload)}'


def read_session(token):
    """Devolve {'kind', 'event'} se o cookie for válido e a palavra-passe não tiver mudado."""
    try:
        payload, signature = token.split('.')
        if not hmac.compare_digest(signature, _signature(payload)):
            return None
        data = json.loads(_unb64(payload))
    except (AttributeError, ValueError, TypeError):
        return None
    if data['x'] < time.time():
        return None
    if data['k'] == 'admin':
        stored = admin_hash()
        return {'kind': 'admin', 'event': None} if stored and _fingerprint(stored) == data['f'] else None
    event = STORE.get_event(data['e'])
    if not event or _fingerprint(event['password_hash']) != data['f']:
        return None
    return {'kind': 'event', 'event': event}


# ── Impressão Direct Protocol (preservado literalmente da v2.6) ──────────────

def ascii_text(s):
    s = unicodedata.normalize('NFKD', str(s))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ''.join(c if 32 <= ord(c) <= 126 and c not in '"\\' else ' ' for c in s).strip()

def badge_layout(name, role='', company=''):
    """70x40mm Direct Protocol: origin on printed label is bottom-up.

    Text positions are measured from the bottom, so name uses HIGHER Y.
    No graphics, no font substitutions; Univers remains printer-native.
    """
    name, role, company = ascii_text(name), ascii_text(role), ascii_text(company)
    if not name:
        raise ValueError('Nome obrigatório para imprimir.')
    if any(len(v) > 100 for v in (name, role, company)):
        raise ValueError('Campo demasiado longo para a etiqueta.')

    # Compact group centered on the 320-dot label.
    # DP Y increases from bottom to top. Keep v2.2 font sizes.
    layout = []
    def fit(value, preferred, minimum, width=465):
        # Conservative character-width estimate for native Univers.
        return min(preferred, int(width / max(1, len(value) * 1.28)))

    name_size = fit(name, 23, 17)
    if name_size >= 17:
        layout.append({'kind':'name', 'text':name, 'size':name_size, 'y':191})
    else:
        words=name.split()
        candidates=[]
        for cut in range(1,len(words)):
            a,b=' '.join(words[:cut]),' '.join(words[cut:])
            size=min(21,fit(a,21,0),fit(b,21,0))
            candidates.append((size,-abs(len(a)-len(b)),a,b))
        if not candidates:
            raise ValueError('Nome demasiado comprido para a etiqueta.')
        size,_,a,b=max(candidates)
        if size<15:
            raise ValueError('Nome demasiado comprido para a etiqueta. Encurta-o.')
        layout.extend([{'kind':'name','text':a,'size':size,'y':212},
                       {'kind':'name','text':b,'size':size,'y':170}])

    if role:
        size=fit(role,13,10)
        if size<10:
            raise ValueError('Função demasiado comprida para a etiqueta. Encurta-a.')
        layout.append({'kind':'role','text':role,'size':size,'y':137})
    if company:
        size=fit(company,15,11)
        if size<11:
            raise ValueError('Empresa demasiado comprida para a etiqueta. Encurta-a.')
        layout.append({'kind':'company','text':company,'size':size,'y':83})
    return layout


def dp_commands(name, role='', company=''):
    """Preserva a sequencia Direct Protocol testada: CLL/DIR 1/ALIGN 5/PRINTFEED."""
    lines = badge_layout(name, role, company)
    commands = ['CLL', 'DIR 1', 'ALIGN 5']
    for line in lines:
        commands.extend([f'FONT "Univers",{line["size"]}',
                         f'PRPOS {WIDTH // 2},{line["y"]}',
                         f'PRTXT "{line["text"]}"'])
    commands.append('PRINTFEED')
    return ('\r\n'.join(commands) + '\r\n').encode('ascii')

def print_badge(name, role='', company=''):
    data = dp_commands(name, role, company)
    p = subprocess.run(['lp', '-d', PRINTER, '-o', 'raw'], input=data, capture_output=True, timeout=20)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace').strip() or 'Erro ao enviar para a impressora.')
    return p.stdout.decode(errors='replace').strip()


# ── Fila de impressão (modo agente) ──────────────────────────────────────────
# Online não há impressora: o servidor gera os mesmos comandos (dp_commands, inalterado)
# e o agente_impressao.py, no Mac do evento, envia-os para a Honeywell com o mesmo lp.

JOB_MAX_AGE = 15 * 60  # etiquetas por imprimir há mais tempo do que isto já não saem
AGENT_TIMEOUT = 45     # segundos sem contacto até a impressora aparecer como desligada
JOBS_READY = threading.Condition()


def badge(name, role, company):
    """Imprime já (modo local) ou devolve os comandos para a fila do evento (modo agente)."""
    if PRINT_MODE == 'local':
        print_badge(name, role, company)
        return None
    return dp_commands(name, role, company)


def wake_agents():
    with JOBS_READY:
        JOBS_READY.notify_all()


def next_job(event, wait=20):
    """Long-poll: devolve a próxima etiqueta do evento, ou None ao fim de `wait` segundos.

    Procura a cada segundo (pode ter sido criada noutro contentor) e acorda logo
    quando o check-in acontece neste.
    """
    deadline = time.time() + wait
    while True:
        STORE.agent_seen(event)
        job = STORE.claim_job(event, JOB_MAX_AGE)
        remaining = deadline - time.time()
        if job or remaining <= 0:
            return job
        with JOBS_READY:
            JOBS_READY.wait(min(1.0, remaining))


def printer_status(event):
    if PRINT_MODE == 'local':
        return {'mode': 'local'}
    pending, error, last_seen = STORE.printer_info(event, JOB_MAX_AGE)
    return {'mode': 'agente', 'online': time.time() - last_seen < AGENT_TIMEOUT, 'pending': pending, 'error': error}


# ── Importação de listas (.xlsx / .csv) ──────────────────────────────────────

COLUMNS = {
    'name': {'nome', 'nome completo', 'name', 'full name', 'participante', 'attendee'},
    'role': {'funcao', 'cargo', 'role', 'job title', 'jobtitle', 'position', 'titulo profissional'},
    'company': {'empresa', 'company', 'organization', 'organisation', 'organizacao', 'entidade'},
}


def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s or '').casefold()) if not unicodedata.combining(c))


def person_key(name, company):
    return norm(name), norm(company)


def fields_valid(name, role, company):
    return bool(name) and len(name) <= MAX_NAME and len(role) <= MAX_TEXT and len(company) <= MAX_TEXT


def safe_fields(data):
    name, role, company = (str(data.get(k) or '').strip() for k in ('name', 'role', 'company'))
    if not fields_valid(name, role, company):
        raise ValueError(f'Preenche um nome válido (até {MAX_NAME} caracteres); função e empresa até {MAX_TEXT}.')
    return name, role, company


def column_index(ref):
    """'A1' → 0, 'AB7' → 27."""
    letters = re.match(r'[A-Z]+', ref)
    if not letters:
        return None
    idx = 0
    for letter in letters.group():
        idx = idx * 26 + ord(letter) - 64
    return idx - 1


def xlsx_rows(blob):
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        shared = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            shared = [''.join(t.text or '' for t in si.findall('.//m:t', ns)) for si in root.findall('m:si', ns)]
        sheet = ET.fromstring(z.read('xl/workbook.xml')).find('m:sheets/m:sheet', ns)
        if sheet is None:
            raise ValueError('O Excel não tem folhas.')
        # Primeira folha do livro, resolvida pelas relações (nem sempre é sheet1.xml).
        sheet_path = 'xl/worksheets/sheet1.xml'
        rid = sheet.get('{' + ns['r'] + '}id')
        if rid and 'xl/_rels/workbook.xml.rels' in names:
            for rel in ET.fromstring(z.read('xl/_rels/workbook.xml.rels')):
                if rel.get('Id') == rid:
                    target = rel.get('Target', '')
                    sheet_path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
                    break
        for row in ET.fromstring(z.read(sheet_path)).findall('.//m:sheetData/m:row', ns):
            values = {}
            for c in row.findall('m:c', ns):
                idx = column_index(c.get('r', ''))
                if idx is None:
                    continue
                kind, v = c.get('t'), c.find('m:v', ns)
                if kind == 'inlineStr':
                    values[idx] = ''.join(x.text or '' for x in c.findall('.//m:t', ns))
                elif v is None:
                    values[idx] = ''
                elif kind == 's':
                    values[idx] = shared[int(v.text or 0)]
                else:
                    values[idx] = v.text or ''
            if values:
                yield [values.get(i, '') for i in range(max(values) + 1)]


def csv_rows(blob):
    try:
        content = blob.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = blob.decode('cp1252')
    try:
        dialect = csv.Sniffer().sniff(content[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    yield from csv.reader(io.StringIO(content), dialect)


def parse_people(blob, filename):
    """Lê e valida o ficheiro inteiro antes de qualquer alteração à base de dados."""
    if len(blob) > MAX_UPLOAD:
        raise ValueError('Ficheiro demasiado grande (máximo 12 MB).')
    ext = Path(filename).suffix.lower()
    if ext == '.xlsx':
        rows = xlsx_rows(blob)
    elif ext == '.csv':
        rows = csv_rows(blob)
    else:
        raise ValueError('Formato não suportado. Usa .xlsx ou .csv.')
    rows = [row for row in rows if any(str(c).strip() for c in row)]
    if len(rows) < 2:
        raise ValueError('A lista não contém participantes. Nenhum dado foi alterado.')
    if len(rows) > MAX_ROWS:
        raise ValueError('Lista demasiado grande (máximo 20 000 linhas).')

    header = [norm(c).strip().replace('_', ' ').replace('-', ' ') for c in rows[0]]
    index = {field: next((i for i, h in enumerate(header) if h in names), None) for field, names in COLUMNS.items()}
    if index['name'] is None:
        raise ValueError('Não encontrei a coluna Nome. Usa cabeçalhos: Nome, Função, Empresa.')

    def cell(row, field):
        i = index[field]
        return str(row[i]).strip() if i is not None and i < len(row) else ''

    people, seen, skipped = [], set(), 0
    for row in rows[1:]:
        name, role, company = (cell(row, f) for f in ('name', 'role', 'company'))
        key = person_key(name, company)
        if not fields_valid(name, role, company) or key in seen:
            skipped += 1
            continue
        seen.add(key)
        people.append((name, role, company))
    if not people:
        raise ValueError('Não foram encontrados participantes válidos. Nenhum dado foi alterado.')
    return people, skipped


def import_people(event, blob, filename):
    """Acrescenta participantes novos; os existentes (e os seus check-ins) mantêm-se."""
    people, skipped = parse_people(blob, filename)
    with IMPORT_LOCK:
        existing = {person_key(name, company) for name, company in STORE.person_keys(event)}
        new = [p for p in people if person_key(p[0], p[2]) not in existing]
        STORE.add_people(event, new)
    return len(new), skipped + len(people) - len(new)


def replace_people(event, blob, filename, admin_password):
    """Substitui a lista (e os check-ins) deste evento, com cópia de segurança prévia."""
    require_admin_password(admin_password, 'Palavra-passe de administração incorreta. A lista não foi alterada.')
    people, skipped = parse_people(blob, filename)
    with IMPORT_LOCK:
        old_count, backup = STORE.replace_people(event, people)
    return old_count, len(people), skipped, backup


# ── API HTTP ─────────────────────────────────────────────────────────────────

ROUTES = {}
PERSON_MESSAGES = {
    'save': 'Dados guardados.',
    'checkin': 'Check-in registado e etiqueta enviada para a fila.',
    'reprint': 'Badge enviado para reimpressão.',
    'undo': 'Check-in anulado.',
}


def route(method, *paths):
    def register(fn):
        for path in paths:
            ROUTES[method, path] = fn
        return fn
    return register


@route('GET', '/api/session')
def api_session(h):
    sess = h.session
    return {'admin_configured': bool(admin_hash()),
            'is_admin': bool(sess and sess['kind'] == 'admin'),
            'event': public_event(sess['event']) if sess and sess['event'] else None}


@route('GET', '/api/events')
def api_events(h):
    return {'events': STORE.list_events()}


@route('GET', '/api/admin/events')
def api_admin_events(h):
    h.require('admin')
    return {'events': STORE.list_events_with_stats()}


@route('GET', '/api/people')
def api_people(h):
    """Lista do evento. Com ?v=<versão>&since=<instante>, só o que mudou desde então."""
    event = h.require('event')['event']
    version, full, people = STORE.people(event, h.param('v'), h.param('since'))
    return {'version': version, 'full': full, 'people': people, 'now': time.time(), 'printer': printer_status(event)}


@route('GET', '/api/export')
def api_export(h):
    event = h.require('event')['event']
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';')
    writer.writerow(['Nome', 'Função', 'Empresa', 'Check-in', 'Data/hora', 'Origem'])
    for p in STORE.people(event)[2]:
        writer.writerow([p['name'], p['role'], p['company'], 'Sim' if p['checked_at'] else 'Não', p['checked_at'] or '', p['source']])
    slug = re.sub(r'[^a-z0-9]+', '_', norm(event['name'])).strip('_') or 'evento'
    h.send(200, ('﻿' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8',
           [('Content-Disposition', f'attachment; filename="checkins_{slug}.csv"')])


@route('POST', '/api/logout')
def api_logout(h):
    return {'message': 'Sessão terminada.'}, ''


@route('POST', '/api/setup-admin')
def api_setup_admin(h):
    if PUBLIC_URL:
        # Online, quem abrisse o site primeiro ficaria com a administração.
        raise PermissionError('Na versão online, a palavra-passe de administração define-se com: bash gcp/definir_admin.sh')
    if not set_admin_password(str(h.data.get('password', '')), only_if_absent=True):
        raise PermissionError('Administração já configurada.')
    return {'message': 'Administração configurada.'}, new_session('admin', admin_hash())


@route('POST', '/api/login-admin')
def api_login_admin(h):
    require_admin_password(str(h.data.get('password', '')))
    return {'message': 'Autenticado.'}, new_session('admin', admin_hash())


@route('POST', '/api/login-event')
def api_login_event(h):
    event = STORE.get_event(str(h.data.get('event_id', '')))
    if event and event['password_hash'] == NO_PASSWORD:
        raise PermissionError('Este evento ainda não tem palavra-passe. Define-a em Administração de eventos.')
    if not event or not check_password(str(h.data.get('password', '')), event['password_hash']):
        raise PermissionError('Palavra-passe do evento incorreta.')
    return {'event': public_event(event)}, new_session('event', event['password_hash'], event['id'])


@route('POST', '/api/admin/create-event')
def api_create_event(h):
    h.require('admin')
    name = str(h.data.get('name', '')).strip()
    date = str(h.data.get('event_date', '')).strip()
    password = str(h.data.get('password', ''))
    if not name or len(name) > 130:
        raise ValueError('Indica um nome válido para o evento.')
    if date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        raise ValueError('Data inválida.')
    if len(password) < EVENT_MIN_PASSWORD:
        raise ValueError(f'A palavra-passe do evento deve ter pelo menos {EVENT_MIN_PASSWORD} caracteres.')
    return {'message': 'Evento criado.', 'id': STORE.create_event(name, date, hash_password(password))}


@route('POST', '/api/admin/change-password')
def api_change_password(h):
    h.require('admin')
    event_id, password = str(h.data.get('event_id', '')), str(h.data.get('password', ''))
    if len(password) < EVENT_MIN_PASSWORD:
        raise ValueError(f'A palavra-passe deve ter pelo menos {EVENT_MIN_PASSWORD} caracteres.')
    if not STORE.set_event_password(event_id, hash_password(password)):
        raise ValueError('Evento não encontrado.')
    return {'message': 'Palavra-passe alterada. As sessões anteriores deste evento foram terminadas.'}


@route('POST', '/api/save', '/api/checkin', '/api/reprint', '/api/undo')
def api_person(h):
    action, event = h.path_only.rsplit('/', 1)[-1], h.require('event')['event']
    fields = None if action == 'undo' else safe_fields(h.data)
    pid = STORE.person_action(event, action, str(h.data.get('id') or '') or None, fields, badge)
    message = PERSON_MESSAGES[action]
    if PRINT_MODE != 'local' and action in ('checkin', 'reprint'):
        wake_agents()
        if not printer_status(event)['online']:
            message += ' Atenção: a impressora do evento está desligada; a etiqueta fica em fila durante 15 minutos.'
    return {'message': message, 'id': pid}


@route('GET', '/api/print/next')
def api_print_next(h):
    if PRINT_MODE == 'local':
        raise ValueError('Este servidor imprime diretamente (modo local).')
    job = next_job(h.require('event')['event'])
    return {'job': job and {'id': job['id'], 'label': job['label'], 'data': base64.b64encode(job['data']).decode()}}


@route('POST', '/api/print/result')
def api_print_result(h):
    STORE.finish_job(h.require('event')['event'], str(h.data.get('id', '')), str(h.data.get('error') or '')[:300])
    return {'message': 'Registado.'}


@route('POST', '/api/import')
def api_import(h):
    count, skipped = import_people(h.require('event')['event'], h.body, h.param('filename'))
    return {'message': f'{count} participantes importados. {skipped} ignorados (duplicados ou inválidos).'}


@route('POST', '/api/replace')
def api_replace(h):
    old_count, count, skipped, backup = replace_people(
        h.require('event')['event'], h.body, h.param('filename'), h.headers.get('X-Admin-Password', ''))
    return {'message': f'Lista substituída: {old_count} anteriores removidos; {count} participantes importados; '
                       f'{skipped} linhas ignoradas. Cópia de segurança: {backup}.'}


PASSWORD_ROUTES = {'/api/setup-admin', '/api/login-admin', '/api/login-event', '/api/replace'}


class Handler(BaseHTTPRequestHandler):
    body = b''

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def dispatch(self, method):
        url = urlparse(self.path)
        self.path_only, self.query = url.path, parse_qs(url.query)
        if method == 'GET' and url.path in STATIC:
            filename, ctype = STATIC[url.path]
            return self.send(200, (WEB_DIR / filename).read_bytes(), ctype)
        handler = ROUTES.get((method, url.path))
        if not handler:
            return self.send_error(404)
        guarded = url.path in PASSWORD_ROUTES
        try:
            if guarded:
                check_attempts(self.client_ip)
            if method == 'POST':
                self.read_body()
            result = handler(self)
            if result is not None:
                payload, cookie = result if isinstance(result, tuple) else (result, None)
                self.send_json(payload, cookie=cookie)
        except PermissionError as exc:
            if guarded:
                STORE.note_failure(self.client_ip)
            self.send_json({'error': str(exc)}, 403)
        except AlreadyCheckedIn as exc:
            self.send_json({'error': str(exc)}, 409)
        except Exception as exc:
            self.send_json({'error': str(exc)}, 400)

    def read_body(self):
        # Apenas escritas da própria página (mesma origem), incluindo ficheiros importados.
        origin = self.headers.get('Origin', '')
        same_host = PUBLIC_URL and urlparse(origin).netloc == self.headers.get('Host', '')
        if origin and origin not in (LOCAL_ORIGIN, PUBLIC_URL) and not same_host:
            raise PermissionError('Origem não autorizada.')
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > MAX_UPLOAD:
            raise ValueError('Pedido demasiado grande.')
        self.body = self.rfile.read(length)

    @property
    def data(self):
        try:
            data = json.loads(self.body or b'{}')
        except ValueError:
            raise ValueError('Pedido inválido.') from None
        return data if isinstance(data, dict) else {}

    def param(self, name):
        return self.query.get(name, [''])[0]

    @property
    def client_ip(self):
        if TRUST_PROXY == 'cloudflare':
            ip = self.headers.get('CF-Connecting-IP')
        elif TRUST_PROXY == 'google':
            # O Google acrescenta o IP real no fim de X-Forwarded-For; o início pode vir do cliente.
            ip = self.headers.get('X-Forwarded-For', '').split(',')[-1].strip()
        else:
            ip = None
        return ip or self.client_address[0]

    @property
    def session(self):
        try:
            morsel = SimpleCookie(self.headers.get('Cookie', '')).get(SESSION_COOKIE)
        except CookieError:
            return None
        return read_session(morsel.value) if morsel else None

    def require(self, kind):
        sess = self.session
        if not sess or sess['kind'] != kind:
            raise PermissionError('Acesso não autorizado. Entra novamente.')
        return sess

    def send(self, status, body, ctype, headers=()):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=200, cookie=None):
        headers = []
        if cookie is not None:  # '' apaga o cookie
            max_age = SESSION_TTL if cookie else 0
            secure = '; Secure' if SECURE_COOKIE else ''
            headers.append(('Set-Cookie', f'{SESSION_COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}'))
        self.send(status, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', headers)


def prompt_admin_password():
    """Definir ou repor a palavra-passe de administração a partir do terminal."""
    password = getpass.getpass('Nova palavra-passe de administração: ')
    if password != getpass.getpass('Confirmar: '):
        raise SystemExit('As palavras-passe não coincidem.')
    try:
        set_admin_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc))
    print('Palavra-passe de administração definida.')


def main():
    open_store()
    if '--definir-admin' in sys.argv[1:]:
        return prompt_admin_password()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        raise SystemExit(f'Não foi possível iniciar a porta {PORT}. Fecha a versão anterior com Control+C. ({exc})')
    url = f'http://{HOST}:{PORT}/'
    print(f'marcAsério | Check-in: {PUBLIC_URL or url} (dados: {STORE_KIND}, impressão: {PRINT_MODE})', flush=True)
    if OPEN_BROWSER:
        print('Para terminar: Control+C')
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nA terminar...')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
