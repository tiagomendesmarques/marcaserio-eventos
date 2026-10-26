"""Teste de ponta a ponta: arranca a app (SQLite, impressão em fila) e percorre o essencial.

Corre antes de cada deploy (GitHub Actions) e localmente com:  python3 -m unittest discover tests
"""
import base64
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from marcaserio_checkin import dp_commands  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        port = free_port()
        cls.url = f'http://127.0.0.1:{port}'
        env = dict(os.environ, MA_PORT=str(port), MA_DATA_DIR=cls.tmp.name, MA_PRINT_MODE='agente',
                   MA_OPEN_BROWSER='0', MA_VERSION='teste')
        cls.server = subprocess.Popen([sys.executable, str(ROOT / 'marcaserio_checkin.py')], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.url + '/api/estado', timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            cls.server.kill()
            raise RuntimeError('A app não arrancou:\n' + cls.server.stdout.read().decode())

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(5)
        cls.server.stdout.close()
        cls.tmp.cleanup()

    def client(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, opener, path, data=None, raw=None, headers=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(self.url + path, data=body, headers=headers or {},
                                     method='POST' if body is not None else 'GET')
        try:
            with opener.open(req, timeout=30) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_fluxo_completo(self):
        admin, hostess, other = self.client(), self.client(), self.client()

        status, estado = self.call(admin, '/api/estado')
        self.assertEqual((status, estado['version']), (200, 'teste'))
        for page in ('/', '/app.js', '/app.css', '/logo.png'):
            self.assertEqual(admin.open(self.url + page).status, 200, page)

        self.assertEqual(self.call(admin, '/api/setup-admin', {'password': 'admin-de-teste-1'})[0], 200)
        status, created = self.call(admin, '/api/admin/create-event',
                                    {'name': 'Evento Teste', 'event_date': '2027-01-01', 'password': 'evento-teste-1'})
        self.assertEqual(status, 200)
        event_id = created['id']

        self.assertEqual(self.call(hostess, '/api/login-event', {'event_id': event_id, 'password': 'errada'})[0], 403)
        self.assertEqual(self.call(hostess, '/api/login-event', {'event_id': event_id, 'password': 'evento-teste-1'})[0], 200)
        self.assertEqual(self.call(other, '/api/people')[0], 403)

        csv = 'Nome;Função;Empresa\nJoão Pereira;CFO;Empresa Lda\nAna Mendes;;Banco\nAna Mendes;Dup;Banco\n'
        status, result = self.call(hostess, '/api/import?filename=lista.csv', raw=csv.encode(),
                                   headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(status, 200, result)
        self.assertIn('2 participantes importados', result['message'])

        people = self.call(hostess, '/api/people')[1]['people']
        joao = next(p for p in people if p['name'] == 'João Pereira')

        status, result = self.call(hostess, '/api/checkin', {'id': joao['id'], 'name': 'João Pereira', 'role': 'CFO', 'company': 'Empresa Lda'})
        self.assertEqual(status, 200, result)
        status, result = self.call(hostess, '/api/checkin', {'id': joao['id'], 'name': 'João Pereira', 'role': 'CFO', 'company': 'Empresa Lda'})
        self.assertEqual(status, 409, result)

        # O agente recebe exatamente os comandos Direct Protocol da v2.6.
        status, result = self.call(hostess, '/api/print/next')
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(result['job']['data']), dp_commands('João Pereira', 'CFO', 'Empresa Lda'))
        self.assertEqual(self.call(hostess, '/api/print/result', {'id': result['job']['id'], 'error': ''})[0], 200)

        checked = [p for p in self.call(hostess, '/api/people')[1]['people'] if p['checked_at']]
        self.assertEqual([p['name'] for p in checked], ['João Pereira'])

        # Mudar a palavra-passe do evento termina as sessões abertas.
        self.assertEqual(self.call(admin, '/api/admin/change-password', {'event_id': event_id, 'password': 'evento-teste-2'})[0], 200)
        self.assertEqual(self.call(hostess, '/api/people')[0], 403)


if __name__ == '__main__':
    unittest.main()
