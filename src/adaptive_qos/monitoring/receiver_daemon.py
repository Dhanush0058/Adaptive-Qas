"""Receiver daemon intended to run inside the wan_server network namespace."""

import argparse
import json
import os
import sys
import time

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from adaptive_qos.common.models import FlowConfig, TrafficType, TransportProtocol
from adaptive_qos.common.logger import get_logger
from adaptive_qos.monitoring.passive import PassiveTrafficMonitor
from adaptive_qos.monitoring.probes import ProbeEchoResponder

logger = get_logger("receiver.daemon")


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
    parser = argparse.ArgumentParser(description="Receiver daemon for WAN server namespace")
    parser.add_argument("--config", required=True, help="Path to experiment config JSON")
    parser.add_argument("--output", required=True, help="Path to write receiver snapshots JSON")
    parser.add_argument("--duration", type=float, default=25.0, help="Runtime duration in seconds")
    parser.add_argument("--bind-ip", default="0.0.0.0", help="IP address to bind listener sockets")
    parser.add_argument("--probe-port", type=int, default=5099, help="UDP port for active probe echo responder")
    args = parser.parse_args()

    flows, full_config = parse_flows(args.config)

    logger.info(f"Starting receiver daemon on {args.bind_ip} for {len(flows)} flows...")
    monitor = PassiveTrafficMonitor(flows, bind_ip=args.bind_ip)
    echo_responder = ProbeEchoResponder(bind_ip=args.bind_ip, bind_port=args.probe_port)

    monitor.start()
    echo_responder.start()

    snapshots = []
    start_time = time.time()
    last_sample = start_time

    try:
        while time.time() - start_time < args.duration:
            time.sleep(0.5)
            now = time.time()
            if now - last_sample >= 1.0:
                snap = monitor.collect_snapshot(window_sec=now - last_sample)
                snapshots.append({
                    "timestamp": now,
                    "elapsed_sec": round(now - start_time, 2),
                    "flows": {k: v.to_dict() for k, v in snap.items()},
                })
                last_sample = now
                # Log progress
                flow_summary = ", ".join(f"{k}: {v.throughput_mbps:.1f}M / {v.latency_ms:.1f}ms" for k, v in snap.items())
                logger.info(f"[RX {now - start_time:4.1f}s] {flow_summary}")

    finally:
        monitor.stop()
        echo_responder.stop()

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(snapshots, f, indent=2)
        logger.info(f"Receiver daemon finished. Saved {len(snapshots)} snapshots to {args.output}")


if __name__ == "__main__":
    main()
