#!/bin/bash
# Plano B: dá (ou repõe) uma palavra-passe a um administrador, para entrar sem o Google.
# Pede o email e a palavra-passe no terminal; nunca ficam guardados em claro.
set -euo pipefail
source "$(dirname "$0")/config.sh"
python_firestore "$REPO/marcaserio_checkin.py" --definir-admin
