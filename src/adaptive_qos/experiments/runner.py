"""Orchestrator for executing experiments across Linux Network Namespaces or Simulation."""

import json
import os
import subprocess
import sys
import time
from typing import Optional, List, Dict, Any

from adaptive_qos.common.models import (
    ExperimentConfig,
    ExperimentResult,
    TimeSliceSnapshot,
    FlowMetrics,
    TrafficType,
)
from adaptive_qos.common.logger import get_logger
from adaptive_qos.topology.base import BaseTopology
from adaptive_qos.topology.linux_netns import LinuxNetnsTopology
from adaptive_qos.traffic.runner import TrafficRunner
from adaptive_qos.monitoring.collector import MetricsCollector
from adaptive_qos.monitoring.storage import MetricsStorage

logger = get_logger("experiment.runner")


class ExperimentRunner:
    """
    Executes an end-to-end network experiment:
    - In Linux Mode: Dispatches receiver to `wan_server`, generators to `lan_client`, and manages queue in `qos_gw`.
    - In Simulated Mode: Runs in-process multi-flow threads and local simulation queue.
    """

    def __init__(self, config: ExperimentConfig, topology: BaseTopology, output_base_dir: str = "results"):
        self.config = config
        self.topology = topology
        self.output_dir = os.path.join(output_base_dir, f"{config.experiment_id}_{int(time.time())}")
        os.makedirs(self.output_dir, exist_ok=True)
        self.storage = MetricsStorage(self.output_dir)

    def run(self) -> ExperimentResult:
        logger.info(f"=== Starting Experiment: {self.config.name} ===")
        logger.info(
            f"Duration: {self.config.total_duration_sec}s | "
            f"Congestion: {self.config.congestion_start_sec}s - {self.config.congestion_end_sec}s"
        )

        if isinstance(self.topology, LinuxNetnsTopology):
            return self._run_linux_netns()
        else:
            return self._run_in_process()

    def _run_in_process(self) -> ExperimentResult:
        """Runs traffic and monitoring in-process (used for local simulation)."""
        traffic_runner = TrafficRunner(self.config.flows)
        collector = MetricsCollector(
            flows=self.config.flows,
            queue_monitor=self.topology.queue_monitor if hasattr(self.topology, "queue_monitor") else None,
            enable_active_probe=True,
            probe_target_ip=self.config.flows[0].dst_ip if self.config.flows else "127.0.0.1",
            probe_target_port=5099,
        )

        self.topology.setup()
        start_time = time.time()
        end_time = start_time + self.config.total_duration_sec

        try:
            collector.start()
            traffic_runner.start_all()

            current_bandwidth = self.config.topology.initial_bandwidth_mbps
            is_congested = False
            last_sample_time = time.time()

            while time.time() < end_time:
                now = time.time()
                elapsed = now - start_time

                if self.config.congestion_start_sec <= elapsed < self.config.congestion_end_sec and not is_congested:
                    is_congested = True
                    current_bandwidth = self.config.topology.congested_bandwidth_mbps
                    logger.warning(f"--- [CONGESTION INJECTED] Throttling WAN link to {current_bandwidth} Mbps ---")
                    self.topology.set_wan_bandwidth(current_bandwidth)

                elif elapsed >= self.config.congestion_end_sec and is_congested:
                    is_congested = False
                    current_bandwidth = self.config.topology.initial_bandwidth_mbps
                    logger.info(f"--- [CONGESTION CLEARED] Restoring WAN link to {current_bandwidth} Mbps ---")
                    self.topology.set_wan_bandwidth(current_bandwidth)

                if now - last_sample_time >= self.config.sampling_interval_sec:
                    snapshot = collector.collect_timeslice(
                        elapsed_sec=elapsed,
                        wan_capacity_mbps=current_bandwidth,
                        is_congested=is_congested,
                        window_sec=now - last_sample_time,
                    )
                    last_sample_time = now
                    self._log_snapshot_summary(snapshot)

                time.sleep(0.1)

        finally:
            traffic_runner.stop_all()
            collector.stop()
            self.topology.teardown()

        result = collector.compile_result(self.config, start_time, time.time())
        self.storage.save_experiment_result(result)
        logger.info(f"=== Experiment Completed Successfully ===")
        logger.info(f"Results stored in: {self.output_dir}")
        return result

    def _run_linux_netns(self) -> ExperimentResult:
        """Runs traffic generators in `lan_client`, receiver in `wan_server`, and manages queue in `qos_gw`."""
        self.topology.setup()
        start_time = time.time()

        cfg_file = os.path.join(self.output_dir, "run_config.json")
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(self.config.to_dict(), f, indent=2)

        rx_output = os.path.join(self.output_dir, "rx_snapshots.json")
        tx_output = os.path.join(self.output_dir, "tx_probe_snapshots.json")

        env = os.environ.copy()
        pythonpath = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        env["PYTHONPATH"] = f"{pythonpath}:{env.get('PYTHONPATH', '')}"

        # 1. Start Receiver Daemon in `wan_server` namespace
        rx_cmd = [
            "ip", "netns", "exec", "wan_server",
            sys.executable, "-m", "adaptive_qos.monitoring.receiver_daemon",
            "--config", cfg_file,
            "--output", rx_output,
            "--duration", str(self.config.total_duration_sec + 2.0),
        ]
        logger.info("Launching Receiver Daemon in `wan_server` namespace...")
        rx_proc = subprocess.Popen(rx_cmd, env=env)

        time.sleep(0.5)  # Let receiver bind sockets

        # 2. Start Sender Daemon in `lan_client` namespace
        tx_cmd = [
            "ip", "netns", "exec", "lan_client",
            sys.executable, "-m", "adaptive_qos.traffic.sender_daemon",
            "--config", cfg_file,
            "--output", tx_output,
            "--duration", str(self.config.total_duration_sec),
        ]
        logger.info("Launching Sender Flows in `lan_client` namespace...")
        tx_proc = subprocess.Popen(tx_cmd, env=env)

        current_bandwidth = self.config.topology.initial_bandwidth_mbps
        is_congested = False
        queue_snapshots: List[Dict[str, Any]] = []

        try:
            while tx_proc.poll() is None:
                now = time.time()
                elapsed = now - start_time

                if self.config.congestion_start_sec <= elapsed < self.config.congestion_end_sec and not is_congested:
                    is_congested = True
                    current_bandwidth = self.config.topology.congested_bandwidth_mbps
                    logger.warning(f"--- [CONGESTION INJECTED] Throttling WAN link to {current_bandwidth} Mbps ---")
                    self.topology.set_wan_bandwidth(current_bandwidth)

                elif elapsed >= self.config.congestion_end_sec and is_congested:
                    is_congested = False
                    current_bandwidth = self.config.topology.initial_bandwidth_mbps
                    logger.info(f"--- [CONGESTION CLEARED] Restoring WAN link to {current_bandwidth} Mbps ---")
                    self.topology.set_wan_bandwidth(current_bandwidth)

                # Query TC queue in qos_gw
                q_metrics = self.topology.get_queue_metrics()
                queue_snapshots.append({
                    "timestamp": now,
                    "elapsed_sec": round(elapsed, 2),
                    "wan_capacity_mbps": current_bandwidth,
                    "is_congested": is_congested,
                    "queue": q_metrics.to_dict(),
                })
                time.sleep(1.0)

            tx_proc.wait()
            rx_proc.wait(timeout=5.0)

        finally:
            if tx_proc.poll() is None:
                tx_proc.terminate()
            if rx_proc.poll() is None:
                rx_proc.terminate()
            self.topology.teardown()

        end_time = time.time()

        # 3. Fuse RX, TX Probe, and Queue Snapshots
        result = self._fuse_linux_results(rx_output, tx_output, queue_snapshots, start_time, end_time)
        self.storage.save_experiment_result(result)
        logger.info(f"=== Linux Netns Experiment Completed Successfully ===")
        logger.info(f"Results stored in: {self.output_dir}")
        return result

    def _fuse_linux_results(
        self,
        rx_path: str,
        tx_path: str,
        queue_snapshots: List[Dict[str, Any]],
        start_time: float,
        end_time: float,
    ) -> ExperimentResult:
        """Merge receiver, probe, and queue snapshots into unified ExperimentResult."""
        rx_snaps = []
        if os.path.exists(rx_path):
            with open(rx_path, "r", encoding="utf-8") as f:
                rx_snaps = json.load(f)

        tx_snaps = []
        if os.path.exists(tx_path):
            with open(tx_path, "r", encoding="utf-8") as f:
                tx_snaps = json.load(f)

        tx_probe_map = {int(round(s.get("elapsed_sec", 0))): s.get("probe", {}) for s in tx_snaps}

        unified_snapshots: List[TimeSliceSnapshot] = []

        for i, q in enumerate(queue_snapshots):
            sec_idx = int(round(q.get("elapsed_sec", 0)))
            
            # Find matching RX snapshot
            matching_rx = None
            for rx in rx_snaps:
                if int(round(rx.get("elapsed_sec", 0))) == sec_idx:
                    matching_rx = rx
                    break

            flows_dict: Dict[str, FlowMetrics] = {}
            if matching_rx and "flows" in matching_rx:
                for fid, fd in matching_rx["flows"].items():
                    flows_dict[fid] = FlowMetrics(
                        flow_id=fd["flow_id"],
                        traffic_type=TrafficType(fd["traffic_type"]),
                        packets_sent=fd.get("packets_sent", 0),
                        packets_recv=fd.get("packets_recv", 0),
                        bytes_recv=fd.get("bytes_recv", 0),
                        throughput_mbps=fd.get("throughput_mbps", 0.0),
                        latency_ms=fd.get("latency_ms", 0.0),
                        min_latency_ms=fd.get("min_latency_ms", 0.0),
                        max_latency_ms=fd.get("max_latency_ms", 0.0),
                        p95_latency_ms=fd.get("p95_latency_ms", 0.0),
                        jitter_ms=fd.get("jitter_ms", 0.0),
                        packet_loss_pct=fd.get("packet_loss_pct", 0.0),
                    )

            # Add probe metrics
            if sec_idx in tx_probe_map:
                pm = tx_probe_map[sec_idx]
                flows_dict["active_probe"] = FlowMetrics(
                    flow_id="active_probe",
                    traffic_type=TrafficType.PROBE,
                    packets_sent=pm.get("packets_sent", 0),
                    packets_recv=pm.get("packets_recv", 0),
                    latency_ms=pm.get("latency_ms", 0.0),
                    min_latency_ms=pm.get("min_latency_ms", 0.0),
                    max_latency_ms=pm.get("max_latency_ms", 0.0),
                    p95_latency_ms=pm.get("p95_latency_ms", 0.0),
                    jitter_ms=pm.get("jitter_ms", 0.0),
                    packet_loss_pct=pm.get("packet_loss_pct", 0.0),
                )

            qm = None
            if "queue" in q and q["queue"]:
                qd = q["queue"]
                from adaptive_qos.common.models import QueueMetrics
                qm = QueueMetrics(
                    timestamp=qd.get("timestamp", q["timestamp"]),
                    interface_name=qd.get("interface_name", "veth-wan-gw"),
                    capacity_mbps=qd.get("capacity_mbps", q["wan_capacity_mbps"]),
                    backlog_bytes=qd.get("backlog_bytes", 0),
                    backlog_packets=qd.get("backlog_packets", 0),
                    dropped_packets=qd.get("dropped_packets", 0),
                    overlimit_count=qd.get("overlimit_count", 0),
                )

            snap = TimeSliceSnapshot(
                timestamp=q["timestamp"],
                elapsed_sec=q["elapsed_sec"],
                wan_capacity_mbps=q["wan_capacity_mbps"],
                is_congested=q["is_congested"],
                flows=flows_dict,
                queue=qm,
            )
            unified_snapshots.append(snap)

        # Build summaries
        uncong_snaps = [s for s in unified_snapshots if not s.is_congested]
        cong_snaps = [s for s in unified_snapshots if s.is_congested]

        def compute_summary(snaps: List[TimeSliceSnapshot]) -> Dict[str, Any]:
            if not snaps:
                return {}
            all_fids = set()
            for s in snaps:
                all_fids.update(s.flows.keys())
            f_data = {}
            for fid in all_fids:
                lats = [s.flows[fid].latency_ms for s in snaps if fid in s.flows and s.flows[fid].latency_ms > 0]
                jits = [s.flows[fid].jitter_ms for s in snaps if fid in s.flows and s.flows[fid].jitter_ms > 0]
                losses = [s.flows[fid].packet_loss_pct for s in snaps if fid in s.flows]
                tputs = [s.flows[fid].throughput_mbps for s in snaps if fid in s.flows]
                f_data[fid] = {
                    "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else 0.0,
                    "max_latency_ms": round(max(lats), 2) if lats else 0.0,
                    "avg_jitter_ms": round(sum(jits) / len(jits), 2) if jits else 0.0,
                    "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else 0.0,
                    "avg_throughput_mbps": round(sum(tputs) / len(tputs), 2) if tputs else 0.0,
                }
            return {"snapshot_count": len(snaps), "flows": f_data}

        summary_by_flow = {f.flow_id: {"traffic_type": f.traffic_type.value} for f in self.config.flows}
        summary_by_flow["active_probe"] = {"traffic_type": "probe"}

        return ExperimentResult(
            experiment_id=self.config.experiment_id,
            start_time=start_time,
            end_time=end_time,
            config=self.config,
            snapshots=unified_snapshots,
            summary_by_flow=summary_by_flow,
            summary_uncongested=compute_summary(uncong_snaps),
            summary_congested=compute_summary(cong_snaps),
        )

    def _log_snapshot_summary(self, snapshot) -> None:
        flow_summaries = []
        for fid, fm in snapshot.flows.items():
            flow_summaries.append(f"{fid}: {fm.throughput_mbps:.1f}M / {fm.latency_ms:.0f}ms")
        status = "CONGESTED" if snapshot.is_congested else "NORMAL"
        logger.info(
            f"[{snapshot.elapsed_sec:4.1f}s | {status} ({snapshot.wan_capacity_mbps}M)] "
            f"Flows: {', '.join(flow_summaries)}"
        )
