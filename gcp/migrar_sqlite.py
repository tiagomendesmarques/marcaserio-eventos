"""Copia uma base de dados SQLite da versão local para o Firestore (ver migrar_sqlite.sh)."""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from marcaserio_checkin import NO_PASSWORD  # noqa: E402
from store_firestore import FirestoreStore  # noqa: E402


def main(path):
    source = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    store = FirestoreStore(os.environ['GOOGLE_CLOUD_PROJECT'], os.environ['MA_FIRESTORE_DB'], NO_PASSWORD)

    admin = source.execute("SELECT value FROM settings WHERE key='admin_password'").fetchone()
    if admin and store.set_setting('admin_password', admin['value'], only_if_absent=True):
        print('Palavra-passe de administração copiada.')
    else:
        print('Palavra-passe de administração: mantida a que já existe no Firestore.')

    existing = {e['id'] for e in store.list_events()}
    for event in source.execute('SELECT * FROM events ORDER BY event_date'):
        if event['id'] in existing:
            print(f'Ignorado (já existe): {event["name"]}')
            continue
        people = [dict(p) for p in source.execute('SELECT * FROM people WHERE event_id=?', (event['id'],))]
        count = store.restore_event(dict(event), people)
        checked = sum(1 for p in people if p['checked_at'])
        print(f'Copiado: {event["name"]} — {count} participantes, {checked} check-ins.')


if __name__ == '__main__':
    main(sys.argv[1])
