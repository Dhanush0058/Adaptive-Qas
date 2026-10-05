"""Data models for Adaptive QoS engine simulation, traffic generation, and metrics."""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
import json
import time


class TrafficType(str, Enum):
    """Supported traffic classes."""
    GAMING = "gaming"
    VIDEO_CALL = "video_call"
    VIDEO_STREAMING = "video_streaming"
    BULK_DOWNLOAD = "bulk_download"
    PROBE = "probe"


class TransportProtocol(str, Enum):
    UDP = "udp"
    TCP = "tcp"


@dataclass
class FlowConfig:
    """Configuration for a specific traffic flow."""
    flow_id: str
    traffic_type: TrafficType
    protocol: TransportProtocol
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    target_rate_mbps: float = 0.0  # 0 for unbounded/bulk
    packet_size_bytes: int = 1400
    duration_sec: float = 30.0
    start_offset_sec: float = 0.0
    burst_interval_ms: Optional[float] = None
    burst_size_packets: Optional[int] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["traffic_type"] = self.traffic_type.value
        d["protocol"] = self.protocol.value
        return d


@dataclass
class FlowMetrics:
    """Performance metrics for a flow over a specific time window or cumulative."""
    flow_id: str
    traffic_type: TrafficType
    packets_sent: int = 0
    packets_recv: int = 0
    bytes_sent: int = 0
    bytes_recv: int = 0
    throughput_mbps: float = 0.0
    latency_ms: float = 0.0          # Current / average RTT or one-way delay
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    jitter_ms: float = 0.0           # RFC 3550 style delay variation
    packet_loss_pct: float = 0.0     # Percentage of lost packets

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["traffic_type"] = self.traffic_type.value
        return d


@dataclass
class QueueMetrics:
    """Router / WAN interface queue and buffer metrics."""
    timestamp: float
    interface_name: str
    capacity_mbps: float
    backlog_bytes: int = 0
    backlog_packets: int = 0
    dropped_packets: int = 0
    overlimit_count: int = 0
    requeues: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TimeSliceSnapshot:
    """A synchronized snapshot of all flows and queues at a given second."""
    timestamp: float
    elapsed_sec: float
    wan_capacity_mbps: float
    is_congested: bool
    flows: Dict[str, FlowMetrics] = field(default_factory=dict)
    queue: Optional[QueueMetrics] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "elapsed_sec": self.elapsed_sec,
            "wan_capacity_mbps": self.wan_capacity_mbps,
            "is_congested": self.is_congested,
            "flows": {k: v.to_dict() for k, v in self.flows.items()},
            "queue": self.queue.to_dict() if self.queue else None,
        }


@dataclass
class TopologyConfig:
    """Configuration for the simulated or Linux namespace network topology."""
    lan_ip: str = "10.0.1.2"
    lan_subnet: str = "10.0.1.0/24"
    gw_lan_ip: str = "10.0.1.1"
    gw_wan_ip: str = "10.0.2.1"
    wan_ip: str = "10.0.2.2"
    wan_subnet: str = "10.0.2.0/24"
    initial_bandwidth_mbps: float = 100.0
    congested_bandwidth_mbps: float = 20.0
    wan_base_rtt_ms: float = 15.0
    buffer_queue_limit_packets: int = 100

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentConfig:
    """Configuration for an end-to-end experiment run."""
    experiment_id: str
    name: str
    description: str
    topology: TopologyConfig = field(default_factory=TopologyConfig)
    total_duration_sec: float = 30.0
    congestion_start_sec: float = 10.0
    congestion_end_sec: float = 25.0
    sampling_interval_sec: float = 1.0
    flows: List[FlowConfig] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "description": self.description,
            "topology": self.topology.to_dict(),
            "total_duration_sec": self.total_duration_sec,
            "congestion_start_sec": self.congestion_start_sec,
            "congestion_end_sec": self.congestion_end_sec,
            "sampling_interval_sec": self.sampling_interval_sec,
            "flows": [f.to_dict() for f in self.flows],
        }


@dataclass
class ExperimentResult:
    """Full outcome of an experiment run."""
    experiment_id: str
    start_time: float
    end_time: float
    config: ExperimentConfig
    snapshots: List[TimeSliceSnapshot] = field(default_factory=list)
    summary_by_flow: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary_uncongested: Dict[str, Any] = field(default_factory=dict)
    summary_congested: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "config": self.config.to_dict(),
            "snapshots": [s.to_dict() for s in self.snapshots],
            "summary_by_flow": self.summary_by_flow,
            "summary_uncongested": self.summary_uncongested,
            "summary_congested": self.summary_congested,
        }
