#!/usr/bin/env python3
"""marcAsério | Agente de impressão.

Corre no Mac ligado à Honeywell, no local do evento. Entra na versão online com
email e palavra-passe (por exemplo, uma conta de hospedeira atribuída ao evento)
e imprime as etiquetas à medida que as hospedeiras fazem check-in, em qualquer
dispositivo.

Os comandos Direct Protocol chegam já gerados pelo servidor (a mesma função da
v2.6) e seguem para a impressora com o mesmo comando lp do modo local.
Só faz ligações de saída (HTTPS); não é preciso abrir portas na rede do evento.
"""
import base64
import getpass
import json
import os
import ssl
import subprocess
import sys
import time
from datetime import datetime
from http.cookies import SimpleCookie
from urllib.error import HTTPError
from urllib.request import Request, urlopen

URL = os.environ.get('MA_URL', 'https://eventos.marcaserio.pt').rstrip('/')
PRINTER = 'Honeywell_PC42d_203_FP'
USER_AGENT = 'marcAserio-agente-impressao/1.0'
# Certificados do sistema macOS: evita o erro de SSL do Python do python.org.
CA_FILE = '/etc/ssl/cert.pem'
SSL_CONTEXT = ssl.create_default_context(cafile=CA_FILE if os.path.exists(CA_FILE) else None)


class ApiError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def log(message):
    print(f'{datetime.now():%H:%M:%S}  {message}', flush=True)


def request(path, data=None, cookie=None, timeout=40):
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers['Content-Type'] = 'application/json'
    if cookie:
        headers['Cookie'] = cookie
    req = Request(URL + path, data=body, headers=headers, method='GET' if data is None else 'POST')
    try:
        with urlopen(req, timeout=timeout, context=SSL_CONTEXT) as res:
            return json.loads(res.read() or b'{}'), res.headers
    except HTTPError as exc:
        try:
            message = json.loads(exc.read()).get('error')
        except ValueError:
            message = None  # p. ex. uma página de desafio da Cloudflare
        raise ApiError(message or f'Resposta HTTP {exc.code} inesperada.', exc.code) from None


def cookie_from(headers):
    return f"ma_session={SimpleCookie(headers.get('Set-Cookie', ''))['ma_session'].value}"


def login(credentials=None):
    """Entra com email e palavra-passe. Devolve (credenciais, cookie); tenta primeiro as já conhecidas."""
    while True:
        if credentials is None:
            email = input('Email: ').strip()
            credentials = (email, getpass.getpass('Palavra-passe: '))
        try:
            _, headers = request('/api/login', {'email': credentials[0], 'password': credentials[1]})
            return credentials, cookie_from(headers)
        except ApiError as exc:
            print(f'  {exc}')
            if exc.status == 403 and 'Demasiadas' in str(exc):
                time.sleep(60)
            credentials = None


def choose_event(cookie):
    events = request('/api/my-events', cookie=cookie)[0]['events']
    if not events:
        raise SystemExit('Esta conta não tem nenhum evento atribuído.')
    print('\nEventos:')
    for i, event in enumerate(events, 1):
        print(f'  {i}. {event["name"]}  {event["event_date"]}')
    while True:
        choice = input('\nNúmero do evento: ').strip()
        if choice.isdigit() and 1 <= int(choice) <= len(events):
            return events[int(choice) - 1]


def enter(event, cookie):
    _, headers = request('/api/select-event', {'event_id': event['id']}, cookie)
    return cookie_from(headers)


def print_job(job, cookie):
    error = ''
    try:
        p = subprocess.run(['lp', '-d', PRINTER, '-o', 'raw'], input=base64.b64decode(job['data']),
                           capture_output=True, timeout=20)
        if p.returncode:
            error = p.stderr.decode(errors='replace').strip() or 'Erro ao enviar para a impressora.'
    except (OSError, subprocess.TimeoutExpired) as exc:
        error = str(exc)
    log(f'✗ {job["label"]} — {error}' if error else f'✓ {job["label"]}')
    request('/api/print/result', {'id': job['id'], 'error': error}, cookie)


def main():
    print(f'marcAsério | Agente de impressão\nServidor: {URL}\nImpressora: {PRINTER}')
    credentials, cookie = login()
    event = choose_event(cookie)
    cookie = enter(event, cookie)
    log(f'Ligado a «{event["name"]}». À espera de etiquetas… (Control+C para terminar)')
    delay = 1
    while True:
        try:
            job = request('/api/print/next', cookie=cookie)[0]['job']
            delay = 1
            if job:
                print_job(job, cookie)
        except ApiError as exc:
            if exc.status == 403:  # sessão expirada, palavra-passe alterada ou acesso retirado
                log(f'Sem acesso ({exc}); a entrar de novo…')
                credentials, cookie = login(credentials)
                try:
                    cookie = enter(event, cookie)
                except ApiError as err:
                    raise SystemExit(f'Sem acesso ao evento «{event["name"]}»: {err}')
                continue
            log(f'Erro do servidor: {exc}')
            time.sleep(delay)
            delay = min(delay * 2, 30)
        except OSError as exc:  # sem rede, timeout, DNS…
            log(f'Sem ligação ao servidor ({exc}). Nova tentativa em {delay} s.')
            time.sleep(delay)
            delay = min(delay * 2, 30)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nA terminar…')
        sys.exit(0)
