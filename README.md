# marcAsério Check-in

Acreditação de eventos com impressão de badges numa Honeywell PC42d (Direct Protocol).

- **Local** (um Mac ligado à impressora; Python 3, sem dependências externas, dados em SQLite):
  `bash Abrir_marcAserio_CheckIn.command`. Ver [LEIA-ME.txt](LEIA-ME.txt).
- **Online** em `eventos.marcaserio.pt` (Cloud Run + Firestore, com o agente de impressão no Mac do evento):
  ver [GUIA-ONLINE.md](GUIA-ONLINE.md). Para instalar com o Claude in Chrome, ver [SETUP-CHROME.md](SETUP-CHROME.md).

| Ficheiro | Função |
|---|---|
| `marcaserio_checkin.py` | Servidor HTTP, API, sessões e impressão |
| `store_sqlite.py` | Dados em SQLite (versão local) |
| `store_firestore.py` | Dados em Firestore (versão online) |
| `web/` | Interface (HTML, CSS e JS) |
| `agente_impressao.py` | Agente que imprime as etiquetas da versão online no Mac do evento |
| `Dockerfile`, `requirements.txt` | Contentor do Cloud Run |
| `gcp/` | Scripts de instalação e manutenção no Google Cloud |

O bloco de impressão Direct Protocol (`ascii_text`, `badge_layout`, `dp_commands`, `print_badge`)
está preservado literalmente da v2.6 aprovada. Não alterar sem testar na impressora.
