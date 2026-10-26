#!/bin/bash
# Copia os dados da versão local (marcaserio_checkin.sqlite3) para o Firestore:
# palavra-passe de administração, eventos (com as palavras-passe), participantes e check-ins.
# Eventos que já existam no Firestore são ignorados.
set -euo pipefail
source "$(dirname "$0")/config.sh"
DB="${1:?Uso: bash gcp/migrar_sqlite.sh caminho/para/marcaserio_checkin.sqlite3}"
python_firestore "$REPO/gcp/migrar_sqlite.py" "$DB"
