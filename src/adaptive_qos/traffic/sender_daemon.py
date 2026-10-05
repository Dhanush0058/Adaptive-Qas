"""Sender daemon intended to run inside the lan_client network namespace."""

import argparse
import json
import os
import sys
import time

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from adaptive_qos.common.models import FlowConfig, TrafficType, TransportProtocol
from adaptive_qos.common.logger import get_logger
from adaptive_qos.traffic.runner import TrafficRunner
from adaptive_qos.monitoring.probes import ActiveProbe

logger = get_logger("sender.daemon")


def parse_flows(config_path: str):
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    flows = []
    for fc in data.get("flows", []):
        flows.append(
            FlowConfig(
                flow_id=fc["flow_id"],
                traffic_type=TrafficType(fc["traffic_type"]),
                protocol=TransportProtocol(fc.get("protocol", "udp")),
                src_ip=fc.get("src_ip", "10.0.1.2"),
                dst_ip=fc.get("dst_ip", "10.0.2.2"),
                src_port=fc.get("src_port", 4000),
                dst_port=fc.get("dst_port", 5000),
                target_rate_mbps=fc.get("target_rate_mbps", 0.0),
                packet_size_bytes=fc.get("packet_size_bytes", 1400),
                duration_sec=fc.get("duration_sec", 30.0),
                extra_params=fc.get("extra_params", {}),
            )
        )
    return flows, data


def main():
    parser = argparse.ArgumentParser(description="Sender daemon for LAN client namespace")
    parser.add_argument("--config", required=True, help="Path to experiment config JSON")
    parser.add_argument("--output", required=True, help="Path to write probe snapshots JSON")
    parser.add_argument("--duration", type=float, default=25.0, help="Runtime duration in seconds")
    parser.add_argument("--probe-target", default="10.0.2.2", help="Target IP for active probe")
    parser.add_argument("--probe-port", type=int, default=5099, help="Target UDP port for active probe")
    args = parser.parse_args()

    flows, full_config = parse_flows(args.config)

    logger.info(f"Starting {len(flows)} traffic flows from LAN client...")
    runner = TrafficRunner(flows)
    active_probe = ActiveProbe(args.probe_target, args.probe_port, probe_interval_ms=50.0)

    runner.start_all()
    active_probe.start()

    probe_snapshots = []
    start_time = time.time()
    last_sample = start_time

    try:
        while time.time() - start_time < args.duration:
            time.sleep(0.5)
            now = time.time()
            if now - last_sample >= 1.0:
                p_metric = active_probe.collect_and_reset_window()
                probe_snapshots.append({
                    "timestamp": now,
                    "elapsed_sec": round(now - start_time, 2),
                    "probe": p_metric.to_dict(),
                })
                last_sample = now
                logger.info(f"[TX PROBE {now - start_time:4.1f}s] RTT: {p_metric.latency_ms:.1f}ms | Jitter: {p_metric.jitter_ms:.1f}ms | Loss: {p_metric.packet_loss_pct:.1f}%")

    finally:
        runner.stop_all()
        active_probe.stop()

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(probe_snapshots, f, indent=2)
        logger.info(f"Sender daemon finished. Saved {len(probe_snapshots)} probe snapshots to {args.output}")


if __name__ == "__main__":
    main()
