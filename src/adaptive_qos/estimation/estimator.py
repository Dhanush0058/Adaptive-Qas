"""Link-Capacity Estimator based on real observable network traffic and queue dynamics."""

from typing import List, Optional, Deque, Dict, Any
from collections import deque
import statistics
import time

from adaptive_qos.common.models import TimeSliceSnapshot, QueueMetrics
from adaptive_qos.classification.models import LinkCapacityEstimate, LinkState, DataSource
from adaptive_qos.common.logger import get_logger

logger = get_logger("estimation.link_capacity")


class LinkCapacityEstimator:
    """
    Dynamically estimates available WAN bottleneck capacity from observed throughput,
    queue backlog pressure, packet drops, and transit latency elevation.

    Distinguishes CONFIGURED vs ESTIMATED capacity with computed confidence scores.
    """

    def __init__(
        self,
        window_size_sec: float = 3.0,
        base_rtt_ms: float = 15.0,
        data_source: DataSource = DataSource.SIMULATION,
        default_capacity_mbps: float = 100.0,
    ):
        self.window_size_sec = window_size_sec
        self.base_rtt_ms = base_rtt_ms
        self.data_source = data_source

        self._snapshot_history: Deque[TimeSliceSnapshot] = deque(maxlen=int(window_size_sec * 2 + 5))
        self._current_estimate_mbps: float = default_capacity_mbps
        self._last_detected_capacity: float = default_capacity_mbps
        self._consecutive_saturated_samples = 0
        self._last_drops = 0

    def update(self, snapshot: TimeSliceSnapshot) -> LinkCapacityEstimate:
        """Process a new synchronized snapshot and calculate current capacity estimate."""
        self._snapshot_history.append(snapshot)
        now = snapshot.timestamp

        # 1. Aggregate current observed throughput across all flows
        current_aggregate_tput = sum(
            f.throughput_mbps for f in snapshot.flows.values() if f.throughput_mbps > 0
        )

        # 2. Extract queue and latency metrics
        backlog_bytes = snapshot.queue.backlog_bytes if snapshot.queue else 0
        current_drops = snapshot.queue.dropped_packets if snapshot.queue else 0
        new_drops = max(0, current_drops - self._last_drops)
        self._last_drops = current_drops

        # Determine average latency elevation across flows / probes
        active_latencies = [
            f.latency_ms for f in snapshot.flows.values() if f.latency_ms > 0
        ]
        avg_latency = (
            sum(active_latencies) / len(active_latencies) if active_latencies else self.base_rtt_ms
        )
        latency_elevation = max(0.0, avg_latency - self.base_rtt_ms)

        # 3. Determine Link Saturation State
        is_saturated = (
            (backlog_bytes > 1500)
            or (new_drops > 0)
            or (latency_elevation > 15.0)
            or (snapshot.is_congested and current_aggregate_tput > 12.0)
        )

        if new_drops > 0:
            link_state = LinkState.CONGESTED_DROPPING
        elif is_saturated:
            link_state = LinkState.CONGESTED_SATURATED
        elif current_aggregate_tput > 15.0:
            link_state = LinkState.UNCONGESTED_LOADED
        else:
            link_state = LinkState.UNCONGESTED_IDLE

        # 4. Compute Windowed Throughput Statistics
        recent_snapshots = [
            s for s in self._snapshot_history
            if (now - s.timestamp) <= self.window_size_sec or len(self._snapshot_history) <= 3
        ]
        window_tputs = [
            sum(f.throughput_mbps for f in s.flows.values() if f.throughput_mbps > 0)
            for s in recent_snapshots
        ]
        avg_window_tput = (
            sum(window_tputs) / len(window_tputs) if window_tputs else current_aggregate_tput
        )

        # 5. Capacity Estimation Logic with Adaptive Tracking Alpha
        detected_change = False
        change_desc = ""

        if is_saturated:
            # Under saturation, observed throughput reflects actual bottleneck capacity ceiling
            self._consecutive_saturated_samples += 1
            raw_sample = max(1.0, avg_window_tput)

            # Adaptive alpha: fast tracking when error is large, smoother in steady state
            rel_error = abs(raw_sample - self._current_estimate_mbps) / max(1.0, self._current_estimate_mbps)
            alpha = 0.75 if rel_error > 0.30 else 0.40

            self._current_estimate_mbps = (
                alpha * raw_sample + (1.0 - alpha) * self._current_estimate_mbps
            )

            # High confidence because we have physical evidence (queue/delay/drops)
            confidence = min(0.96, 0.82 + 0.04 * min(3, self._consecutive_saturated_samples))

            # Detect step-change (e.g. 100M -> 20M)
            if abs(self._current_estimate_mbps - self._last_detected_capacity) / max(1.0, self._last_detected_capacity) > 0.25:
                detected_change = True
                change_desc = (
                    f"Bottleneck throttle detected: {self._last_detected_capacity:.0f}M -> {self._current_estimate_mbps:.1f}M "
                    f"(queue: {backlog_bytes}B, drops: {current_drops})"
                )
                self._last_detected_capacity = self._current_estimate_mbps

        else:
            self._consecutive_saturated_samples = 0
            # Link is not saturated; observed throughput is lower bound on capacity
            if avg_window_tput > self._current_estimate_mbps:
                # Upward step detected (e.g. recovered from 20M to 100M)
                raw_sample = avg_window_tput
                rel_error = abs(raw_sample - self._current_estimate_mbps) / max(1.0, self._current_estimate_mbps)
                alpha = 0.75 if rel_error > 0.30 else 0.40
                self._current_estimate_mbps = (
                    alpha * raw_sample + (1.0 - alpha) * self._current_estimate_mbps
                )
                confidence = 0.85
                if abs(self._current_estimate_mbps - self._last_detected_capacity) / max(1.0, self._last_detected_capacity) > 0.25:
                    detected_change = True
                    change_desc = f"Capacity expansion detected: {self._last_detected_capacity:.0f}M -> {self._current_estimate_mbps:.1f}M"
                    self._last_detected_capacity = self._current_estimate_mbps
            else:
                # Idle or light load: maintain last high-confidence capacity but reduce current confidence
                confidence = 0.60 if current_aggregate_tput > 5.0 else 0.40

        return LinkCapacityEstimate(
            timestamp=now,
            configured_capacity_mbps=snapshot.wan_capacity_mbps,
            estimated_capacity_mbps=round(self._current_estimate_mbps, 2),
            confidence=round(confidence, 3),
            link_state=link_state,
            measurement_window_seconds=self.window_size_sec,
            observed_throughput_mbps=round(current_aggregate_tput, 2),
            queue_backlog_bytes=backlog_bytes,
            queue_drops=current_drops,
            latency_elevation_ms=round(latency_elevation, 2),
            data_source=self.data_source,
            detected_change=detected_change,
            change_description=change_desc,
        )

    def set_data_source(self, data_source: DataSource) -> None:
        self.data_source = data_source

    def reset(self) -> None:
        self._snapshot_history.clear()
        self._current_estimate_mbps = 100.0
        self._last_detected_capacity = 100.0
        self._consecutive_saturated_samples = 0
        self._last_drops = 0
