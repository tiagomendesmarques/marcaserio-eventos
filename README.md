# marcAsério Check-in

Acreditação de eventos com impressão de badges numa Honeywell PC42d (Direct Protocol).
Python 3, sem dependências externas.

- **Local** (um Mac ligado à impressora): `bash Abrir_marcAserio_CheckIn.command`. Ver [LEIA-ME.txt](LEIA-ME.txt).
- **Online** em `eventos.marcaserio.pt` (GCP + Cloudflare Tunnel e agente de impressão no Mac do evento):
  ver [GUIA-ONLINE.md](GUIA-ONLINE.md).

| Ficheiro | Função |
|---|---|
| `marcaserio_checkin.py` | Servidor HTTP, API e base de dados SQLite |
| `web/` | Interface (HTML, CSS e JS) |
| `agente_impressao.py` | Agente que imprime as etiquetas da versão online no Mac do evento |
| `gcp/` | Scripts de instalação no Google Cloud |
| `SETUP-CHROME.md` | Prompt para instalar tudo com o Claude in Chrome |

O bloco de impressão Direct Protocol (`ascii_text`, `badge_layout`, `dp_commands`, `print_badge`)
está preservado literalmente da v2.6 aprovada. Não alterar sem testar na impressora.
