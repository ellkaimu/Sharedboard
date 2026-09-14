#!/bin/bash
# Launcher for /usr/bin/shareboard.
set -e
export PYTHONPATH="/usr/lib/python3/dist-packages:${PYTHONPATH:-}"
exec python3 -m shareboard "$@"
