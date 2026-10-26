"""Testes de ponta a ponta: arrancam a app (SQLite, impressão em fila) e percorrem o essencial.

Correm antes de cada deploy (GitHub Actions) e localmente com:  python3 -m unittest discover tests
"""
import base64
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from marcaserio_checkin import dp_commands  # noqa: E402

ADMIN = 'tiago.marques@marcaserio.pt'
CLIENT_ID = 'teste.apps.googleusercontent.com'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class App:
    """A app a correr num processo à parte, com dados num diretório temporário."""

    def __init__(self, **env):
        self.tmp = tempfile.TemporaryDirectory()
        port = free_port()
        self.url = f'http://127.0.0.1:{port}'
        env = dict(os.environ, MA_PORT=str(port), MA_DATA_DIR=self.tmp.name, MA_PRINT_MODE='agente',
                   MA_OPEN_BROWSER='0', MA_VERSION='teste', **{k: v.replace('{url}', self.url) for k, v in env.items()})
        self.proc = subprocess.Popen([sys.executable, str(ROOT / 'marcaserio_checkin.py')], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url + '/api/estado', timeout=1)
                return
            except OSError:
                time.sleep(0.1)
        self.proc.kill()
        raise RuntimeError('A app não arrancou:\n' + self.proc.stdout.read().decode())

    def stop(self):
        self.proc.terminate()
        self.proc.wait(5)
        self.proc.stdout.close()
        self.tmp.cleanup()

    def client(self, follow_redirects=True):
        handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if not follow_redirects:
            handlers.append(NoRedirect())
        return urllib.request.build_opener(*handlers)

    def call(self, opener, path, data=None, raw=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(self.url + path, data=body, method='POST' if body is not None else 'GET')
        try:
            with opener.open(req, timeout=30) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b'{}')


class LocalTest(unittest.TestCase):
    """Versão local: conta de administração criada no primeiro arranque, só email e palavra-passe."""

    @classmethod
    def setUpClass(cls):
        cls.app = App()

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()

    def test_fluxo_completo(self):
        app = self.app
        admin, hostess, editor, anon = app.client(), app.client(), app.client(), app.client()

        status, estado = app.call(anon, '/api/estado')
        self.assertEqual((status, estado['version'], estado['google']), (200, 'teste', False))
        for page in ('/', '/app.js', '/app.css', '/logo.png'):
            self.assertEqual(anon.open(app.url + page).status, 200, page)

        # Primeiro arranque: cria-se a conta de administração (o administrador de raiz ganha palavra-passe).
        self.assertTrue(app.call(anon, '/api/session')[1]['setup_needed'])
        status, result = app.call(admin, '/api/setup', {'email': ADMIN, 'password': 'admin-de-teste-1'})
        self.assertEqual((status, result['me']['role']), (200, 'admin'), result)
        self.assertEqual(app.call(anon, '/api/setup', {'email': 'x@y.pt', 'password': 'outra-pass-1'})[0], 403)

        status, created = app.call(admin, '/api/manage/events/create', {'name': 'Evento Teste', 'event_date': '2027-01-01'})
        self.assertEqual(status, 200, created)
        event_id = created['id']
        other_id = app.call(admin, '/api/manage/events/create', {'name': 'Outro Evento'})[1]['id']

        # Convites: hospedeira (só um evento) e editor.
        self.assertEqual(app.call(admin, '/api/manage/users/save', {
            'email': 'Ana@Exemplo.pt', 'role': 'hostess', 'password': 'curta'})[0], 400)
        self.assertEqual(app.call(admin, '/api/manage/users/save', {
            'email': 'ana@exemplo.pt', 'role': 'hostess', 'password': 'hospedeira-1', 'events': [event_id]})[0], 200)
        self.assertEqual(app.call(admin, '/api/manage/users/save', {
            'email': 'rui@exemplo.pt', 'role': 'editor', 'password': 'editor-de-teste'})[0], 200)

        # A hospedeira entra com email e palavra-passe e só vê o seu evento.
        self.assertEqual(app.call(hostess, '/api/login', {'email': 'ana@exemplo.pt', 'password': 'errada'})[0], 403)
        status, result = app.call(hostess, '/api/login', {'email': 'ANA@exemplo.pt', 'password': 'hospedeira-1'})
        self.assertEqual((status, result['me']['role']), (200, 'hostess'))
        self.assertEqual([e['id'] for e in app.call(hostess, '/api/my-events')[1]['events']], [event_id])
        self.assertEqual(app.call(hostess, '/api/select-event', {'event_id': other_id})[0], 403)
        self.assertEqual(app.call(hostess, '/api/manage/users')[0], 403)
        self.assertEqual(app.call(hostess, '/api/select-event', {'event_id': event_id})[0], 200)

        # Importar é para editores; a hospedeira faz check-ins.
        csv = 'Nome;Função;Empresa\nJoão Pereira;CFO;Empresa Lda\nAna Mendes;;Banco\nAna Mendes;Dup;Banco\n'
        self.assertEqual(app.call(hostess, '/api/import?filename=lista.csv', raw=csv.encode())[0], 403)
        app.call(admin, '/api/select-event', {'event_id': event_id})
        status, result = app.call(admin, '/api/import?filename=lista.csv', raw=csv.encode())
        self.assertEqual(status, 200, result)
        self.assertIn('2 participantes importados', result['message'])

        people = app.call(hostess, '/api/people')[1]['people']
        joao = next(p for p in people if p['name'] == 'João Pereira')
        person = {'id': joao['id'], 'name': 'João Pereira', 'role': 'CFO', 'company': 'Empresa Lda'}
        self.assertEqual(app.call(hostess, '/api/checkin', person)[0], 200)
        self.assertEqual(app.call(admin, '/api/checkin', person)[0], 409)

        # O agente recebe exatamente os comandos Direct Protocol da v2.6.
        status, result = app.call(hostess, '/api/print/next')
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(result['job']['data']), dp_commands('João Pereira', 'CFO', 'Empresa Lda'))
        self.assertEqual(app.call(hostess, '/api/print/result', {'id': result['job']['id'], 'error': ''})[0], 200)

        # Um editor só gere hospedeiras.
        app.call(editor, '/api/login', {'email': 'rui@exemplo.pt', 'password': 'editor-de-teste'})
        self.assertEqual(app.call(editor, '/api/manage/users/save', {
            'email': 'novo@exemplo.pt', 'role': 'admin', 'password': 'qualquer-coisa'})[0], 403)
        self.assertEqual(app.call(editor, '/api/manage/users/delete', {'email': ADMIN})[0], 403)
        self.assertEqual(app.call(editor, '/api/manage/logs')[0], 403)

        # Nem o último administrador nem a própria conta podem ser removidos.
        self.assertEqual(app.call(admin, '/api/manage/users/delete', {'email': ADMIN})[0], 400)

        # Mudar a palavra-passe termina as sessões; retirar o evento tira o acesso.
        self.assertEqual(app.call(admin, '/api/manage/users/save', {
            'email': 'ana@exemplo.pt', 'role': 'hostess', 'password': 'hospedeira-2', 'events': [event_id]})[0], 200)
        self.assertEqual(app.call(hostess, '/api/people')[0], 403)
        app.call(hostess, '/api/login', {'email': 'ana@exemplo.pt', 'password': 'hospedeira-2'})
        app.call(hostess, '/api/select-event', {'event_id': event_id})
        self.assertEqual(app.call(hostess, '/api/people')[0], 200)
        app.call(admin, '/api/manage/users/save', {'email': 'ana@exemplo.pt', 'role': 'hostess', 'events': [other_id]})
        self.assertEqual(app.call(hostess, '/api/people')[0], 403)

        # Registos: entradas, alterações e check-ins.
        status, result = app.call(admin, '/api/manage/logs')
        self.assertEqual(status, 200)
        actions = [(log['actor'], log['action']) for log in result['logs']]
        for expected in [(ADMIN, 'Conta de administração criada'), ('ana@exemplo.pt', 'Entrada falhada'),
                         ('ana@exemplo.pt', 'Entrada (palavra-passe)'), (ADMIN, 'Utilizador criado'),
                         (ADMIN, 'Lista importada'), ('ana@exemplo.pt', 'Check-in'), (ADMIN, 'Utilizador alterado')]:
            self.assertIn(expected, actions)


class FakeGoogle(BaseHTTPRequestHandler):
    """Endpoint de tokens do Google: o «code» escolhe a conta devolvida."""
    ACCOUNTS = {'editor': ('ana.silva@marcaserio.pt', 'marcaserio.pt'), 'admin': (ADMIN, 'marcaserio.pt'),
                'gmail': ('alguem@marcaserio.pt', None)}

    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
        email, hd = self.ACCOUNTS[form['code'][0]]
        claims = {'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'exp': time.time() + 300,
                  'email': email, 'email_verified': True}
        if hd:
            claims['hd'] = hd
        token = 'x.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.y'
        body = json.dumps({'id_token': token}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class GoogleLoginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.google = HTTPServer(('127.0.0.1', 0), FakeGoogle)
        threading.Thread(target=cls.google.serve_forever, daemon=True).start()
        cls.app = App(MA_PUBLIC_URL='{url}', MA_GOOGLE_CLIENT_ID=CLIENT_ID, MA_GOOGLE_CLIENT_SECRET='segredo',
                      MA_GOOGLE_TOKEN_URL=f'http://127.0.0.1:{cls.google.server_port}/token')

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()
        cls.google.shutdown()

    def google_login(self, code):
        opener = self.app.client(follow_redirects=False)
        try:
            opener.open(self.app.url + '/auth/google')
        except urllib.error.HTTPError as exc:
            location = exc.headers['Location']
        self.assertTrue(location.startswith('https://accounts.google.com/'))
        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        self.assertEqual(params['hd'], ['marcaserio.pt'])
        try:
            opener.open(f'{self.app.url}/auth/google/callback?code={code}&state={urllib.parse.quote(params["state"][0])}')
        except urllib.error.HTTPError as exc:
            return opener, exc.headers['Location']

    def test_google(self):
        opener, location = self.google_login('editor')
        self.assertEqual(location, '/')
        self.assertEqual(self.app.call(opener, '/api/session')[1]['me']['role'], 'editor')

        opener, location = self.google_login('admin')
        self.assertEqual(self.app.call(opener, '/api/session')[1]['me']['role'], 'admin')

        # Email @marcaserio.pt fora da organização Google Workspace (sem «hd»): recusado.
        opener, location = self.google_login('gmail')
        self.assertTrue(location.startswith('/?erro='))
        self.assertIsNone(self.app.call(opener, '/api/session')[1]['me'])

        # Sem o cookie do pedido original (state roubado ou antigo): recusado.
        stranger = self.app.client(follow_redirects=False)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            stranger.open(f'{self.app.url}/auth/google/callback?code=admin&state=forjado')
        self.assertTrue(ctx.exception.headers['Location'].startswith('/?erro='))

        # Online não há criação de administração pelo browser.
        self.assertEqual(self.app.call(stranger, '/api/setup', {'email': 'x@y.pt', 'password': 'qualquer-coisa'})[0], 403)


if __name__ == '__main__':
    unittest.main()
