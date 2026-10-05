"""Metrics Collector coordinating passive flows, active probes, and queue monitoring."""

from typing import Dict, List, Optional, Any
import time
from adaptive_qos.common.models import (
    FlowConfig,
    FlowMetrics,
    QueueMetrics,
    TimeSliceSnapshot,
    ExperimentConfig,
    ExperimentResult,
)
from adaptive_qos.common.logger import get_logger
from adaptive_qos.monitoring.passive import PassiveTrafficMonitor
from adaptive_qos.monitoring.probes import ActiveProbe, ProbeEchoResponder
from adaptive_qos.monitoring.queue_monitor import QueueMonitor

logger = get_logger("monitoring.collector")


class MetricsCollector:
    """Aggregates all monitoring sources and produces periodic synchronized snapshots."""

    def __init__(
        self,
        flows: List[FlowConfig],
        queue_monitor: Optional[QueueMonitor] = None,
        enable_active_probe: bool = True,
        probe_target_ip: str = "127.0.0.1",
        probe_target_port: int = 5099,
        bind_ip: str = "0.0.0.0",
    ):
        self.flows = flows
        self.queue_monitor = queue_monitor
        self.passive_monitor = PassiveTrafficMonitor(flows, bind_ip=bind_ip)
        self.active_probe = (
            ActiveProbe(probe_target_ip, probe_target_port) if enable_active_probe else None
        )
        self.probe_echo = (
            ProbeEchoResponder(bind_ip=bind_ip, bind_port=probe_target_port)
            if enable_active_probe
            else None
        )
        self.is_running = False
        self.snapshots: List[TimeSliceSnapshot] = []

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self.snapshots.clear()
        if self.probe_echo:
            self.probe_echo.start()
        self.passive_monitor.start()
        if self.active_probe:
            self.active_probe.start()
        logger.info("Metrics collector started.")

    def collect_timeslice(
        self, elapsed_sec: float, wan_capacity_mbps: float, is_congested: bool, window_sec: float = 1.0
    ) -> TimeSliceSnapshot:
        """Capture synchronized snapshot of all flows and queue."""
        flow_metrics = self.passive_monitor.collect_snapshot(window_sec)
        
        if self.active_probe:
            probe_m = self.active_probe.collect_and_reset_window()
            flow_metrics["active_probe"] = probe_m

        queue_m = self.queue_monitor.collect() if self.queue_monitor else None

        snapshot = TimeSliceSnapshot(
            timestamp=time.time(),
            elapsed_sec=elapsed_sec,
            wan_capacity_mbps=wan_capacity_mbps,
            is_congested=is_congested,
            flows=flow_metrics,
            queue=queue_m,
        )
        self.snapshots.append(snapshot)
        return snapshot

    def stop(self) -> None:
        if not self.is_running:
            return
        self.passive_monitor.stop()
        if self.active_probe:
            self.active_probe.stop()
        if self.probe_echo:
            self.probe_echo.stop()
        self.is_running = False
        logger.info(f"Metrics collector stopped. Collected {len(self.snapshots)} snapshots.")

    def compile_result(self, config: ExperimentConfig, start_time: float, end_time: float) -> ExperimentResult:
        """Calculate aggregate summaries for uncongested vs congested phases."""
        uncong_snapshots = [s for s in self.snapshots if not s.is_congested]
        cong_snapshots = [s for s in self.snapshots if s.is_congested]

        def compute_phase_summary(snaps: List[TimeSliceSnapshot]) -> Dict[str, Any]:
            if not snaps:
                return {}
            flow_data: Dict[str, Dict[str, float]] = {}
            all_flow_ids = set()
            for s in snaps:
                all_flow_ids.update(s.flows.keys())

            for fid in all_flow_ids:
                lats = [s.flows[fid].latency_ms for s in snaps if fid in s.flows and s.flows[fid].latency_ms > 0]
                jits = [s.flows[fid].jitter_ms for s in snaps if fid in s.flows and s.flows[fid].jitter_ms > 0]
                losses = [s.flows[fid].packet_loss_pct for s in snaps if fid in s.flows]
                tputs = [s.flows[fid].throughput_mbps for s in snaps if fid in s.flows]

                flow_data[fid] = {
                    "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else 0.0,
                    "max_latency_ms": round(max(lats), 2) if lats else 0.0,
                    "avg_jitter_ms": round(sum(jits) / len(jits), 2) if jits else 0.0,
                    "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else 0.0,
                    "avg_throughput_mbps": round(sum(tputs) / len(tputs), 2) if tputs else 0.0,
                }

            avg_queue_backlog = 0.0
            total_queue_drops = 0
            q_backlogs = [s.queue.backlog_bytes for s in snaps if s.queue]
            if q_backlogs:
                avg_queue_backlog = sum(q_backlogs) / len(q_backlogs)
            if snaps and snaps[-1].queue:
                total_queue_drops = snaps[-1].queue.dropped_packets

            return {
                "snapshot_count": len(snaps),
                "flows": flow_data,
                "avg_queue_backlog_bytes": round(avg_queue_backlog, 1),
                "total_queue_drops": total_queue_drops,
            }

        uncong_summary = compute_phase_summary(uncong_snapshots)
        cong_summary = compute_phase_summary(cong_snapshots)

        # Global flow summary
        summary_by_flow = {}
        for flow in config.flows:
            summary_by_flow[flow.flow_id] = {
                "traffic_type": flow.traffic_type.value,
                "target_rate_mbps": flow.target_rate_mbps,
            }
        if self.active_probe:
            summary_by_flow["active_probe"] = {
                "traffic_type": "probe",
                "target_rate_mbps": 0.0,
            }

        return ExperimentResult(
            experiment_id=config.experiment_id,
            start_time=start_time,
            end_time=end_time,
            config=config,
            snapshots=self.snapshots,
            summary_by_flow=summary_by_flow,
            summary_uncongested=uncong_summary,
            summary_congested=cong_summary,
        )
