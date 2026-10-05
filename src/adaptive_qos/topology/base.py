"""Base network topology interface."""

import abc
from typing import Optional
from adaptive_qos.common.models import TopologyConfig, QueueMetrics


class BaseTopology(abc.ABC):
    """Abstract interface for managing simulated or Linux namespace network topologies."""

    def __init__(self, config: TopologyConfig):
        self.config = config
        self.current_bandwidth_mbps = config.initial_bandwidth_mbps

    @abc.abstractmethod
    def setup(self) -> None:
        """Create interfaces, namespaces, routing rules, and initial traffic control shapers."""
        pass

    @abc.abstractmethod
    def teardown(self) -> None:
        """Clean up namespaces, interfaces, and qdiscs."""
        pass

    @abc.abstractmethod
    def set_wan_bandwidth(self, bandwidth_mbps: float, rtt_ms: Optional[float] = None) -> None:
        """Dynamically throttle or restore WAN link bandwidth and latency."""
        pass

    @abc.abstractmethod
    def get_queue_metrics(self) -> QueueMetrics:
        """Retrieve current queue backlog and drop metrics."""
        pass
