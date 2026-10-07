"""Non-payload statistical and behavioral feature extraction for traffic flows."""

from typing import List, Dict, Optional, Deque
from collections import deque
import statistics
import time

from adaptive_qos.common.models import FlowMetrics, FlowConfig
from adaptive_qos.classification.models import FlowFeatures


class FlowFeatureExtractor:
    """
    Extracts statistical, temporal, and volumetric features from observable network traffic.
    Maintains a sliding history per flow to capture burstiness and duty-cycle patterns.
    Strictly complies with privacy constraints: ZERO payload inspection or decryption.
    """

    def __init__(self, history_window_size: int = 10):
        self.history_window_size = history_window_size
        self._flow_history: Dict[str, Deque[FlowMetrics]] = {}
        self._first_seen: Dict[str, float] = {}
        self._last_pkts: Dict[str, int] = {}
        self._last_time: Dict[str, float] = {}

    def extract(
        self,
        current_metric: FlowMetrics,
        flow_config: Optional[FlowConfig] = None,
        elapsed_sec: Optional[float] = None,
    ) -> FlowFeatures:
        """Extract rich behavioral features from the current metric and recent sliding window."""
        flow_id = current_metric.flow_id
        now = time.time()

        if flow_id not in self._first_seen:
            self._first_seen[flow_id] = now
            self._flow_history[flow_id] = deque(maxlen=self.history_window_size)
            self._last_pkts[flow_id] = current_metric.packets_recv if current_metric.packets_recv > 0 else current_metric.packets_sent
            self._last_time[flow_id] = now

        self._flow_history[flow_id].append(current_metric)
        history = list(self._flow_history[flow_id])

        # 1. Packet Size Estimation
        if current_metric.packets_recv > 0 and current_metric.bytes_recv > 0:
            avg_packet_size = float(current_metric.bytes_recv) / float(current_metric.packets_recv)
        elif current_metric.packets_sent > 0 and current_metric.bytes_sent > 0:
            avg_packet_size = float(current_metric.bytes_sent) / float(current_metric.packets_sent)
        elif flow_config and flow_config.packet_size_bytes:
            avg_packet_size = float(flow_config.packet_size_bytes)
        else:
            avg_packet_size = 1200.0

        # 2. Window Packet Rate (packets per second)
        current_cum_pkts = current_metric.packets_recv if current_metric.packets_recv > 0 else current_metric.packets_sent
        dt = max(0.1, now - self._last_time[flow_id])
        delta_pkts = max(0, current_cum_pkts - self._last_pkts[flow_id])
        
        if delta_pkts > 0 and dt > 0:
            packet_rate = delta_pkts / dt
        elif current_metric.throughput_mbps > 0 and avg_packet_size > 0:
            packet_rate = (current_metric.throughput_mbps * 1_000_000.0 / 8.0) / avg_packet_size
        else:
            packet_rate = 0.0

        self._last_pkts[flow_id] = current_cum_pkts
        self._last_time[flow_id] = now

        # 3. Flow Duration
        if elapsed_sec is not None:
            flow_duration = elapsed_sec
        else:
            flow_duration = max(0.1, now - self._first_seen[flow_id])

        # 4. Burstiness & Duty Cycle from history
        throughput_history = [h.throughput_mbps for h in history]
        if throughput_history and max(throughput_history) > 0:
            peak_tput = max(throughput_history)
            avg_tput = sum(throughput_history) / len(throughput_history)
            burstiness_ratio = (peak_tput / max(0.01, avg_tput))
            active_windows = sum(1 for t in throughput_history if t > 0.05)
            duty_cycle = active_windows / len(throughput_history)
        else:
            burstiness_ratio = 1.0
            duty_cycle = 1.0 if current_metric.throughput_mbps > 0 else 0.0

        # 5. Protocol and Port Metadata
        protocol_str = flow_config.protocol.value.upper() if flow_config else "UDP"
        src_port = flow_config.src_port if flow_config else None
        dst_port = flow_config.dst_port if flow_config else None

        return FlowFeatures(
            flow_id=flow_id,
            protocol=protocol_str,
            packet_rate_pps=round(packet_rate, 1),
            throughput_mbps=round(current_metric.throughput_mbps, 3),
            avg_packet_size_bytes=round(avg_packet_size, 1),
            flow_duration_sec=round(flow_duration, 1),
            latency_ms=round(current_metric.latency_ms, 2),
            jitter_ms=round(current_metric.jitter_ms, 2),
            packet_loss_pct=round(current_metric.packet_loss_pct, 2),
            burstiness_ratio=round(burstiness_ratio, 2),
            duty_cycle=round(duty_cycle, 2),
            src_port=src_port,
            dst_port=dst_port,
        )

    def reset(self) -> None:
        """Clear all historical state."""
        self._flow_history.clear()
        self._first_seen.clear()
        self._last_pkts.clear()
        self._last_time.clear()
