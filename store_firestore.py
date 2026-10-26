"""Armazenamento em Firestore: versão online (Cloud Run), com vários contentores em paralelo.

Estrutura (base de dados própria, separada de qualquer outra do projeto):

  settings/{chave}                                  {value}
  users/{email}                                     perfil, palavra-passe (hash), eventos, …
  audit/{id}                                        registos (apagados ao fim de 12 meses, por TTL)
  events/{evento}                                   nome, data, list_version, …
  events/{evento}/lists/{versão}                    {created_at, count}
  events/{evento}/lists/{versão}/people/{pessoa}    nome, função, empresa, check-in, …
  events/{evento}/print_jobs/{etiqueta}             fila de impressão do evento
  login_failures/{hash do IP}                       tentativas falhadas recentes

«Substituir lista» escreve a lista nova numa versão nova e só depois muda
list_version, de uma vez. A lista anterior fica guardada como cópia de segurança.
O check-in é uma transação: duas hospedeiras ao mesmo participante, ganha uma.
"""
import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone

from google.api_core.exceptions import Conflict
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from store_sqlite import AlreadyCheckedIn, now

BATCH = 400               # máximo de 500 operações por lote no Firestore
HEARTBEAT_INTERVAL = 15   # segundos entre registos de contacto do agente (por contentor)
AUDIT_RETENTION = timedelta(days=365)  # campo expire_at, usado pela política de TTL


def _count(query):
    return query.count().get()[0][0].value


class FirestoreStore:
    def __init__(self, project, database):
        self.client = firestore.Client(project=project or None, database=database)
        self.heartbeats = {}  # event_id → último registo de contacto feito por este contentor

    def init(self):
        pass  # o Firestore não precisa de esquema

    # ── caminhos ──

    def _event_ref(self, event_id):
        return self.client.collection('events').document(event_id)

    def _people(self, event, version=None):
        version = version or event['list_version']
        return self._event_ref(event['id']).collection('lists').document(str(version)).collection('people')

    def _jobs(self, event):
        return self._event_ref(event['id']).collection('print_jobs')

    # ── definições ──

    def get_setting(self, key):
        snap = self.client.collection('settings').document(key).get()
        return snap.get('value') if snap.exists else None

    def set_setting(self, key, value, only_if_absent=False):
        ref = self.client.collection('settings').document(key)
        if not only_if_absent:
            ref.set({'value': value})
            return True
        try:
            ref.create({'value': value})  # falha se já existir, mesmo em paralelo
            return True
        except Conflict:
            return False

    # ── eventos ──

    def _events(self):
        events = [dict(s.to_dict(), id=s.id) for s in self.client.collection('events').stream()]
        events.sort(key=lambda e: e['name'].casefold())
        events.sort(key=lambda e: e.get('event_date', ''), reverse=True)
        return events

    def list_events(self):
        return [{'id': e['id'], 'name': e['name'], 'event_date': e.get('event_date', '')} for e in self._events()]

    def list_events_with_stats(self):
        result = []
        for e in self._events():
            people = self._people(e)
            result.append({'id': e['id'], 'name': e['name'], 'event_date': e.get('event_date', ''),
                           'total': _count(people),
                           'checked': _count(people.where(filter=FieldFilter('checked', '==', True)))})
        return result

    def get_event(self, event_id):
        if not event_id:
            return None
        snap = self._event_ref(event_id).get()
        return dict(snap.to_dict(), id=snap.id) if snap.exists else None

    def create_event(self, name, event_date, event_id=None, created_at=None):
        event_id = event_id or str(uuid.uuid4())
        self._event_ref(event_id).set({'name': name, 'event_date': event_date, 'created_at': created_at or now(),
                                       'list_version': 1, 'agent_seen_ts': 0, 'last_print_error': ''})
        return event_id

    # ── utilizadores ──

    def get_user(self, email):
        snap = self.client.collection('users').document(email).get() if email else None
        return dict(snap.to_dict(), email=snap.id) if snap and snap.exists else None

    def list_users(self):
        return sorted((dict(s.to_dict(), email=s.id) for s in self.client.collection('users').stream()), key=lambda u: u['email'])

    def save_user(self, user):
        data = {k: v for k, v in user.items() if k != 'email'}
        self.client.collection('users').document(user['email']).set(data)

    def delete_user(self, email):
        self.client.collection('users').document(email).delete()

    # ── registos ──

    def log(self, entry):
        self.client.collection('audit').add(dict(entry, expire_at=datetime.now(timezone.utc) + AUDIT_RETENTION))

    def recent_logs(self, limit):
        query = self.client.collection('audit').order_by('ts', direction=firestore.Query.DESCENDING).limit(limit)
        return [{k: v for k, v in s.to_dict().items() if k != 'expire_at'} for s in query.stream()]

    # ── participantes ──

    @staticmethod
    def _person_out(snap):
        d = snap.to_dict()
        return {'id': snap.id, 'name': d['name'], 'role': d.get('role', ''), 'company': d.get('company', ''),
                'checked_at': d.get('checked_at'), 'created_at': d.get('created_at'),
                'updated_at': d.get('updated_at'), 'source': d.get('source', '')}

    def people(self, event, version=None, since=None):
        """Devolve (versão, completa?, participantes). Com `since`, só os alterados desde então."""
        current = event['list_version']
        col = self._people(event)
        if since and str(version) == str(current):
            snaps = col.where(filter=FieldFilter('updated_ts', '>', float(since))).stream()
            return current, False, [self._person_out(s) for s in snaps]
        people = [self._person_out(s) for s in col.stream()]
        people.sort(key=lambda p: (p['name'].casefold(), p['id']))
        return current, True, people

    def person_keys(self, event):
        return [(s.get('name'), s.get('company')) for s in self._people(event).select(['name', 'company']).stream()]

    @staticmethod
    def _new_person(name, role, company, source, checked_at=None, created_at=None):
        t = now()
        return {'name': name, 'role': role, 'company': company, 'checked_at': checked_at,
                'checked': bool(checked_at), 'created_at': created_at or t, 'updated_at': t,
                'updated_ts': time.time(), 'source': source}

    def _write_people(self, col, people):
        for i in range(0, len(people), BATCH):
            batch = self.client.batch()
            for person in people[i:i + BATCH]:
                if isinstance(person, tuple):
                    person = self._new_person(*person, source='import')
                batch.set(col.document(person.pop('id', None) or str(uuid.uuid4())), person)
            batch.commit()

    def add_people(self, event, people):
        self._write_people(self._people(event), list(people))

    def replace_people(self, event, people):
        """Escreve a lista numa versão nova e só no fim passa a usá-la. A anterior fica guardada."""
        old_version = event['list_version']
        old_count = _count(self._people(event))
        lists = self._event_ref(event['id']).collection('lists')
        new_version = max([old_version] + [int(s.id) for s in lists.select([]).stream() if s.id.isdigit()]) + 1
        lists.document(str(new_version)).set({'created_at': now(), 'count': len(people)})
        self._write_people(self._people(event, new_version), list(people))
        self._event_ref(event['id']).update({'list_version': new_version})
        return old_count, f'lista anterior guardada no Firestore (events/{event["id"]}/lists/{old_version})'

    def person_action(self, event, action, pid, fields, badge):
        """save / checkin / reprint / undo numa transação. `badge` devolve os bytes da etiqueta."""
        col, jobs = self._people(event), self._jobs(event)
        ref = col.document(pid) if pid else None
        data = badge(*fields) if action in ('checkin', 'reprint') else None  # valida antes de escrever

        @firestore.transactional
        def run(transaction):
            snap = ref.get(transaction=transaction) if ref else None
            if ref and not snap.exists:
                raise ValueError('Participante não encontrado neste evento. Atualiza a página.')
            old = snap.to_dict() if snap else None
            update = {'updated_at': now(), 'updated_ts': time.time()}

            if action == 'undo':
                if not old:
                    raise ValueError('Seleciona um participante.')
                transaction.update(ref, dict(update, checked_at=None, checked=False))
                return pid

            name, role, company = fields
            update.update(name=name, role=role, company=company)
            if action == 'reprint' and not old:
                raise ValueError('Guarda primeiro o participante.')
            if action == 'checkin':
                if old and old.get('checked_at'):
                    raise AlreadyCheckedIn('Este participante já fez check-in. Usa Reimprimir badge.')
                update.update(checked_at=update['updated_at'], checked=True)
            if data is not None:
                t = time.time()
                transaction.set(jobs.document(str(uuid.uuid4())), {'label': name, 'data': data, 'status': 'pending',
                                                                   'error': '', 'created_ts': t, 'updated_ts': t})
            if old:
                transaction.update(ref, update)
                return pid
            new_ref = col.document(str(uuid.uuid4()))
            transaction.set(new_ref, self._new_person(name, role, company, 'walk-in', checked_at=update.get('checked_at')))
            return new_ref.id

        return run(self.client.transaction())

    # ── fila de impressão ──

    def claim_job(self, event, max_age):
        jobs = self._jobs(event)

        @firestore.transactional
        def run(transaction):
            t = time.time()
            pending = sorted(jobs.where(filter=FieldFilter('status', '==', 'pending')).stream(transaction=transaction),
                             key=lambda s: s.get('created_ts'))
            for snap in pending:
                if snap.get('created_ts') < t - max_age:
                    transaction.update(snap.reference, {'status': 'expired', 'updated_ts': t})
                else:
                    transaction.update(snap.reference, {'status': 'sent', 'updated_ts': t})
                    return {'id': snap.id, 'label': snap.get('label'), 'data': snap.get('data')}
            return None

        return run(self.client.transaction())

    def finish_job(self, event, job_id, error):
        ref = self._jobs(event).document(job_id) if job_id else None
        if not ref or not ref.get().exists:
            return
        ref.update({'status': 'error' if error else 'done', 'error': error, 'updated_ts': time.time()})
        self._event_ref(event['id']).update({'last_print_error': error})

    def printer_info(self, event, max_age):
        pending = sum(1 for s in self._jobs(event).where(filter=FieldFilter('status', '==', 'pending')).stream()
                      if s.get('created_ts') >= time.time() - max_age)
        return pending, event.get('last_print_error', ''), event.get('agent_seen_ts', 0)

    def agent_seen(self, event):
        t = time.time()
        if t - self.heartbeats.get(event['id'], 0) >= HEARTBEAT_INTERVAL:
            self.heartbeats[event['id']] = t
            self._event_ref(event['id']).update({'agent_seen_ts': t})

    # ── tentativas de login ──

    def _failures_ref(self, ip):
        return self.client.collection('login_failures').document(hashlib.sha256(ip.encode()).hexdigest()[:32])

    def recent_failures(self, ip, window):
        snap = self._failures_ref(ip).get()
        times = snap.get('times') if snap.exists else []
        recent = [t for t in times if t > time.time() - window]
        if len(recent) < len(times):
            self._failures_ref(ip).set({'times': recent})
        return len(recent)

    def note_failure(self, ip):
        self._failures_ref(ip).set({'times': firestore.ArrayUnion([time.time()])}, merge=True)

    # ── migração a partir da versão local ──

    def restore_event(self, event, people):
        """Copia um evento da base de dados SQLite (mesmo id, palavra-passe e check-ins)."""
        self.create_event(event['name'], event['event_date'], event_id=event['id'], created_at=event['created_at'])
        rows = []
        for p in people:
            row = self._new_person(p['name'], p['role'], p['company'], p['source'],
                                   checked_at=p['checked_at'], created_at=p['created_at'])
            rows.append(dict(row, id=p['id']))
        self._write_people(self._people({'id': event['id'], 'list_version': 1}), rows)
        return len(rows)
