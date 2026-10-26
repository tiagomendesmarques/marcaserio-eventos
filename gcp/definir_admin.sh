#!/bin/bash
# Define ou repõe a palavra-passe de administração (pedida no terminal; nunca fica guardada em claro).
set -euo pipefail
source "$(dirname "$0")/config.sh"
python_firestore "$REPO/marcaserio_checkin.py" --definir-admin
