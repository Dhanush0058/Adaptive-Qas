#!/usr/bin/env bash
# ==============================================================================
# Teardown Script for Adaptive QoS Linux Environment
# ==============================================================================
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run as root (sudo $0)"
  exit 1
fi

echo "[+] Removing network namespaces..."
ip netns del lan_client 2>/dev/null || true
ip netns del qos_gw 2>/dev/null || true
ip netns del wan_server 2>/dev/null || true
echo "[✔] Teardown complete."
