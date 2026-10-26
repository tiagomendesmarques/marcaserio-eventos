#!/usr/bin/env python3
"""marcAsério | Check-in — acreditação de eventos, multi-evento.

Contas com três perfis:
  • administrador: tudo, incluindo utilizadores e registos;
  • editor: eventos, listas e hospedeiras (qualquer conta Google @marcaserio.pt entra como editor);
  • hospedeira: só check-ins nos eventos atribuídos (entra com email e palavra-passe).

Corre de duas formas, escolhidas por variáveis de ambiente:
  • local (por omissão): num Mac ligado à Honeywell, dados em SQLite;
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
import urllib.request
import webbrowser
import zipfile
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse
from xml.etree import ElementTree as ET

from store_sqlite import AlreadyCheckedIn, SqliteStore, now

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
VERSION = os.environ.get('MA_VERSION', 'local')                # commit publicado (definido no deploy)
DATA_DIR = Path(os.environ.get('MA_DATA_DIR', APP_DIR))

# Contas: administradores de raiz e login com Google (só a organização marcaserio.pt).
BOOTSTRAP_ADMINS = [e.strip().lower() for e in os.environ.get(
    'MA_ADMINS', 'tiago.marques@marcaserio.pt,miguel.romao@marcaserio.pt').split(',') if e.strip()]
GOOGLE_DOMAIN = os.environ.get('MA_GOOGLE_DOMAIN', 'marcaserio.pt')
GOOGLE_CLIENT_ID = os.environ.get('MA_GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.environ.get('MA_GOOGLE_CLIENT_SECRET', '').strip()
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = os.environ.get('MA_GOOGLE_TOKEN_URL', 'https://oauth2.googleapis.com/token')
GOOGLE_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and PUBLIC_URL)

PRINTER = 'Honeywell_PC42d_203_FP'
WIDTH, HEIGHT = 560, 320

LOCAL_ORIGIN = f'http://127.0.0.1:{PORT}'
SECURE_COOKIE = PUBLIC_URL.startswith('https://')
SESSION_COOKIE, OAUTH_COOKIE = 'ma_session', 'ma_oauth'
SESSION_TTL = 12 * 3600
MAX_UPLOAD = 12_000_000
MAX_ROWS = 20_000
MAX_NAME, MAX_TEXT = 150, 180
MIN_PASSWORD = 8
LOGIN_WINDOW, LOGIN_MAX_FAILURES = 15 * 60, 10
LOG_LIMIT = 500
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

ROLES = {'hostess': 0, 'editor': 1, 'admin': 2}
ROLE_LABELS = {'hostess': 'Hospedeira', 'editor': 'Editor', 'admin': 'Administrador'}

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
        STORE = FirestoreStore(os.environ.get('GOOGLE_CLOUD_PROJECT'), FIRESTORE_DB)
    else:
        STORE = SqliteStore(DATA_DIR / 'marcaserio_checkin.sqlite3', DATA_DIR / 'checkin_people_finance.sqlite3',
                            DATA_DIR / 'copias_seguranca')
    STORE.init()
    for email in BOOTSTRAP_ADMINS:
        if not STORE.get_user(email):
            STORE.save_user(new_user(email, 'admin'))
    return STORE


def public_event(event):
    return {'id': event['id'], 'name': event['name'], 'event_date': event.get('event_date', '')}


# ── Contas ───────────────────────────────────────────────────────────────────

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


def normalize_email(value):
    email = str(value or '').strip().lower()
    if len(email) > 200 or not EMAIL_RE.match(email):
        raise ValueError('Email inválido.')
    return email


def check_new_password(password):
    if len(password) < MIN_PASSWORD:
        raise ValueError(f'A palavra-passe deve ter pelo menos {MIN_PASSWORD} caracteres.')
    return password


def new_user(email, role, password='', events=(), created_by='sistema'):
    return {'email': email, 'role': role, 'password_hash': hash_password(password) if password else '',
            'events': list(events), 'sv': 1, 'created_at': now(), 'created_by': created_by, 'last_login': ''}


def is_company_email(email):
    return email.endswith('@' + GOOGLE_DOMAIN)


def at_least(user, role):
    return ROLES.get(user['role'], -1) >= ROLES[role]


def can_access(user, event_id):
    return at_least(user, 'editor') or event_id in user.get('events', [])


def setup_needed():
    """Só na versão local: ninguém consegue entrar enquanto não houver um administrador com palavra-passe."""
    return not PUBLIC_URL and not any(u['password_hash'] for u in STORE.list_users() if u['role'] == 'admin')


def check_attempts(ip):
    if STORE.recent_failures(ip, LOGIN_WINDOW) >= LOGIN_MAX_FAILURES:
        raise PermissionError('Demasiadas tentativas falhadas. Tenta novamente dentro de 15 minutos.')


# ── Sessões ──────────────────────────────────────────────────────────────────
# Cookies assinados (nada fica em memória), para funcionarem com vários contentores.
# Levam a «versão de sessão» do utilizador: mudar-lhe a palavra-passe termina-as.
# O perfil e os eventos são lidos a cada pedido, por isso as alterações contam logo.

def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def session_secret():
    global _SECRET
    if not _SECRET:
        STORE.set_setting('session_secret', secrets.token_hex(32), only_if_absent=True)
        _SECRET = STORE.get_setting('session_secret').encode()
    return _SECRET


def sign(data):
    payload = _b64(json.dumps(data).encode())
    return f'{payload}.{_b64(hmac.new(session_secret(), payload.encode(), "sha256").digest())}'


def unsign(token):
    """Devolve os dados de um token assinado e não expirado, ou None."""
    try:
        payload, signature = token.split('.')
        expected = _b64(hmac.new(session_secret(), payload.encode(), 'sha256').digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        return data if data['x'] >= time.time() else None
    except (AttributeError, KeyError, ValueError, TypeError):
        return None


def new_session(user, event_id=None):
    return sign({'u': user['email'], 's': user['sv'], 'e': event_id, 'x': int(time.time() + SESSION_TTL)})


def read_session(token):
    data = unsign(token)
    if not data:
        return None
    user = STORE.get_user(data.get('u'))
    if not user or user.get('sv') != data.get('s'):
        return None
    event_id = data.get('e')
    event = STORE.get_event(event_id) if event_id and can_access(user, event_id) else None
    return {'user': user, 'event': event}


# ── Login com Google (OpenID Connect) ────────────────────────────────────────

def google_redirect_uri():
    return PUBLIC_URL + '/auth/google/callback'


def google_login_url(state):
    return GOOGLE_AUTH_URL + '?' + urlencode({
        'client_id': GOOGLE_CLIENT_ID, 'redirect_uri': google_redirect_uri(), 'response_type': 'code',
        'scope': 'openid email', 'state': state, 'hd': GOOGLE_DOMAIN, 'prompt': 'select_account'})


def check_google_claims(claims):
    """Valida o id_token. Só contas da organização Google Workspace (campo hd), não apenas o email."""
    if (claims.get('aud') != GOOGLE_CLIENT_ID or claims.get('exp', 0) < time.time()
            or claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com')):
        raise PermissionError('Resposta do Google inválida. Tenta outra vez.')
    email = str(claims.get('email', '')).lower()
    if not claims.get('email_verified') or claims.get('hd') != GOOGLE_DOMAIN or not is_company_email(email):
        raise PermissionError(f'Só as contas Google @{GOOGLE_DOMAIN} podem entrar com o Google.')
    return email


def google_email(code):
    # O id_token chega numa ligação direta (TLS) ao Google, por isso não é preciso verificar a assinatura.
    body = urlencode({'code': code, 'client_id': GOOGLE_CLIENT_ID, 'client_secret': GOOGLE_CLIENT_SECRET,
                      'redirect_uri': google_redirect_uri(), 'grant_type': 'authorization_code'}).encode()
    with urllib.request.urlopen(urllib.request.Request(GOOGLE_TOKEN_URL, data=body), timeout=15) as res:
        id_token = json.loads(res.read())['id_token']
    return check_google_claims(json.loads(_unb64(id_token.split('.')[1])))


# ── Registos ─────────────────────────────────────────────────────────────────

def audit(actor, action, detail='', event=None, ip=''):
    try:
        STORE.log({'ts': time.time(), 'at': now(), 'actor': actor or '', 'action': action,
                   'event_id': event['id'] if event else '', 'event_name': event['name'] if event else '',
                   'detail': str(detail)[:500], 'ip': ip or ''})
    except Exception as exc:  # um registo falhado não deve impedir a ação
        print(f'Erro ao gravar registo «{action}»: {exc}', file=sys.stderr, flush=True)


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


def replace_people(event, blob, filename):
    """Substitui a lista (e os check-ins) deste evento, com cópia de segurança prévia."""
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


def me(user):
    return {'email': user['email'], 'role': user['role'], 'role_label': ROLE_LABELS[user['role']],
            'can_manage': at_least(user, 'editor'), 'is_admin': at_least(user, 'admin')}


def accessible_events(user):
    return [e for e in STORE.list_events() if can_access(user, e['id'])]


@route('GET', '/api/estado')
def api_estado(h):
    """Verificação depois de cada deploy: versão no ar e base de dados a responder."""
    STORE.get_setting('session_secret')  # confirma que a base de dados responde
    return {'ok': True, 'version': VERSION, 'dados': STORE_KIND, 'impressao': PRINT_MODE, 'google': GOOGLE_ENABLED}


@route('GET', '/api/session')
def api_session(h):
    sess = h.session
    return {'me': me(sess['user']) if sess else None,
            'event': public_event(sess['event']) if sess and sess['event'] else None,
            'google': GOOGLE_ENABLED, 'domain': GOOGLE_DOMAIN, 'setup_needed': setup_needed()}


# ── entrar e sair ──

def finish_login(h, user, method):
    user['last_login'] = now()
    STORE.save_user(user)
    h.audit(f'Entrada ({method})', actor=user['email'])
    return new_session(user)


@route('POST', '/api/setup')
def api_setup(h):
    if not setup_needed():
        raise PermissionError('A administração já está configurada.')
    email = normalize_email(h.data.get('email'))
    password = check_new_password(str(h.data.get('password', '')))
    user = STORE.get_user(email) or new_user(email, 'admin')
    user.update(role='admin', password_hash=hash_password(password), sv=user.get('sv', 0) + 1)
    STORE.save_user(user)
    h.audit('Conta de administração criada', email, actor=email)
    return {'me': me(user)}, finish_login(h, user, 'palavra-passe')


@route('POST', '/api/login')
def api_login(h):
    email = str(h.data.get('email', '')).strip().lower()
    user = STORE.get_user(email) if email else None
    if not user or not user['password_hash'] or not check_password(str(h.data.get('password', '')), user['password_hash']):
        h.audit('Entrada falhada', 'email ou palavra-passe incorretos', actor=email[:200])
        raise PermissionError('Email ou palavra-passe incorretos.')
    return {'me': me(user)}, finish_login(h, user, 'palavra-passe')


@route('GET', '/auth/google')
def auth_google(h):
    if not GOOGLE_ENABLED:
        return h.redirect('/?erro=' + quote('O login com Google não está configurado.'))
    nonce = secrets.token_urlsafe(16)
    state = sign({'n': nonce, 'x': int(time.time() + 600)})
    h.redirect(google_login_url(state), [h.cookie(OAUTH_COOKIE, nonce, 600, samesite='Lax', path='/auth')])


@route('GET', '/auth/google/callback')
def auth_google_callback(h):
    clear = h.cookie(OAUTH_COOKIE, '', 0, samesite='Lax', path='/auth')
    try:
        state = unsign(h.param('state'))
        if not GOOGLE_ENABLED or not state or not hmac.compare_digest(state['n'], h.cookies.get(OAUTH_COOKIE, '')):
            raise PermissionError('O pedido de login expirou. Tenta outra vez.')
        if h.param('error') or not h.param('code'):
            raise PermissionError('Login com Google cancelado.')
        email = google_email(h.param('code'))
        user = STORE.get_user(email)
        if not user:  # contas @marcaserio.pt entram como editores
            user = new_user(email, 'admin' if email in BOOTSTRAP_ADMINS else 'editor', created_by='Google')
            STORE.save_user(user)
            h.audit('Utilizador criado', f'{email} · {ROLE_LABELS[user["role"]]} (primeiro login com Google)', actor=email)
        token = finish_login(h, user, 'Google')
    except Exception as exc:
        if not isinstance(exc, PermissionError):
            print(f'Erro no login com Google: {exc}', file=sys.stderr, flush=True)
        h.audit('Entrada falhada', f'Google: {exc}')
        return h.redirect('/?erro=' + quote(str(exc) if isinstance(exc, PermissionError) else 'Não foi possível entrar com o Google.'), [clear])
    h.redirect('/', [clear, h.cookie(SESSION_COOKIE, token, SESSION_TTL)])


@route('POST', '/api/logout')
def api_logout(h):
    if h.session:
        h.audit('Saída')
    return {'message': 'Sessão terminada.'}, ''


# ── escolher o evento ──

@route('GET', '/api/my-events')
def api_my_events(h):
    return {'events': accessible_events(h.require()['user'])}


@route('POST', '/api/select-event')
def api_select_event(h):
    user = h.require()['user']
    event_id = str(h.data.get('event_id', ''))
    event = STORE.get_event(event_id) if can_access(user, event_id) else None
    if not event:
        raise PermissionError('Não tens acesso a este evento.')
    return {'event': public_event(event)}, new_session(user, event_id)


@route('POST', '/api/leave-event')
def api_leave_event(h):
    return {'message': 'OK'}, new_session(h.require()['user'])


# ── gestão: eventos ──

@route('GET', '/api/manage/events')
def api_manage_events(h):
    h.require('editor')
    return {'events': STORE.list_events_with_stats()}


@route('POST', '/api/manage/events/create')
def api_create_event(h):
    h.require('editor')
    name = str(h.data.get('name', '')).strip()
    date = str(h.data.get('event_date', '')).strip()
    if not name or len(name) > 130:
        raise ValueError('Indica um nome válido para o evento.')
    if date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        raise ValueError('Data inválida.')
    event_id = STORE.create_event(name, date)
    h.audit('Evento criado', date, event={'id': event_id, 'name': name})
    return {'message': 'Evento criado.', 'id': event_id}


# ── gestão: utilizadores ──

def user_out(user, event_names):
    return {'email': user['email'], 'role': user['role'], 'role_label': ROLE_LABELS[user['role']],
            'events': user.get('events', []), 'event_names': [event_names.get(e, '?') for e in user.get('events', [])],
            'has_password': bool(user['password_hash']), 'google': GOOGLE_ENABLED and is_company_email(user['email']),
            'last_login': user.get('last_login', ''), 'created_by': user.get('created_by', '')}


@route('GET', '/api/manage/users')
def api_users(h):
    h.require('editor')
    names = {e['id']: e['name'] for e in STORE.list_events()}
    return {'users': [user_out(u, names) for u in STORE.list_users()], 'roles': ROLE_LABELS}


def check_can_edit(actor, target_role, existing):
    if not at_least(actor, 'admin') and (target_role != 'hostess' or (existing and existing['role'] != 'hostess')):
        raise PermissionError('Só um administrador pode gerir administradores e editores.')


def check_keeps_an_admin(existing, new_role=None):
    if existing and existing['role'] == 'admin' and new_role != 'admin':
        if sum(1 for u in STORE.list_users() if u['role'] == 'admin') <= 1:
            raise ValueError('Tem de ficar pelo menos um administrador.')


@route('POST', '/api/manage/users/save')
def api_user_save(h):
    actor = h.require('editor')['user']
    email = normalize_email(h.data.get('email'))
    role = str(h.data.get('role', 'hostess'))
    if role not in ROLES:
        raise ValueError('Perfil inválido.')
    password = str(h.data.get('password') or '')
    events = sorted({str(e) for e in h.data.get('events') or []}) if role == 'hostess' else []
    names = {e['id']: e['name'] for e in STORE.list_events()}
    if any(e not in names for e in events):
        raise ValueError('Evento inválido. Atualiza a página.')

    existing = STORE.get_user(email)
    check_can_edit(actor, role, existing)
    check_keeps_an_admin(existing, role)
    if password:
        check_new_password(password)
    elif not existing and not (GOOGLE_ENABLED and is_company_email(email)):
        raise ValueError(f'Define uma palavra-passe: só as contas @{GOOGLE_DOMAIN} podem entrar com o Google.')
    if role == 'hostess' and not events:
        raise ValueError('Escolhe pelo menos um evento para a hospedeira.')

    user = existing or new_user(email, role, created_by=actor['email'])
    changes = []
    if existing and existing['role'] != role:
        changes.append(f'perfil {ROLE_LABELS[existing["role"]]} → {ROLE_LABELS[role]}')
    if existing and sorted(existing.get('events', [])) != events:
        changes.append('eventos alterados')
    if password:
        user.update(password_hash=hash_password(password), sv=user['sv'] + (1 if existing else 0))
        if existing:
            changes.append('palavra-passe alterada')
    user.update(role=role, events=events)
    STORE.save_user(user)

    detail = f'{email} · {ROLE_LABELS[role]}'
    if events:
        detail += ' · ' + ', '.join(names[e] for e in events)
    if changes:
        detail += ' (' + '; '.join(changes) + ')'
    h.audit('Utilizador alterado' if existing else 'Utilizador criado', detail)
    return {'message': 'Utilizador guardado.' if existing else 'Utilizador criado.'}


@route('POST', '/api/manage/users/delete')
def api_user_delete(h):
    actor = h.require('editor')['user']
    email = normalize_email(h.data.get('email'))
    existing = STORE.get_user(email)
    if not existing:
        raise ValueError('Utilizador não encontrado.')
    if email == actor['email']:
        raise ValueError('Não podes remover a tua própria conta.')
    check_can_edit(actor, existing['role'], existing)
    check_keeps_an_admin(existing)
    STORE.delete_user(email)
    h.audit('Utilizador removido', f'{email} · {ROLE_LABELS[existing["role"]]}')
    return {'message': 'Utilizador removido.'}


# ── gestão: registos ──

@route('GET', '/api/manage/logs')
def api_logs(h):
    h.require('admin')
    return {'logs': STORE.recent_logs(LOG_LIMIT), 'limit': LOG_LIMIT}


# ── evento ──

@route('GET', '/api/people')
def api_people(h):
    """Lista do evento. Com ?v=<versão>&since=<instante>, só o que mudou desde então."""
    sess = h.require(event=True)
    version, full, people = STORE.people(sess['event'], h.param('v'), h.param('since'))
    return {'version': version, 'full': full, 'people': people, 'now': time.time(),
            'printer': printer_status(sess['event']), 'can_import': at_least(sess['user'], 'editor')}


@route('GET', '/api/export')
def api_export(h):
    event = h.require(event=True)['event']
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';')
    writer.writerow(['Nome', 'Função', 'Empresa', 'Check-in', 'Data/hora', 'Origem'])
    people = STORE.people(event)[2]
    for p in people:
        writer.writerow([p['name'], p['role'], p['company'], 'Sim' if p['checked_at'] else 'Não', p['checked_at'] or '', p['source']])
    h.audit('CSV exportado', f'{len(people)} participantes', event=event)
    slug = re.sub(r'[^a-z0-9]+', '_', norm(event['name'])).strip('_') or 'evento'
    h.send(200, ('﻿' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8',
           [('Content-Disposition', f'attachment; filename="checkins_{slug}.csv"')])


@route('POST', '/api/save', '/api/checkin', '/api/reprint', '/api/undo')
def api_person(h):
    action, event = h.path_only.rsplit('/', 1)[-1], h.require(event=True)['event']
    pid = str(h.data.get('id') or '') or None
    fields = None if action == 'undo' else safe_fields(h.data)
    new_pid = STORE.person_action(event, action, pid, fields, badge)
    message = PERSON_MESSAGES[action]
    if PRINT_MODE != 'local' and action in ('checkin', 'reprint'):
        wake_agents()
        if not printer_status(event)['online']:
            message += ' Atenção: a impressora do evento está desligada; a etiqueta fica em fila durante 15 minutos.'
    label = {'save': 'Participante editado' if pid else 'Walk-in criado',
             'checkin': 'Check-in' if pid else 'Check-in (walk-in)',
             'reprint': 'Badge reimpresso', 'undo': 'Check-in anulado'}[action]
    h.audit(label, fields[0] if fields else str(h.data.get('name') or ''), event=event)
    return {'message': message, 'id': new_pid}


@route('GET', '/api/print/next')
def api_print_next(h):
    if PRINT_MODE == 'local':
        raise ValueError('Este servidor imprime diretamente (modo local).')
    job = next_job(h.require(event=True)['event'])
    return {'job': job and {'id': job['id'], 'label': job['label'], 'data': base64.b64encode(job['data']).decode()}}


@route('POST', '/api/print/result')
def api_print_result(h):
    event = h.require(event=True)['event']
    error = str(h.data.get('error') or '')[:300]
    STORE.finish_job(event, str(h.data.get('id', '')), error)
    if error:
        h.audit('Erro de impressão', error, event=event)
    return {'message': 'Registado.'}


@route('POST', '/api/import')
def api_import(h):
    event = h.require('editor', event=True)['event']
    count, skipped = import_people(event, h.body, h.param('filename'))
    h.audit('Lista importada', f'{count} novos, {skipped} ignorados ({h.param("filename")})', event=event)
    return {'message': f'{count} participantes importados. {skipped} ignorados (duplicados ou inválidos).'}


@route('POST', '/api/replace')
def api_replace(h):
    event = h.require('editor', event=True)['event']
    old_count, count, skipped, backup = replace_people(event, h.body, h.param('filename'))
    h.audit('Lista substituída', f'{old_count} → {count} participantes ({h.param("filename")}); cópia: {backup}', event=event)
    return {'message': f'Lista substituída: {old_count} anteriores removidos; {count} participantes importados; '
                       f'{skipped} linhas ignoradas. Cópia de segurança: {backup}.'}


PASSWORD_ROUTES = {'/api/setup', '/api/login'}
_UNSET = object()


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
        self.path_only, self.query, self._session = url.path, parse_qs(url.query), _UNSET
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
    def cookies(self):
        try:
            return {k: m.value for k, m in SimpleCookie(self.headers.get('Cookie', '')).items()}
        except CookieError:
            return {}

    @property
    def session(self):
        if self._session is _UNSET:
            token = self.cookies.get(SESSION_COOKIE)
            self._session = read_session(token) if token else None
        return self._session

    def require(self, role=None, event=False):
        sess = self.session
        if not sess:
            raise PermissionError('Sessão terminada. Entra novamente.')
        if role and not at_least(sess['user'], role):
            raise PermissionError('Não tens permissão para esta ação.')
        if event and not sess['event']:
            raise PermissionError('Escolhe primeiro um evento.')
        return sess

    def audit(self, action, detail='', event=None, actor=None):
        sess = self.session if actor is None else None
        audit(actor if actor is not None else (sess['user']['email'] if sess else ''), action, detail, event, self.client_ip)

    def cookie(self, name, value, max_age, samesite='Strict', path='/'):
        secure = '; Secure' if SECURE_COOKIE else ''
        return ('Set-Cookie', f'{name}={value}; HttpOnly; SameSite={samesite}; Path={path}; Max-Age={max_age}{secure}')

    def redirect(self, location, headers=()):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()

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
        headers = [] if cookie is None else [self.cookie(SESSION_COOKIE, cookie, SESSION_TTL if cookie else 0)]
        self.send(status, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', headers)


def prompt_admin_password():
    """Cria um administrador com palavra-passe, ou repõe a de um existente, a partir do terminal."""
    try:
        email = normalize_email(input('Email do administrador: '))
        password = getpass.getpass('Nova palavra-passe: ')
        if password != getpass.getpass('Confirmar: '):
            raise SystemExit('As palavras-passe não coincidem.')
        check_new_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc))
    user = STORE.get_user(email) or new_user(email, 'admin', created_by='terminal')
    user.update(role='admin', password_hash=hash_password(password), sv=user['sv'] + 1)
    STORE.save_user(user)
    audit('terminal', 'Utilizador alterado', f'{email} · Administrador (palavra-passe definida no terminal)')
    print(f'Administrador {email} com palavra-passe definida.')


def main():
    open_store()
    if '--definir-admin' in sys.argv[1:]:
        return prompt_admin_password()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        raise SystemExit(f'Não foi possível iniciar a porta {PORT}. Fecha a versão anterior com Control+C. ({exc})')
    url = f'http://{HOST}:{PORT}/'
    login = 'Google + palavra-passe' if GOOGLE_ENABLED else 'palavra-passe'
    print(f'marcAsério | Check-in: {PUBLIC_URL or url} (dados: {STORE_KIND}, impressão: {PRINT_MODE}, login: {login})', flush=True)
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
