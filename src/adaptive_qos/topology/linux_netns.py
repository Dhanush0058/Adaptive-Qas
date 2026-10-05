"""Linux Network Namespaces, veth pairs, and tc/netem topology implementation."""

import subprocess
import time
from typing import Optional
from adaptive_qos.common.models import TopologyConfig, QueueMetrics
from adaptive_qos.common.logger import get_logger
from adaptive_qos.topology.base import BaseTopology
from adaptive_qos.monitoring.queue_monitor import QueueMonitor

logger = get_logger("topology.linux")


class LinuxNetnsTopology(BaseTopology):
    """
    Constructs an isolated 3-node Linux Network Namespace laboratory:
      [lan_client (10.0.1.2)]  <--veth-lan-->  [qos_gw (10.0.1.1 / 10.0.2.1)]  <--veth-wan-->  [wan_server (10.0.2.2)]
    
    Applies Linux tc (TBF / Netem) traffic control on the WAN interface of the gateway router.
    """

    NS_LAN = "lan_client"
    NS_GW = "qos_gw"
    NS_WAN = "wan_server"

    IF_LAN_CLIENT = "veth-lan-c"
    IF_LAN_GW = "veth-lan-gw"
    IF_WAN_GW = "veth-wan-gw"
    IF_WAN_SERVER = "veth-wan-s"

    def __init__(self, config: TopologyConfig):
        super().__init__(config)
        self.queue_monitor = QueueMonitor(
            interface=self.IF_WAN_GW,
            netns=self.NS_GW,
            capacity_mbps=config.initial_bandwidth_mbps
        )

    def _run_cmd(self, cmd: str, check: bool = True) -> subprocess.CompletedProcess:
        logger.debug(f"Exec: {cmd}")
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)

    def setup(self) -> None:
        """Build network namespaces, veth interfaces, IP addressing, and routing."""
        logger.info("Setting up Linux Network Namespaces topology...")
        self.teardown()  # Clean any stale resources

        try:
            # 1. Create Namespaces
            self._run_cmd(f"ip netns add {self.NS_LAN}")
            self._run_cmd(f"ip netns add {self.NS_GW}")
            self._run_cmd(f"ip netns add {self.NS_WAN}")

            # 2. Create VETH Pairs
            self._run_cmd(f"ip link add {self.IF_LAN_CLIENT} type veth peer name {self.IF_LAN_GW}")
            self._run_cmd(f"ip link add {self.IF_WAN_GW} type veth peer name {self.IF_WAN_SERVER}")

            # 3. Move interfaces into namespaces
            self._run_cmd(f"ip link set {self.IF_LAN_CLIENT} netns {self.NS_LAN}")
            self._run_cmd(f"ip link set {self.IF_LAN_GW} netns {self.NS_GW}")
            self._run_cmd(f"ip link set {self.IF_WAN_GW} netns {self.NS_GW}")
            self._run_cmd(f"ip link set {self.IF_WAN_SERVER} netns {self.NS_WAN}")

            # 4. Bring up loopback & interfaces
            for ns in [self.NS_LAN, self.NS_GW, self.NS_WAN]:
                self._run_cmd(f"ip netns exec {ns} ip link set lo up")

            self._run_cmd(f"ip netns exec {self.NS_LAN} ip addr add {self.config.lan_ip}/24 dev {self.IF_LAN_CLIENT}")
            self._run_cmd(f"ip netns exec {self.NS_LAN} ip link set {self.IF_LAN_CLIENT} up")

            self._run_cmd(f"ip netns exec {self.NS_GW} ip addr add {self.config.gw_lan_ip}/24 dev {self.IF_LAN_GW}")
            self._run_cmd(f"ip netns exec {self.NS_GW} ip link set {self.IF_LAN_GW} up")

            self._run_cmd(f"ip netns exec {self.NS_GW} ip addr add {self.config.gw_wan_ip}/24 dev {self.IF_WAN_GW}")
            self._run_cmd(f"ip netns exec {self.NS_GW} ip link set {self.IF_WAN_GW} up")

            self._run_cmd(f"ip netns exec {self.NS_WAN} ip addr add {self.config.wan_ip}/24 dev {self.IF_WAN_SERVER}")
            self._run_cmd(f"ip netns exec {self.NS_WAN} ip link set {self.IF_WAN_SERVER} up")

            # 5. Enable IP forwarding in Gateway
            self._run_cmd(f"ip netns exec {self.NS_GW} sysctl -w net.ipv4.ip_forward=1")

            # 6. Configure Default Routing
            self._run_cmd(f"ip netns exec {self.NS_LAN} ip route add default via {self.config.gw_lan_ip}")
            self._run_cmd(f"ip netns exec {self.NS_WAN} ip route add default via {self.config.gw_wan_ip}")

            # 7. Apply initial tc rate shaping (TBF + netem for WAN delay)
            self.set_wan_bandwidth(self.config.initial_bandwidth_mbps, self.config.wan_base_rtt_ms)

            logger.info("Linux Network Namespace topology successfully established.")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to setup Linux netns topology: {e.stderr}")
            self.teardown()
            raise

    def set_wan_bandwidth(self, bandwidth_mbps: float, rtt_ms: Optional[float] = None) -> None:
        """Configure Linux tc shaping on gateway's WAN interface."""
        self.current_bandwidth_mbps = bandwidth_mbps
        self.queue_monitor.set_capacity(bandwidth_mbps)
        delay_ms = (rtt_ms or self.config.wan_base_rtt_ms) / 2.0  # one-way delay

        logger.info(f"Applying Linux tc: {bandwidth_mbps} Mbps, delay {delay_ms} ms, buffer {self.config.buffer_queue_limit_packets} pkts")

        # Delete existing root qdisc if present
        self._run_cmd(
            f"ip netns exec {self.NS_GW} tc qdisc del dev {self.IF_WAN_GW} root",
            check=False
        )

        # Configure Hierarchical Token Bucket / TBF with Netem delay & tail drop queue limit
        burst_kb = max(32, int(bandwidth_mbps * 125 / 10))  # 10ms burst
        limit_bytes = self.config.buffer_queue_limit_packets * 1500

        # Create Netem delay + TBF rate limiter
        cmd = (
            f"ip netns exec {self.NS_GW} tc qdisc add dev {self.IF_WAN_GW} root handle 1: "
            f"netem delay {delay_ms}ms limit {self.config.buffer_queue_limit_packets}"
        )
        self._run_cmd(cmd, check=False)

        cmd2 = (
            f"ip netns exec {self.NS_GW} tc qdisc add dev {self.IF_WAN_GW} parent 1: handle 10: "
            f"tbf rate {bandwidth_mbps}mbit burst {burst_kb}k latency 100ms"
        )
        self._run_cmd(cmd2, check=False)

    def get_queue_metrics(self) -> QueueMetrics:
        return self.queue_monitor.collect()

    def teardown(self) -> None:
        """Remove network namespaces and interfaces cleanly."""
        logger.info("Tearing down Linux netns topology...")
        for ns in [self.NS_LAN, self.NS_GW, self.NS_WAN]:
            self._run_cmd(f"ip netns del {ns}", check=False)
