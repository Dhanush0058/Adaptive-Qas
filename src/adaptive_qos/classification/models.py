"""Data models for Phase 2 Traffic Classification and Link Capacity Estimation."""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any
import time


class ClassificationCategory(str, Enum):
    """Broad traffic classification categories for Phase 2."""
    INTERACTIVE = "interactive"
    GAMING = "gaming"
    VIDEO_CALL = "video_call"
    VIDEO_STREAMING = "video_streaming"
    BULK_DOWNLOAD = "bulk_download"
    BACKGROUND_UNKNOWN = "background_unknown"


class DataSource(str, Enum):
    """Explicitly tags data origin for integrity and validation."""
    LINUX_REAL = "linux_real"
    SIMULATION = "simulation"


class LinkState(str, Enum):
    """Observable operational state of the bottleneck link."""
    UNCONGESTED_IDLE = "uncongested_idle"
    UNCONGESTED_LOADED = "uncongested_loaded"
    CONGESTED_SATURATED = "congested_saturated"
    CONGESTED_DROPPING = "congested_dropping"
    CAPACITY_TRANSITION = "capacity_transition"


@dataclass
class FlowFeatures:
    """Observable non-payload statistical and behavioral flow features."""
    flow_id: str
    protocol: str                          # "UDP" or "TCP"
    packet_rate_pps: float                 # packets per second
    throughput_mbps: float                 # bits per second / 1e6
    avg_packet_size_bytes: float           # bytes per packet
    min_packet_size_bytes: int = 0
    max_packet_size_bytes: int = 0
    flow_duration_sec: float = 0.0
    latency_ms: float = 0.0                # transit delay or RTT
    jitter_ms: float = 0.0                 # RFC 3550 jitter
    packet_loss_pct: float = 0.0           # packet loss percentage
    burstiness_ratio: float = 1.0          # peak_rate / avg_rate
    duty_cycle: float = 1.0                # active_time / total_window
    src_port: Optional[int] = None
    dst_port: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FlowClassificationResult:
    """Classification outcome and computed confidence for a single flow."""
    flow_id: str
    classification: ClassificationCategory
    confidence: float                      # Computed confidence score [0.0, 1.0]
    category_scores: Dict[str, float]      # Computed similarity score per category
    features: FlowFeatures
    timestamp: float = field(default_factory=time.time)
    data_source: DataSource = DataSource.SIMULATION
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "classification": self.classification.value,
            "confidence": round(self.confidence, 3),
            "category_scores": {k: round(v, 3) for k, v in self.category_scores.items()},
            "features": self.features.to_dict(),
            "timestamp": self.timestamp,
            "data_source": self.data_source.value,
            "reasoning": self.reasoning,
        }


@dataclass
class LinkCapacityEstimate:
    """Estimated available WAN bottleneck capacity based on observed network dynamics."""
    timestamp: float
    configured_capacity_mbps: Optional[float]
    estimated_capacity_mbps: float
    confidence: float                      # Computed confidence [0.0, 1.0]
    link_state: LinkState
    measurement_window_seconds: float
    observed_throughput_mbps: float
    queue_backlog_bytes: int = 0
    queue_drops: int = 0
    latency_elevation_ms: float = 0.0
    data_source: DataSource = DataSource.SIMULATION
    detected_change: bool = False
    change_description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "configured_capacity_mbps": self.configured_capacity_mbps,
            "estimated_capacity_mbps": round(self.estimated_capacity_mbps, 2),
            "confidence": round(self.confidence, 3),
            "link_state": self.link_state.value,
            "measurement_window_seconds": round(self.measurement_window_seconds, 1),
            "observed_throughput_mbps": round(self.observed_throughput_mbps, 2),
            "queue_backlog_bytes": self.queue_backlog_bytes,
            "queue_drops": self.queue_drops,
            "latency_elevation_ms": round(self.latency_elevation_ms, 2),
            "data_source": self.data_source.value,
            "detected_change": self.detected_change,
            "change_description": self.change_description,
        }
