"""Structured storage for metrics time-series, summaries, and experiment results."""

import csv
import json
import os
from typing import List, Dict, Any
from adaptive_qos.common.models import ExperimentResult, TimeSliceSnapshot
from adaptive_qos.common.logger import get_logger

logger = get_logger("monitoring.storage")


class MetricsStorage:
    """Saves experiment results and live time-series into structured JSON, CSV, and Markdown."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def save_experiment_result(self, result: ExperimentResult) -> Dict[str, str]:
        """Save all artifacts for an experiment and return generated filepaths."""
        saved_paths = {}

        # 1. Full JSON result (metadata + config + snapshots + summaries)
        json_path = os.path.join(self.output_dir, "experiment_result.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2)
        saved_paths["json_result"] = json_path

        # 2. Flattened CSV time-series for easy analytics & dashboard ingestion
        csv_path = os.path.join(self.output_dir, "metrics_timeseries.csv")
        self._write_csv_timeseries(result.snapshots, csv_path)
        saved_paths["csv_timeseries"] = csv_path

        # 3. Formatted markdown summary report
        md_path = os.path.join(self.output_dir, "experiment_summary.md")
        self._write_markdown_summary(result, md_path)
        saved_paths["markdown_summary"] = md_path

        logger.info(f"Experiment results saved to {self.output_dir}")
        return saved_paths

    def _write_csv_timeseries(self, snapshots: List[TimeSliceSnapshot], csv_path: str) -> None:
        if not snapshots:
            return

        fieldnames = [
            "timestamp",
            "elapsed_sec",
            "wan_capacity_mbps",
            "is_congested",
            "flow_id",
            "traffic_type",
            "throughput_mbps",
            "latency_ms",
            "min_latency_ms",
            "max_latency_ms",
            "p95_latency_ms",
            "jitter_ms",
            "packet_loss_pct",
            "packets_sent",
            "packets_recv",
            "queue_backlog_bytes",
            "queue_backlog_packets",
            "queue_dropped_packets",
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for s in snapshots:
                q_bytes = s.queue.backlog_bytes if s.queue else 0
                q_pkts = s.queue.backlog_packets if s.queue else 0
                q_drops = s.queue.dropped_packets if s.queue else 0

                for flow_id, m in s.flows.items():
                    writer.writerow({
                        "timestamp": f"{s.timestamp:.3f}",
                        "elapsed_sec": f"{s.elapsed_sec:.1f}",
                        "wan_capacity_mbps": s.wan_capacity_mbps,
                        "is_congested": s.is_congested,
                        "flow_id": flow_id,
                        "traffic_type": m.traffic_type.value,
                        "throughput_mbps": m.throughput_mbps,
                        "latency_ms": m.latency_ms,
                        "min_latency_ms": m.min_latency_ms,
                        "max_latency_ms": m.max_latency_ms,
                        "p95_latency_ms": m.p95_latency_ms,
                        "jitter_ms": m.jitter_ms,
                        "packet_loss_pct": m.packet_loss_pct,
                        "packets_sent": m.packets_sent,
                        "packets_recv": m.packets_recv,
                        "queue_backlog_bytes": q_bytes,
                        "queue_backlog_packets": q_pkts,
                        "queue_dropped_packets": q_drops,
                    })

    def _write_markdown_summary(self, result: ExperimentResult, md_path: str) -> None:
        cfg = result.config
        uncong = result.summary_uncongested
        cong = result.summary_congested

        md = []
        md.append(f"# Adaptive QoS - Baseline Experiment Report")
        md.append(f"**Experiment ID**: `{result.experiment_id}`  ")
        md.append(f"**Description**: {cfg.description}  ")
        md.append(f"**Total Duration**: {cfg.total_duration_sec:.1f}s  ")
        md.append(f"**Congestion Window**: {cfg.congestion_start_sec:.1f}s - {cfg.congestion_end_sec:.1f}s  ")
        md.append("")
        md.append("## 1. Capacity Comparison Summary")
        md.append("")
        md.append("| Metric | Uncongested (100 Mbps) | Congested (20 Mbps) | Impact / Degradation |")
        md.append("| :--- | :--- | :--- | :--- |")

        for flow_id in result.summary_by_flow:
            u_flow = uncong.get("flows", {}).get(flow_id, {})
            c_flow = cong.get("flows", {}).get(flow_id, {})

            u_lat = u_flow.get("avg_latency_ms", 0.0)
            c_lat = c_flow.get("avg_latency_ms", 0.0)
            lat_diff = f"+{c_lat - u_lat:.1f} ms" if c_lat >= u_lat else f"{c_lat - u_lat:.1f} ms"

            u_jit = u_flow.get("avg_jitter_ms", 0.0)
            c_jit = c_flow.get("avg_jitter_ms", 0.0)
            jit_diff = f"+{c_jit - u_jit:.1f} ms" if c_jit >= u_jit else f"{c_jit - u_jit:.1f} ms"

            u_loss = u_flow.get("avg_loss_pct", 0.0)
            c_loss = c_flow.get("avg_loss_pct", 0.0)

            u_tput = u_flow.get("avg_throughput_mbps", 0.0)
            c_tput = c_flow.get("avg_throughput_mbps", 0.0)

            md.append(f"| **`{flow_id}` Latency (ms)** | {u_lat:.2f} ms | {c_lat:.2f} ms | **{lat_diff}** |")
            md.append(f"| **`{flow_id}` Jitter (ms)** | {u_jit:.2f} ms | {c_jit:.2f} ms | **{jit_diff}** |")
            md.append(f"| **`{flow_id}` Loss (%)** | {u_loss:.2f}% | {c_loss:.2f}% | +{c_loss - u_loss:.2f}% |")
            md.append(f"| **`{flow_id}` Throughput** | {u_tput:.2f} Mbps | {c_tput:.2f} Mbps | {c_tput - u_tput:.2f} Mbps |")

        md.append("")
        md.append("## 2. Key Findings & Baseline Problem Analysis")
        md.append("- **Bufferbloat / Queue Buildup**: When WAN bandwidth dropped from 100 Mbps to 20 Mbps, bulk download and streaming saturated the FIFO queue.")
        md.append("- **Interactive Degradation**: Low-latency traffic (Gaming & Video Call) experienced substantial latency spike, high jitter, and packet loss due to lack of QoS prioritization.")
        md.append("- **Phase 2 Target**: Phase 2 QoS engine will dynamically identify interactive flows and prioritize them to prevent this degradation.")
        md.append("")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md))
