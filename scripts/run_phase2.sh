#!/usr/bin/env bash
# ==============================================================================
# Run Phase 2: Traffic Classification & Link-Capacity Estimation on Linux
# ==============================================================================
set -euo pipefail

MODE="${1:-linux}"
DURATION="${2:-20.0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

if [ "$MODE" = "linux" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo "[-] Linux mode requires root. Re-executing with sudo..."
        exec sudo python3 "$REPO_ROOT/run_phase2.py" --mode linux --duration "$DURATION"
    fi
    python3 "$REPO_ROOT/run_phase2.py" --mode linux --duration "$DURATION"
else
    python3 "$REPO_ROOT/run_phase2.py" --mode simulated --duration "$DURATION"
fi
