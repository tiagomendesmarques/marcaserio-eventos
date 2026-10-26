"""Armazenamento em SQLite: modo local, num Mac ligado à impressora.

Um só processo, por isso um cadeado (LOCK) chega para garantir que dois check-ins
ao mesmo participante não passam os dois.
"""
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

LEGACY_EVENT_ID = 'legacy-people-finance'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


class AlreadyCheckedIn(ValueError):
    pass


class SqliteStore:
    """Mesma interface que store_firestore.FirestoreStore."""

    def __init__(self, db_path, legacy_db, backup_dir, no_password):
        self.db_path, self.legacy_db, self.backup_dir = db_path, legacy_db, backup_dir
        self.no_password = no_password
        self.lock = threading.RLock()
        self.failures = {}     # IP → instantes das tentativas falhadas
        self.agents = {}       # event_id → último contacto do agente

    # ── ligação e esquema ──

    @contextmanager
    def db(self):
        """Ligação numa transação: commit no fim, rollback em erro, fecho sempre."""
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _copy(source_uri, target):
        source, dest = sqlite3.connect(source_uri, uri=True), sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            source.close()
            dest.close()

    def init(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # A base de dados original da v2.6 nunca é editada: faz-se uma cópia de trabalho única.
        if not self.db_path.exists() and self.legacy_db.exists():
            self._copy(self.legacy_db.as_uri() + '?mode=ro', self.db_path)
        with self.db() as db:
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
                           (LEGACY_EVENT_ID, 'People & Finance Forum 2026', '2026-10-15', self.no_password, now()))
                db.execute('UPDATE people SET event_id=? WHERE event_id IS NULL', (LEGACY_EVENT_ID,))

    # ── definições ──

    def get_setting(self, key):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None

    def set_setting(self, key, value, only_if_absent=False):
        """Grava; com only_if_absent devolve False se já existia (e não altera)."""
        with self.lock, self.db() as db:
            verb = 'INSERT OR IGNORE' if only_if_absent else 'INSERT OR REPLACE'
            return db.execute(f'{verb} INTO settings(key,value) VALUES (?,?)', (key, value)).rowcount > 0

    # ── eventos ──

    def list_events(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT id,name,event_date FROM events ORDER BY event_date DESC,name')]

    def list_events_with_stats(self):
        with self.db() as db:
            rows = db.execute('''SELECT e.id, e.name, e.event_date, e.password_hash<>? AS has_password,
                                        COUNT(p.id) AS total, COUNT(p.checked_at) AS checked
                                 FROM events e LEFT JOIN people p ON p.event_id=e.id
                                 GROUP BY e.id ORDER BY e.event_date DESC, e.name''', (self.no_password,))
            return [dict(r, has_password=bool(r['has_password'])) for r in rows]

    def get_event(self, event_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        return dict(row, list_version=1) if row else None

    def create_event(self, name, event_date, password_hash):
        event_id = str(uuid.uuid4())
        with self.lock, self.db() as db:
            db.execute('INSERT INTO events(id,name,event_date,password_hash,created_at) VALUES (?,?,?,?,?)',
                       (event_id, name, event_date, password_hash, now()))
        return event_id

    def set_event_password(self, event_id, password_hash):
        with self.lock, self.db() as db:
            return db.execute('UPDATE events SET password_hash=? WHERE id=?', (password_hash, event_id)).rowcount > 0

    # ── participantes ──

    def people(self, event, version=None, since=None):
        """Devolve (versão da lista, lista completa?, participantes). Localmente é sempre completa."""
        with self.db() as db:
            rows = db.execute('SELECT * FROM people WHERE event_id=? ORDER BY name COLLATE NOCASE,id', (event['id'],))
            return 1, True, [dict(r) for r in rows]

    def person_keys(self, event):
        with self.db() as db:
            return [(r['name'], r['company']) for r in db.execute('SELECT name,company FROM people WHERE event_id=?', (event['id'],))]

    def _insert(self, db, event_id, people):
        t = now()
        db.executemany('INSERT INTO people(id,name,role,company,created_at,updated_at,source,event_id) VALUES (?,?,?,?,?,?,?,?)',
                       [(str(uuid.uuid4()), name, role, company, t, t, 'import', event_id) for name, role, company in people])

    def add_people(self, event, people):
        with self.lock, self.db() as db:
            self._insert(db, event['id'], people)

    def replace_people(self, event, people):
        """Substitui a lista do evento. Devolve (n.º anterior, descrição da cópia de segurança)."""
        with self.lock:
            self.backup_dir.mkdir(exist_ok=True)
            backup = self.backup_dir / f'antes_substituir_{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(4)}.sqlite3'
            self._copy(self.db_path.as_uri(), backup)
            with self.db() as db:
                old_count = db.execute('SELECT COUNT(*) FROM people WHERE event_id=?', (event['id'],)).fetchone()[0]
                db.execute('DELETE FROM people WHERE event_id=?', (event['id'],))
                self._insert(db, event['id'], people)
        return old_count, f'copias_seguranca/{backup.name}'

    def person_action(self, event, action, pid, fields, badge):
        """save / checkin / reprint / undo. `badge` imprime ou devolve bytes para a fila."""
        event_id = event['id']
        with self.lock, self.db() as db:
            old = db.execute('SELECT * FROM people WHERE id=? AND event_id=?', (pid, event_id)).fetchone() if pid else None
            if pid and not old:
                raise ValueError('Participante não encontrado neste evento. Atualiza a página.')
            t = now()

            if action == 'undo':
                if not old:
                    raise ValueError('Seleciona um participante.')
                db.execute('UPDATE people SET checked_at=NULL,updated_at=? WHERE id=?', (t, pid))
                return pid

            name, role, company = fields
            if action == 'reprint':
                if not old:
                    raise ValueError('Guarda primeiro o participante.')
                self._queue(db, event_id, name, badge(name, role, company))
                db.execute('UPDATE people SET name=?,role=?,company=?,updated_at=? WHERE id=?', (name, role, company, t, pid))
                return pid

            if action == 'checkin':
                if old and old['checked_at']:
                    raise AlreadyCheckedIn('Este participante já fez check-in. Usa Reimprimir badge.')
                self._queue(db, event_id, name, badge(name, role, company))
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
            return pid

    # ── fila de impressão ──

    @staticmethod
    def _queue(db, event_id, label, data):
        if data is None:  # impresso diretamente (modo local)
            return
        t = time.time()
        db.execute('INSERT INTO print_jobs(id,event_id,label,data,created_ts,updated_ts) VALUES (?,?,?,?,?,?)',
                   (str(uuid.uuid4()), event_id, label, data, t, t))

    def claim_job(self, event, max_age):
        t = time.time()
        with self.lock, self.db() as db:
            db.execute("UPDATE print_jobs SET status='expired',updated_ts=? WHERE event_id=? AND status='pending' AND created_ts<?",
                       (t, event['id'], t - max_age))
            job = db.execute("SELECT id,label,data FROM print_jobs WHERE event_id=? AND status='pending' ORDER BY created_ts LIMIT 1",
                             (event['id'],)).fetchone()
            if job:
                db.execute("UPDATE print_jobs SET status='sent',updated_ts=? WHERE id=?", (t, job['id']))
        return dict(job) if job else None

    def finish_job(self, event, job_id, error):
        with self.lock, self.db() as db:
            db.execute("UPDATE print_jobs SET status=?,error=?,updated_ts=? WHERE id=? AND event_id=? AND status='sent'",
                       ('error' if error else 'done', error, time.time(), job_id, event['id']))

    def printer_info(self, event, max_age):
        """Devolve (etiquetas em fila, erro da última impressão, último contacto do agente)."""
        with self.db() as db:
            pending = db.execute("SELECT COUNT(*) FROM print_jobs WHERE event_id=? AND status='pending' AND created_ts>=?",
                                 (event['id'], time.time() - max_age)).fetchone()[0]
            last = db.execute("SELECT status,error FROM print_jobs WHERE event_id=? AND status IN ('done','error') "
                              "ORDER BY updated_ts DESC LIMIT 1", (event['id'],)).fetchone()
        error = last['error'] if last and last['status'] == 'error' else ''
        return pending, error, self.agents.get(event['id'], 0)

    def agent_seen(self, event):
        self.agents[event['id']] = time.time()

    # ── tentativas de login ──

    def recent_failures(self, ip, window):
        with self.lock:
            recent = [t for t in self.failures.get(ip, []) if t > time.time() - window]
            self.failures[ip] = recent
            return len(recent)

    def note_failure(self, ip):
        with self.lock:
            self.failures.setdefault(ip, []).append(time.time())
