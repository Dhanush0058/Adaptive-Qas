"""Queue and buffer monitor for Linux tc qdisc and simulated router interfaces."""

import re
import subprocess
import time
from typing import Optional, Callable
from adaptive_qos.common.models import QueueMetrics
from adaptive_qos.common.logger import get_logger

logger = get_logger("monitoring.queue")


class QueueMonitor:
    """
    Monitors queue statistics (backlog bytes, backlog packets, dropped packets, overlimits).
    Can inspect real Linux tc qdisc on an interface (optionally inside a network namespace)
    or poll a simulation queue callback.
    """

    def __init__(
        self,
        interface: str = "veth-wan-gw",
        netns: Optional[str] = None,
        capacity_mbps: float = 100.0,
        sim_queue_callback: Optional[Callable[[], QueueMetrics]] = None,
    ):
        self.interface = interface
        self.netns = netns
        self.capacity_mbps = capacity_mbps
        self.sim_queue_callback = sim_queue_callback

    def set_capacity(self, capacity_mbps: float) -> None:
        self.capacity_mbps = capacity_mbps

    def collect(self) -> QueueMetrics:
        """Poll and return the latest queue metrics."""
        if self.sim_queue_callback:
            metrics = self.sim_queue_callback()
            metrics.capacity_mbps = self.capacity_mbps
            return metrics

        return self._poll_linux_tc()

    def _poll_linux_tc(self) -> QueueMetrics:
        """Execute `tc -s qdisc show dev <interface>` and parse the output."""
        now = time.time()
        cmd = ["tc", "-s", "qdisc", "show", "dev", self.interface]
        if self.netns:
            cmd = ["ip", "netns", "exec", self.netns] + cmd

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
            if res.returncode != 0:
                logger.debug(f"tc command returned {res.returncode}: {res.stderr}")
                return QueueMetrics(timestamp=now, interface_name=self.interface, capacity_mbps=self.capacity_mbps)

            output = res.stdout
            return self._parse_tc_output(output, now)
        except Exception as e:
            logger.debug(f"Failed to query tc queue: {e}")
            return QueueMetrics(timestamp=now, interface_name=self.interface, capacity_mbps=self.capacity_mbps)

    def _parse_tc_output(self, output: str, timestamp: float) -> QueueMetrics:
        """
        Example tc output:
        qdisc tbf 1: root refcnt 2 rate 100Mbit burst 100Kb lat 50.0ms
         Sent 10543200 bytes 7530 pkt (dropped 120, overlimits 456 requeues 0)
         backlog 45000b 30p requeues 0
        """
        backlog_bytes = 0
        backlog_packets = 0
        dropped = 0
        overlimits = 0
        requeues = 0

        # Extract dropped and overlimits
        drop_match = re.search(r"dropped\s+(\d+)", output)
        if drop_match:
            dropped = int(drop_match.group(1))

        overlimit_match = re.search(r"overlimits\s+(\d+)", output)
        if overlimit_match:
            overlimits = int(overlimit_match.group(1))

        # Extract backlog
        backlog_match = re.search(r"backlog\s+(\d+)([bBkKmM]?)\s+(\d+)p", output)
        if backlog_match:
            val = int(backlog_match.group(1))
            unit = backlog_match.group(2).lower()
            multiplier = 1
            if unit == "k":
                multiplier = 1024
            elif unit == "m":
                multiplier = 1024 * 1024
            backlog_bytes = val * multiplier
            backlog_packets = int(backlog_match.group(3))

        return QueueMetrics(
            timestamp=timestamp,
            interface_name=self.interface,
            capacity_mbps=self.capacity_mbps,
            backlog_bytes=backlog_bytes,
            backlog_packets=backlog_packets,
            dropped_packets=dropped,
            overlimit_count=overlimits,
            requeues=requeues,
        )
