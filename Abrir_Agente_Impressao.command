#!/bin/bash
# Mantém o Mac acordado enquanto o agente estiver aberto.
cd "$(dirname "$0")"
exec caffeinate -i python3 agente_impressao.py
