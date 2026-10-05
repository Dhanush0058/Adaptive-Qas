#!/usr/bin/env python3
"""
CLI Entrypoint for running the Phase 1 Baseline Network QoS Experiment.
Supports running in Linux Network Namespaces (--mode linux) or Pure Simulation (--mode simulated).
"""

import argparse
import sys
import os

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

from adaptive_qos.common.logger import get_logger
from adaptive_qos.experiments.baseline import create_baseline_experiment_config
from adaptive_qos.experiments.runner import ExperimentRunner
from adaptive_qos.topology.linux_netns import LinuxNetnsTopology
from adaptive_qos.topology.simulation import SimulatedTopology

logger = get_logger("run_baseline")


def main():
    parser = argparse.ArgumentParser(
        description="Run Adaptive QoS Phase 1 Baseline Congestion Experiment"
    )
    parser.add_argument(
        "--mode",
        choices=["simulated", "linux"],
        default="simulated",
        help="Topology mode: 'simulated' (cross-platform, non-root) or 'linux' (Linux netns + tc, requires root)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=20.0,
        help="Total experiment duration in seconds (default: 20.0s)",
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
        "--dst-ip",
        type=str,
        default=None,
        help="Destination IP address (defaults to 10.0.2.2 for Linux mode, 127.0.0.1 for simulated)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results",
        help="Directory to save experiment output artifacts (default: results)",
    )

    args = parser.parse_args()

    dst_ip = args.dst_ip or ("10.0.2.2" if args.mode == "linux" else "127.0.0.1")

    config = create_baseline_experiment_config(
        dst_ip=dst_ip,
        total_duration_sec=args.duration,
        congestion_start_sec=args.congestion_start,
        congestion_end_sec=args.congestion_end,
    )

    if args.mode == "linux":
        if os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() != 0):
            logger.warning("Linux mode requires root privileges (sudo) on a Linux host.")
        topology = LinuxNetnsTopology(config.topology)
    else:
        topology = SimulatedTopology(config.topology)

    runner = ExperimentRunner(config=config, topology=topology, output_base_dir=args.output_dir)
    result = runner.run()

    print("\n" + "=" * 60)
    print("PHASE 1 BASELINE EXPERIMENT SUMMARY REPORT")
    print("=" * 60)
    print(f"Artifacts saved in: {runner.output_dir}\n")

    uncong = result.summary_uncongested.get("flows", {})
    cong = result.summary_congested.get("flows", {})

    print(f"{'Flow ID':<18} | {'Type':<16} | {'Uncongested Latency':<20} | {'Congested Latency':<20}")
    print("-" * 80)
    for fid, fmeta in result.summary_by_flow.items():
        u_lat = uncong.get(fid, {}).get("avg_latency_ms", 0.0)
        c_lat = cong.get(fid, {}).get("avg_latency_ms", 0.0)
        print(f"{fid:<18} | {fmeta['traffic_type']:<16} | {u_lat:>6.2f} ms             | {c_lat:>6.2f} ms")
    print("=" * 80)


if __name__ == "__main__":
    main()
