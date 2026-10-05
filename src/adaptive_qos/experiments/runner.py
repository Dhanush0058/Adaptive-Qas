"""Orchestrator for executing experiments."""

import time
import os
from typing import Optional
from adaptive_qos.common.models import ExperimentConfig, ExperimentResult
from adaptive_qos.common.logger import get_logger
from adaptive_qos.topology.base import BaseTopology
from adaptive_qos.traffic.runner import TrafficRunner
from adaptive_qos.monitoring.collector import MetricsCollector
from adaptive_qos.monitoring.storage import MetricsStorage

logger = get_logger("experiment.runner")


class ExperimentRunner:
    """
    Executes an end-to-end network experiment:
    1. Sets up the network topology (Linux Netns or Simulated)
    2. Initializes traffic receivers & monitoring collectors
    3. Spawns multi-flow traffic generators
    4. Triggers dynamic bandwidth throttling (100 Mbps -> 20 Mbps)
    5. Records live time-series metrics
    6. Stores structured results (JSON, CSV, Markdown)
    7. Cleans up all resources
    """

    def __init__(self, config: ExperimentConfig, topology: BaseTopology, output_base_dir: str = "results"):
        self.config = config
        self.topology = topology
        self.output_dir = os.path.join(output_base_dir, f"{config.experiment_id}_{int(time.time())}")
        self.storage = MetricsStorage(self.output_dir)
        self.traffic_runner = TrafficRunner(config.flows)
        self.collector = MetricsCollector(
            flows=config.flows,
            queue_monitor=self.topology.queue_monitor if hasattr(self.topology, "queue_monitor") else None,
            enable_active_probe=True,
            probe_target_ip=config.flows[0].dst_ip if config.flows else "127.0.0.1",
            probe_target_port=5099,
        )

    def run(self) -> ExperimentResult:
        logger.info(f"=== Starting Experiment: {self.config.name} ===")
        logger.info(f"Duration: {self.config.total_duration_sec}s | Congestion: {self.config.congestion_start_sec}s - {self.config.congestion_end_sec}s")

        self.topology.setup()
        start_time = time.time()
        end_time = start_time + self.config.total_duration_sec

        try:
            # 1. Start Monitoring
            self.collector.start()

            # 2. Start Traffic Flows
            self.traffic_runner.start_all()

            current_bandwidth = self.config.topology.initial_bandwidth_mbps
            is_congested = False
            last_sample_time = time.time()

            # 3. Main Experiment Loop
            while time.time() < end_time:
                now = time.time()
                elapsed = now - start_time

                # Check if we should enter congestion phase
                if (
                    self.config.congestion_start_sec <= elapsed < self.config.congestion_end_sec
                    and not is_congested
                ):
                    is_congested = True
                    current_bandwidth = self.config.topology.congested_bandwidth_mbps
                    logger.warning(f"--- [CONGESTION INJECTED] Throttling WAN link to {current_bandwidth} Mbps ---")
                    self.topology.set_wan_bandwidth(current_bandwidth)

                # Check if congestion phase has ended
                elif elapsed >= self.config.congestion_end_sec and is_congested:
                    is_congested = False
                    current_bandwidth = self.config.topology.initial_bandwidth_mbps
                    logger.info(f"--- [CONGESTION CLEARED] Restoring WAN link to {current_bandwidth} Mbps ---")
                    self.topology.set_wan_bandwidth(current_bandwidth)

                # Sample metrics every sampling_interval_sec
                if now - last_sample_time >= self.config.sampling_interval_sec:
                    snapshot = self.collector.collect_timeslice(
                        elapsed_sec=elapsed,
                        wan_capacity_mbps=current_bandwidth,
                        is_congested=is_congested,
                        window_sec=now - last_sample_time,
                    )
                    last_sample_time = now
                    self._log_snapshot_summary(snapshot)

                time.sleep(0.1)

        finally:
            # 4. Stop Traffic and Monitoring
            self.traffic_runner.stop_all()
            self.collector.stop()
            self.topology.teardown()

        # 5. Compile and Save Results
        result = self.collector.compile_result(self.config, start_time, time.time())
        saved_paths = self.storage.save_experiment_result(result)

        logger.info(f"=== Experiment Completed Successfully ===")
        logger.info(f"Results stored in: {self.output_dir}")
        return result

    def _log_snapshot_summary(self, snapshot) -> None:
        flow_summaries = []
        for fid, fm in snapshot.flows.items():
            flow_summaries.append(f"{fid}: {fm.throughput_mbps:.1f}M / {fm.latency_ms:.0f}ms")
        status = "CONGESTED" if snapshot.is_congested else "NORMAL"
        logger.info(
            f"[{snapshot.elapsed_sec:4.1f}s | {status} ({snapshot.wan_capacity_mbps}M)] "
            f"Flows: {', '.join(flow_summaries)}"
        )
