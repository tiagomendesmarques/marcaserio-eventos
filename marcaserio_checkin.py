#!/usr/bin/env python3
"""marcAsério | Check-in — servidor local de acreditação multi-evento.

Cada evento tem a sua palavra-passe; a administração (criar eventos, alterar
palavras-passe, substituir listas) tem uma palavra-passe própria.

Corre de duas formas, escolhidas por variáveis de ambiente:
  • local (por omissão): num Mac ligado à Honeywell, como até aqui;
  • online: num servidor (ex.: GCP atrás de Cloudflare Tunnel), com MA_PRINT_MODE=agente;
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
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
import zipfile
from contextlib import contextmanager
from datetime import datetime
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / 'web'

# Configuração por variáveis de ambiente; os valores por omissão são os do modo local.
HOST = os.environ.get('MA_HOST', '127.0.0.1')
PORT = int(os.environ.get('MA_PORT', '8770'))
PUBLIC_URL = os.environ.get('MA_PUBLIC_URL', '').rstrip('/')  # ex.: https://eventos.marcaserio.pt
PRINT_MODE = os.environ.get('MA_PRINT_MODE', 'local')          # 'local' (lp neste computador) ou 'agente'
TRUST_PROXY = os.environ.get('MA_TRUST_PROXY') == '1'          # IP real via CF-Connecting-IP (Cloudflare)
OPEN_BROWSER = os.environ.get('MA_OPEN_BROWSER', '1') == '1'
DATA_DIR = Path(os.environ.get('MA_DATA_DIR', APP_DIR))

PRINTER = 'Honeywell_PC42d_203_FP'
WIDTH, HEIGHT = 560, 320

DB_PATH = DATA_DIR / 'marcaserio_checkin.sqlite3'
LEGACY_DB = DATA_DIR / 'checkin_people_finance.sqlite3'
BACKUP_DIR = DATA_DIR / 'copias_seguranca'
ORIGINS = {f'http://127.0.0.1:{PORT}', PUBLIC_URL} - {''}
SECURE_COOKIE = PUBLIC_URL.startswith('https://')

LEGACY_EVENT_ID = 'legacy-people-finance'
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

LOCK = threading.RLock()
SESSIONS = {}
FAILURES = {}  # IP → instantes das tentativas falhadas recentes


# ── Base de dados ────────────────────────────────────────────────────────────

@contextmanager
def database():
    """Ligação numa transação: commit no fim, rollback em erro, fecho sempre."""
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def copy_sqlite(source_uri, target):
    source, dest = sqlite3.connect(source_uri, uri=True), sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        source.close()
        dest.close()


def backup_database(prefix):
    BACKUP_DIR.mkdir(exist_ok=True)
    path = BACKUP_DIR / f'{prefix}_{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(4)}.sqlite3'
    copy_sqlite(DB_PATH.as_uri(), path)
    return path


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def init_db():
    # A base de dados original da v2.6 nunca é editada: faz-se uma cópia de trabalho única.
    if not DB_PATH.exists() and LEGACY_DB.exists():
        copy_sqlite(LEGACY_DB.as_uri() + '?mode=ro', DB_PATH)
    with database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS people (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '', checked_at TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'manual')''')
        db.execute('''CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, event_date TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS print_jobs (
            id TEXT PRIMARY KEY, event_id TEXT NOT NULL, label TEXT NOT NULL, data BLOB NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
            created_ts REAL NOT NULL, updated_ts REAL NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS ix_jobs_event ON print_jobs(event_id, status, created_ts)')
        if 'event_id' not in {row['name'] for row in db.execute('PRAGMA table_info(people)')}:
            db.execute('ALTER TABLE people ADD COLUMN event_id TEXT')
        db.execute('CREATE INDEX IF NOT EXISTS ix_people_event ON people(event_id)')
        db.execute('CREATE INDEX IF NOT EXISTS ix_people_name ON people(name)')
        # Participantes antigos (sem evento) pertencem apenas ao evento original.
        if db.execute('SELECT 1 FROM people WHERE event_id IS NULL LIMIT 1').fetchone():
            db.execute('INSERT OR IGNORE INTO events(id,name,event_date,password_hash,created_at) VALUES (?,?,?,?,?)',
                       (LEGACY_EVENT_ID, 'People & Finance Forum 2026', '2026-10-15', NO_PASSWORD, now()))
            db.execute('UPDATE people SET event_id=? WHERE event_id IS NULL', (LEGACY_EVENT_ID,))


def list_events():
    with database() as db:
        return [dict(r) for r in db.execute('SELECT id,name,event_date FROM events ORDER BY event_date DESC,name')]


def list_events_with_stats():
    with database() as db:
        rows = db.execute('''SELECT e.id, e.name, e.event_date, e.password_hash<>? AS has_password,
                                    COUNT(p.id) AS total, COUNT(p.checked_at) AS checked
                             FROM events e LEFT JOIN people p ON p.event_id=e.id
                             GROUP BY e.id ORDER BY e.event_date DESC, e.name''', (NO_PASSWORD,))
        return [dict(r) for r in rows]


def get_event(event_id):
    with database() as db:
        row = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
    return dict(row) if row else None


def public_event(event):
    return {'id': event['id'], 'name': event['name'], 'event_date': event['event_date']}


def event_people(event_id):
    with database() as db:
        return [dict(r) for r in db.execute(
            'SELECT * FROM people WHERE event_id=? ORDER BY name COLLATE NOCASE,id', (event_id,))]


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
    with database() as db:
        row = db.execute("SELECT value FROM settings WHERE key='admin_password'").fetchone()
    return row['value'] if row else None


def require_admin_password(password, error='Palavra-passe de administração incorreta.'):
    stored = admin_hash()
    if not stored or not check_password(password, stored):
        raise PermissionError(error)


def set_admin_password(password):
    if len(password) < ADMIN_MIN_PASSWORD:
        raise ValueError(f'A palavra-passe de administração deve ter pelo menos {ADMIN_MIN_PASSWORD} caracteres.')
    with LOCK, database() as db:
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('admin_password',?)", (hash_password(password),))


def check_attempts(ip):
    with LOCK:
        recent = [t for t in FAILURES.get(ip, []) if t > time.time() - LOGIN_WINDOW]
        FAILURES[ip] = recent
        if len(recent) >= LOGIN_MAX_FAILURES:
            raise PermissionError('Demasiadas tentativas falhadas. Tenta novamente dentro de 15 minutos.')


def note_failure(ip):
    with LOCK:
        FAILURES.setdefault(ip, []).append(time.time())


def new_session(kind, event_id=None):
    token = secrets.token_urlsafe(32)
    with LOCK:
        SESSIONS[token] = {'kind': kind, 'event_id': event_id, 'expires': time.time() + SESSION_TTL}
    return token


def get_session(token):
    with LOCK:
        entry = SESSIONS.get(token)
        if entry and entry['expires'] < time.time():
            del SESSIONS[token]
            return None
        return entry


def end_event_sessions(event_id):
    with LOCK:
        for token, sess in list(SESSIONS.items()):
            if sess['kind'] == 'event' and sess['event_id'] == event_id:
                del SESSIONS[token]


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
AGENTS = {}            # event_id → último contacto do agente
JOBS_READY = threading.Condition()


def send_badge(db, event_id, name, role, company):
    """Imprime já (modo local) ou põe a etiqueta na fila do evento, na mesma transação."""
    if PRINT_MODE == 'local':
        print_badge(name, role, company)
        return
    t = time.time()
    db.execute('INSERT INTO print_jobs(id,event_id,label,data,created_ts,updated_ts) VALUES (?,?,?,?,?,?)',
               (str(uuid.uuid4()), event_id, name, dp_commands(name, role, company), t, t))


def wake_agents():
    with JOBS_READY:
        JOBS_READY.notify_all()


def claim_job(event_id):
    t = time.time()
    with LOCK, database() as db:
        db.execute("UPDATE print_jobs SET status='expired',updated_ts=? WHERE event_id=? AND status='pending' AND created_ts<?",
                   (t, event_id, t - JOB_MAX_AGE))
        job = db.execute("SELECT id,label,data FROM print_jobs WHERE event_id=? AND status='pending' ORDER BY created_ts LIMIT 1",
                         (event_id,)).fetchone()
        if job:
            db.execute("UPDATE print_jobs SET status='sent',updated_ts=? WHERE id=?", (t, job['id']))
    return job


def next_job(event_id, wait=25):
    """Long-poll: devolve a próxima etiqueta do evento, ou None ao fim de `wait` segundos."""
    deadline = time.time() + wait
    with JOBS_READY:
        while True:
            AGENTS[event_id] = time.time()
            job = claim_job(event_id)
            remaining = deadline - time.time()
            if job or remaining <= 0:
                return job
            JOBS_READY.wait(remaining)


def finish_job(event_id, job_id, error):
    with LOCK, database() as db:
        db.execute("UPDATE print_jobs SET status=?,error=?,updated_ts=? WHERE id=? AND event_id=? AND status='sent'",
                   ('error' if error else 'done', error[:300], time.time(), job_id, event_id))


def agent_online(event_id):
    return time.time() - AGENTS.get(event_id, 0) < AGENT_TIMEOUT


def printer_status(event_id):
    if PRINT_MODE == 'local':
        return {'mode': 'local'}
    with database() as db:
        pending = db.execute("SELECT COUNT(*) FROM print_jobs WHERE event_id=? AND status='pending' AND created_ts>=?",
                             (event_id, time.time() - JOB_MAX_AGE)).fetchone()[0]
        last = db.execute("SELECT status,error FROM print_jobs WHERE event_id=? AND status IN ('done','error') "
                          "ORDER BY updated_ts DESC LIMIT 1", (event_id,)).fetchone()
    return {'mode': 'agente', 'online': agent_online(event_id), 'pending': pending,
            'error': last['error'] if last and last['status'] == 'error' else ''}


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


def insert_people(db, event_id, people):
    t = now()
    db.executemany('INSERT INTO people(id,name,role,company,created_at,updated_at,source,event_id) VALUES (?,?,?,?,?,?,?,?)',
                   [(str(uuid.uuid4()), name, role, company, t, t, 'import', event_id) for name, role, company in people])


def import_people(event_id, blob, filename):
    """Acrescenta participantes novos; os existentes (e os seus check-ins) mantêm-se."""
    people, skipped = parse_people(blob, filename)
    with LOCK, database() as db:
        existing = {person_key(r['name'], r['company'])
                    for r in db.execute('SELECT name,company FROM people WHERE event_id=?', (event_id,))}
        new = [p for p in people if person_key(p[0], p[2]) not in existing]
        insert_people(db, event_id, new)
    return len(new), skipped + len(people) - len(new)


def replace_people(event_id, blob, filename, admin_password):
    """Substitui a lista (e os check-ins) deste evento, com cópia de segurança prévia."""
    require_admin_password(admin_password, 'Palavra-passe de administração incorreta. A lista não foi alterada.')
    people, skipped = parse_people(blob, filename)
    with LOCK:
        backup = backup_database('antes_substituir')
        with database() as db:
            old_count = db.execute('SELECT COUNT(*) FROM people WHERE event_id=?', (event_id,)).fetchone()[0]
            db.execute('DELETE FROM people WHERE event_id=?', (event_id,))
            insert_people(db, event_id, people)
    return old_count, len(people), skipped, backup.name


# ── Ações sobre participantes ────────────────────────────────────────────────

def person_action(action, event_id, data):
    pid = data.get('id')
    with LOCK, database() as db:
        old = db.execute('SELECT * FROM people WHERE id=? AND event_id=?', (pid, event_id)).fetchone() if pid else None
        if pid and not old:
            raise ValueError('Participante não encontrado neste evento. Atualiza a página.')
        t = now()

        if action == 'undo':
            if not old:
                raise ValueError('Seleciona um participante.')
            db.execute('UPDATE people SET checked_at=NULL,updated_at=? WHERE id=?', (t, pid))
            return {'message': 'Check-in anulado.', 'id': pid}

        name, role, company = safe_fields(data)

        if action == 'reprint':
            if not old:
                raise ValueError('Guarda primeiro o participante.')
            send_badge(db, event_id, name, role, company)
            db.execute('UPDATE people SET name=?,role=?,company=?,updated_at=? WHERE id=?', (name, role, company, t, pid))
            return {'message': 'Badge enviado para reimpressão.', 'id': pid}

        if action == 'checkin':
            if old and old['checked_at']:
                raise ValueError('Este participante já fez check-in. Usa Reimprimir badge.')
            send_badge(db, event_id, name, role, company)
            checked_at = t
        else:
            checked_at = old['checked_at'] if old else None

        if old:
            db.execute('UPDATE people SET name=?,role=?,company=?,checked_at=?,updated_at=? WHERE id=?',
                       (name, role, company, checked_at, t, pid))
        else:
            pid = str(uuid.uuid4())
            db.execute('INSERT INTO people(id,name,role,company,checked_at,created_at,updated_at,source,event_id) VALUES (?,?,?,?,?,?,?,?,?)',
                       (pid, name, role, company, checked_at, t, t, 'walk-in', event_id))
    message = 'Check-in registado e etiqueta enviada para a fila.' if action == 'checkin' else 'Dados guardados.'
    return {'message': message, 'id': pid}


# ── API HTTP ─────────────────────────────────────────────────────────────────

ROUTES = {}


def route(method, *paths):
    def register(fn):
        for path in paths:
            ROUTES[method, path] = fn
        return fn
    return register


@route('GET', '/api/session')
def api_session(h):
    sess = h.session
    event = get_event(sess['event_id']) if sess and sess['kind'] == 'event' else None
    return {'admin_configured': bool(admin_hash()),
            'is_admin': bool(sess and sess['kind'] == 'admin'),
            'event': public_event(event) if event else None}


@route('GET', '/api/events')
def api_events(h):
    return {'events': list_events()}


@route('GET', '/api/admin/events')
def api_admin_events(h):
    h.require('admin')
    return {'events': list_events_with_stats()}


@route('GET', '/api/people')
def api_people(h):
    event_id = h.require('event')['event_id']
    people = event_people(event_id)
    return {'people': people, 'total': len(people), 'checked': sum(bool(p['checked_at']) for p in people),
            'printer': printer_status(event_id)}


@route('GET', '/api/export')
def api_export(h):
    event_id = h.require('event')['event_id']
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';')
    writer.writerow(['Nome', 'Função', 'Empresa', 'Check-in', 'Data/hora', 'Origem'])
    for p in event_people(event_id):
        writer.writerow([p['name'], p['role'], p['company'], 'Sim' if p['checked_at'] else 'Não', p['checked_at'] or '', p['source']])
    slug = re.sub(r'[^a-z0-9]+', '_', norm(get_event(event_id)['name'])).strip('_') or 'evento'
    h.send(200, ('﻿' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8',
           [('Content-Disposition', f'attachment; filename="checkins_{slug}.csv"')])


@route('POST', '/api/logout')
def api_logout(h):
    with LOCK:
        SESSIONS.pop(h.token, None)
    return {'message': 'Sessão terminada.'}, ''


@route('POST', '/api/setup-admin')
def api_setup_admin(h):
    if PUBLIC_URL:
        # Online, quem abrisse o site primeiro ficaria com a administração.
        raise PermissionError('Na versão online, a palavra-passe de administração define-se no servidor: sudo marcaserio-admin')
    with LOCK:
        if admin_hash():
            raise PermissionError('Administração já configurada.')
        set_admin_password(str(h.data.get('password', '')))
    return {'message': 'Administração configurada.'}, new_session('admin')


@route('POST', '/api/login-admin')
def api_login_admin(h):
    require_admin_password(str(h.data.get('password', '')))
    return {'message': 'Autenticado.'}, new_session('admin')


@route('POST', '/api/login-event')
def api_login_event(h):
    event = get_event(str(h.data.get('event_id', '')))
    if event and event['password_hash'] == NO_PASSWORD:
        raise PermissionError('Este evento ainda não tem palavra-passe. Define-a em Administração de eventos.')
    if not event or not check_password(str(h.data.get('password', '')), event['password_hash']):
        raise PermissionError('Palavra-passe do evento incorreta.')
    return {'event': public_event(event)}, new_session('event', event['id'])


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
    event_id = str(uuid.uuid4())
    with LOCK, database() as db:
        db.execute('INSERT INTO events(id,name,event_date,password_hash,created_at) VALUES (?,?,?,?,?)',
                   (event_id, name, date, hash_password(password), now()))
    return {'message': 'Evento criado.', 'id': event_id}


@route('POST', '/api/admin/change-password')
def api_change_password(h):
    h.require('admin')
    event_id, password = str(h.data.get('event_id', '')), str(h.data.get('password', ''))
    if len(password) < EVENT_MIN_PASSWORD:
        raise ValueError(f'A palavra-passe deve ter pelo menos {EVENT_MIN_PASSWORD} caracteres.')
    with LOCK, database() as db:
        if not db.execute('UPDATE events SET password_hash=? WHERE id=?', (hash_password(password), event_id)).rowcount:
            raise ValueError('Evento não encontrado.')
    end_event_sessions(event_id)
    return {'message': 'Palavra-passe alterada. As sessões anteriores deste evento foram terminadas.'}


@route('POST', '/api/save', '/api/checkin', '/api/reprint', '/api/undo')
def api_person(h):
    action, event_id = h.path_only.rsplit('/', 1)[-1], h.require('event')['event_id']
    result = person_action(action, event_id, h.data)
    if PRINT_MODE != 'local' and action in ('checkin', 'reprint'):
        wake_agents()
        if not agent_online(event_id):
            result['message'] += ' Atenção: a impressora do evento está desligada; a etiqueta fica em fila durante 15 minutos.'
    return result


@route('GET', '/api/print/next')
def api_print_next(h):
    if PRINT_MODE == 'local':
        raise ValueError('Este servidor imprime diretamente (modo local).')
    job = next_job(h.require('event')['event_id'])
    return {'job': job and {'id': job['id'], 'label': job['label'], 'data': base64.b64encode(job['data']).decode()}}


@route('POST', '/api/print/result')
def api_print_result(h):
    finish_job(h.require('event')['event_id'], str(h.data.get('id', '')), str(h.data.get('error') or ''))
    return {'message': 'Registado.'}


@route('POST', '/api/import')
def api_import(h):
    count, skipped = import_people(h.require('event')['event_id'], h.body, h.filename)
    return {'message': f'{count} participantes importados. {skipped} ignorados (duplicados ou inválidos).'}


@route('POST', '/api/replace')
def api_replace(h):
    old_count, count, skipped, backup = replace_people(
        h.require('event')['event_id'], h.body, h.filename, h.headers.get('X-Admin-Password', ''))
    return {'message': f'Lista substituída: {old_count} anteriores removidos; {count} participantes importados; '
                       f'{skipped} linhas ignoradas. Cópia de segurança: copias_seguranca/{backup}.'}


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
                note_failure(self.client_ip)
            self.send_json({'error': str(exc)}, 403)
        except Exception as exc:
            self.send_json({'error': str(exc)}, 400)

    def read_body(self):
        # Apenas escritas da própria página (mesma origem), incluindo ficheiros importados.
        origin = self.headers.get('Origin', '')
        if origin and origin not in ORIGINS:
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

    @property
    def client_ip(self):
        # Atrás do Cloudflare Tunnel todas as ligações chegam de 127.0.0.1.
        return (TRUST_PROXY and self.headers.get('CF-Connecting-IP')) or self.client_address[0]

    @property
    def filename(self):
        return self.query.get('filename', [''])[0]

    @property
    def token(self):
        try:
            morsel = SimpleCookie(self.headers.get('Cookie', '')).get(SESSION_COOKIE)
        except CookieError:
            return None
        return morsel.value if morsel else None

    @property
    def session(self):
        return get_session(self.token)

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
    init_db()
    password = getpass.getpass('Nova palavra-passe de administração: ')
    if password != getpass.getpass('Confirmar: '):
        raise SystemExit('As palavras-passe não coincidem.')
    try:
        set_admin_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc))
    print('Palavra-passe de administração definida.')


def main():
    if '--definir-admin' in sys.argv[1:]:
        return prompt_admin_password()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        raise SystemExit(f'Não foi possível iniciar a porta {PORT}. Fecha a versão anterior com Control+C. ({exc})')
    url = f'http://{HOST}:{PORT}/'
    print(f'marcAsério | Check-in: {PUBLIC_URL or url} (impressão: {PRINT_MODE})', flush=True)
    print(f'Base de dados: {DB_PATH}', flush=True)
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
