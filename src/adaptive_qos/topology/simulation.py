"""Cross-platform simulated bottleneck network topology."""

import time
import threading
from typing import Optional
from adaptive_qos.common.models import TopologyConfig, QueueMetrics
from adaptive_qos.common.logger import get_logger
from adaptive_qos.topology.base import BaseTopology
from adaptive_qos.monitoring.queue_monitor import QueueMonitor

logger = get_logger("topology.sim")


class SimulatedTopology(BaseTopology):
    """
    Software-emulated bottleneck network link for local and cross-platform testing:
    - Emulates FIFO queue dynamics, buffer limit, packet drops, and transmission delays.
    - Dynamically models bufferbloat and queue buildup when traffic exceeds link capacity.
    """

    def __init__(self, config: TopologyConfig):
        super().__init__(config)
        self.lock = threading.Lock()
        self.backlog_bytes = 0
        self.backlog_packets = 0
        self.total_dropped = 0
        self.overlimits = 0
        self.last_update = time.time()
        self.queue_monitor = QueueMonitor(
            interface="sim0",
            netns=None,
            capacity_mbps=config.initial_bandwidth_mbps,
            sim_queue_callback=self._get_sim_metrics
        )

    def setup(self) -> None:
        logger.info("Initializing Simulated bottleneck topology...")
        with self.lock:
            self.backlog_bytes = 0
            self.backlog_packets = 0
            self.total_dropped = 0
            self.last_update = time.time()

    def teardown(self) -> None:
        logger.info("Tearing down Simulated bottleneck topology.")

    def set_wan_bandwidth(self, bandwidth_mbps: float, rtt_ms: Optional[float] = None) -> None:
        logger.info(f"Simulated WAN capacity altered to {bandwidth_mbps} Mbps")
        with self.lock:
            self._drain_queue()
            self.current_bandwidth_mbps = bandwidth_mbps
            self.queue_monitor.set_capacity(bandwidth_mbps)

    def enqueue_packet(self, packet_bytes: int) -> bool:
        """
        Simulate packet arrival at bottleneck queue.
        Returns True if packet accepted, False if dropped due to queue full.
        """
        with self.lock:
            self._drain_queue()
            max_bytes = self.config.buffer_queue_limit_packets * 1500
            if self.backlog_bytes + packet_bytes > max_bytes:
                self.total_dropped += 1
                self.overlimits += 1
                return False

            self.backlog_bytes += packet_bytes
            self.backlog_packets += 1
            return True

    def _drain_queue(self) -> None:
        """Drain queued bytes based on elapsed time and current link capacity."""
        now = time.time()
        elapsed = now - self.last_update
        self.last_update = now

        # Drain rate in bytes per second
        drain_rate_bytes_sec = (self.current_bandwidth_mbps * 1_000_000.0) / 8.0
        bytes_drained = int(drain_rate_bytes_sec * elapsed)

        if bytes_drained > 0:
            self.backlog_bytes = max(0, self.backlog_bytes - bytes_drained)
            if self.backlog_bytes == 0:
                self.backlog_packets = 0
            else:
                self.backlog_packets = max(1, int(self.backlog_bytes / 1400))

    def _get_sim_metrics(self) -> QueueMetrics:
        with self.lock:
            self._drain_queue()
            return QueueMetrics(
                timestamp=time.time(),
                interface_name="sim0",
                capacity_mbps=self.current_bandwidth_mbps,
                backlog_bytes=self.backlog_bytes,
                backlog_packets=self.backlog_packets,
                dropped_packets=self.total_dropped,
                overlimit_count=self.overlimits,
            )

    def get_queue_metrics(self) -> QueueMetrics:
        return self.queue_monitor.collect()
