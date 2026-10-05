#!/usr/bin/env bash
# ==============================================================================
# Linux Network Namespaces & TC Setup Script for Adaptive QoS Engine
# ==============================================================================
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run as root (sudo $0)"
  exit 1
fi

echo "[+] Setting up Linux Network Namespaces for Adaptive QoS..."

NS_LAN="lan_client"
NS_GW="qos_gw"
NS_WAN="wan_server"

IF_LAN_C="veth-lan-c"
IF_LAN_GW="veth-lan-gw"
IF_WAN_GW="veth-wan-gw"
IF_WAN_S="veth-wan-s"

# Cleanup any previous state
ip netns del "$NS_LAN" 2>/dev/null || true
ip netns del "$NS_GW" 2>/dev/null || true
ip netns del "$NS_WAN" 2>/dev/null || true

# 1. Create Network Namespaces
echo "[+] Creating namespaces: $NS_LAN, $NS_GW, $NS_WAN..."
ip netns add "$NS_LAN"
ip netns add "$NS_GW"
ip netns add "$NS_WAN"

# 2. Create VETH pairs
echo "[+] Creating veth links..."
ip link add "$IF_LAN_C" type veth peer name "$IF_LAN_GW"
ip link add "$IF_WAN_GW" type veth peer name "$IF_WAN_S"

# 3. Assign veth endpoints into namespaces
ip link set "$IF_LAN_C" netns "$NS_LAN"
ip link set "$IF_LAN_GW" netns "$NS_GW"
ip link set "$IF_WAN_GW" netns "$NS_GW"
ip link set "$IF_WAN_S" netns "$NS_WAN"

# 4. Configure loopback and IP addresses
echo "[+] Configuring IP addressing and bringing up interfaces..."
ip netns exec "$NS_LAN" ip link set lo up
ip netns exec "$NS_GW" ip link set lo up
ip netns exec "$NS_WAN" ip link set lo up

ip netns exec "$NS_LAN" ip addr add 10.0.1.2/24 dev "$IF_LAN_C"
ip netns exec "$NS_LAN" ip link set "$IF_LAN_C" up

ip netns exec "$NS_GW" ip addr add 10.0.1.1/24 dev "$IF_LAN_GW"
ip netns exec "$NS_GW" ip link set "$IF_LAN_GW" up

ip netns exec "$NS_GW" ip addr add 10.0.2.1/24 dev "$IF_WAN_GW"
ip netns exec "$NS_GW" ip link set "$IF_WAN_GW" up

ip netns exec "$NS_WAN" ip addr add 10.0.2.2/24 dev "$IF_WAN_S"
ip netns exec "$NS_WAN" ip link set "$IF_WAN_S" up

# 5. Enable IP forwarding on the Gateway
echo "[+] Enabling IP forwarding on gateway router..."
ip netns exec "$NS_GW" sysctl -w net.ipv4.ip_forward=1 >/dev/null

# 6. Configure routes
echo "[+] Configuring default gateway routes..."
ip netns exec "$NS_LAN" ip route add default via 10.0.1.1
ip netns exec "$NS_WAN" ip route add default via 10.0.2.1

# 7. Apply initial 100 Mbps WAN traffic control
echo "[+] Initializing WAN interface tc qdisc (100 Mbps, 15ms base RTT, 100 packet buffer)..."
ip netns exec "$NS_GW" tc qdisc add dev "$IF_WAN_GW" root handle 1: netem delay 7.5ms limit 100
ip netns exec "$NS_GW" tc qdisc add dev "$IF_WAN_GW" parent 1: handle 10: tbf rate 100mbit burst 125k latency 100ms

echo "[✔] Setup complete! Verifying connectivity..."
ip netns exec "$NS_LAN" ping -c 2 10.0.2.2
