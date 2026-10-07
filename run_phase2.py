#!/usr/bin/env python3
"""
CLI Entrypoint for Phase 2: Real Traffic Classification & Link-Capacity Estimation.
Supports running live experiments (Linux Netns or Simulation) or processing existing Phase 1 data.
"""

import argparse
import json
import os
import sys

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

from adaptive_qos.common.logger import get_logger
from adaptive_qos.common.models import (
    ExperimentResult,
    TimeSliceSnapshot,
    FlowMetrics,
    QueueMetrics,
    TrafficType,
)
from adaptive_qos.classification.models import DataSource
from adaptive_qos.experiments.baseline import create_baseline_experiment_config
from adaptive_qos.experiments.runner import ExperimentRunner
from adaptive_qos.experiments.phase2_pipeline import Phase2Pipeline
from adaptive_qos.topology.linux_netns import LinuxNetnsTopology
from adaptive_qos.topology.simulation import SimulatedTopology

logger = get_logger("run_phase2")


def load_experiment_from_json(json_path: str) -> ExperimentResult:
    """Load an existing Phase 1 JSON experiment result file."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    snapshots = []
    for s in data.get("snapshots", []):
        flows = {}
        for fid, fm in s.get("flows", {}).items():
            flows[fid] = FlowMetrics(
                flow_id=fm["flow_id"],
                traffic_type=TrafficType(fm["traffic_type"]),
                packets_sent=fm.get("packets_sent", 0),
                packets_recv=fm.get("packets_recv", 0),
                bytes_recv=fm.get("bytes_recv", 0),
                throughput_mbps=fm.get("throughput_mbps", 0.0),
                latency_ms=fm.get("latency_ms", 0.0),
                min_latency_ms=fm.get("min_latency_ms", 0.0),
                max_latency_ms=fm.get("max_latency_ms", 0.0),
                p95_latency_ms=fm.get("p95_latency_ms", 0.0),
                jitter_ms=fm.get("jitter_ms", 0.0),
                packet_loss_pct=fm.get("packet_loss_pct", 0.0),
            )
        q = None
        if s.get("queue"):
            qd = s["queue"]
            q = QueueMetrics(
                timestamp=qd.get("timestamp", s["timestamp"]),
                interface_name=qd.get("interface_name", "veth-wan-gw"),
                capacity_mbps=qd.get("capacity_mbps", s.get("wan_capacity_mbps", 100.0)),
                backlog_bytes=qd.get("backlog_bytes", 0),
                backlog_packets=qd.get("backlog_packets", 0),
                dropped_packets=qd.get("dropped_packets", 0),
                overlimit_count=qd.get("overlimit_count", 0),
            )

        snapshots.append(
            TimeSliceSnapshot(
                timestamp=s["timestamp"],
                elapsed_sec=s["elapsed_sec"],
                wan_capacity_mbps=s.get("wan_capacity_mbps", 100.0),
                is_congested=s.get("is_congested", False),
                flows=flows,
                queue=q,
            )
        )

    # Reconstruct minimal ExperimentConfig
    cfg = create_baseline_experiment_config(total_duration_sec=data.get("end_time", 0) - data.get("start_time", 0))

    return ExperimentResult(
        experiment_id=data.get("experiment_id", "loaded_exp"),
        start_time=data.get("start_time", 0.0),
        end_time=data.get("end_time", 0.0),
        config=cfg,
        snapshots=snapshots,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run Adaptive QoS Phase 2: Traffic Classification & Link-Capacity Estimation"
    )
    parser.add_argument(
        "--mode",
        choices=["simulated", "linux"],
        default="simulated",
        help="Topology execution mode: 'simulated' (cross-platform) or 'linux' (Linux netns + tc)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=20.0,
        help="Experiment duration in seconds (default: 20.0s)",
    )
    parser.add_argument(
        "--congestion-start",
        type=float,
        default=6.0,
        help="Second at which WAN capacity throttles to 20 Mbps (default: 6.0s)",
    )
    parser.add_argument(
        "--congestion-end",
        type=float,
        default=16.0,
        help="Second at which WAN capacity recovers (default: 16.0s)",
    )
    parser.add_argument(
        "--input-json",
        type=str,
        default=None,
        help="Optional: Path to existing Phase 1 experiment_result.json to classify directly",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results/phase2",
        help="Directory to save Phase 2 output artifacts (default: results/phase2)",
    )

    args = parser.parse_args()

    data_source = DataSource.LINUX_REAL if args.mode == "linux" else DataSource.SIMULATION

    if args.input_json and os.path.exists(args.input_json):
        logger.info(f"Loading existing experiment data from {args.input_json}...")
        exp_result = load_experiment_from_json(args.input_json)
        # If the input was from a Linux netns run, preserve the tag
        if "linux" in args.input_json.lower() or args.mode == "linux":
            data_source = DataSource.LINUX_REAL
    else:
        logger.info(f"Running live {args.mode.upper()} experiment (duration {args.duration}s)...")
        dst_ip = "10.0.2.2" if args.mode == "linux" else "127.0.0.1"
        config = create_baseline_experiment_config(
            dst_ip=dst_ip,
            total_duration_sec=args.duration,
            congestion_start_sec=args.congestion_start,
            congestion_end_sec=args.congestion_end,
        )

        if args.mode == "linux":
            if os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() != 0):
                logger.warning("Linux mode requires root privileges on a Linux host.")
            topology = LinuxNetnsTopology(config.topology)
        else:
            topology = SimulatedTopology(config.topology)

        runner = ExperimentRunner(config=config, topology=topology, output_base_dir="results")
        exp_result = runner.run()

    # Run Phase 2 Pipeline
    pipeline = Phase2Pipeline(output_dir=args.output_dir, data_source=data_source)
    phase2_artifacts = pipeline.process_experiment(exp_result)

    # Print Formatted Console Output
    print("\n" + "=" * 90)
    print("PHASE 2: TRAFFIC CLASSIFICATION & LINK CAPACITY ESTIMATION REPORT")
    print(f"Data Source Tag: [{data_source.value.upper()}] | Artifacts: {args.output_dir}")
    print("=" * 90)

    print("\n[A] FLOW CLASSIFICATION RESULTS:")
    print(f"{'Flow ID':<18} | {'Expected Type':<16} | {'Predicted Type':<18} | {'Confidence':<10} | {'Data Source':<12}")
    print("-" * 90)
    flow_summary = phase2_artifacts.get("flow_summary", {})
    for fid, smry in flow_summary.items():
        is_match = "[OK]" if smry["expected_type"] == smry["predicted_type"] else "[X]"
        print(
            f"{fid:<18} | {smry['expected_type']:<16} | {smry['predicted_type']:<16} {is_match:<4} | "
            f"{smry['avg_confidence']:>6.2f}     | {smry['data_source']:<12}"
        )

    print("\n[B] LINK-CAPACITY ESTIMATION RESULTS:")
    print(f"{'Phase':<16} | {'Configured Capacity':<22} | {'Estimated Capacity':<20} | {'Data Source':<12}")
    print("-" * 90)

    with open(phase2_artifacts["capacity_estimation_json"], "r", encoding="utf-8") as f:
        cap_data = json.load(f)

    caps = cap_data.get("capacity_timeline", [])
    uncong_caps = [c for c in caps if (c.get("configured_capacity_mbps") or 100.0) >= 50.0]
    cong_caps = [c for c in caps if (c.get("configured_capacity_mbps") or 100.0) < 50.0]

    if uncong_caps:
        u_est = sum(c["estimated_capacity_mbps"] for c in uncong_caps) / len(uncong_caps)
        u_conf = sum(c["confidence"] for c in uncong_caps) / len(uncong_caps)
        print(f"{'Uncongested':<16} | {'100.0 Mbps':<22} | {u_est:>5.1f} Mbps (conf {u_conf:.2f})  | {data_source.value:<12}")

    if cong_caps:
        c_est = sum(c["estimated_capacity_mbps"] for c in cong_caps) / len(cong_caps)
        c_conf = sum(c["confidence"] for c in cong_caps) / len(cong_caps)
        print(f"{'Congested':<16} | {'20.0 Mbps':<22} | {c_est:>5.1f} Mbps (conf {c_conf:.2f})  | {data_source.value:<12}")

    print("=" * 90 + "\n")


if __name__ == "__main__":
    main()
