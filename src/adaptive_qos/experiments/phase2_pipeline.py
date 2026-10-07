"""Phase 2 Pipeline: Enriches Phase 1 network measurements with Classification & Capacity Estimation."""

import csv
import json
import os
import time
from typing import Dict, List, Any, Optional

from adaptive_qos.common.models import ExperimentResult, TimeSliceSnapshot, FlowConfig, TrafficType
from adaptive_qos.classification.models import (
    ClassificationCategory,
    FlowClassificationResult,
    LinkCapacityEstimate,
    DataSource,
)
from adaptive_qos.classification.classifier import TrafficClassifier
from adaptive_qos.estimation.estimator import LinkCapacityEstimator
from adaptive_qos.common.logger import get_logger

logger = get_logger("phase2.pipeline")


class Phase2Pipeline:
    """
    Consumes Phase 1 measurement snapshots and generates:
    1. Observable Flow Feature Extractions
    2. Dynamic Traffic Classifications with Confidence Scores
    3. Link-Capacity Estimations with Congestion/Transition Tracking
    4. Structured Output Artifacts in `results/phase2/`
    """

    def __init__(self, output_dir: str = "results/phase2", data_source: DataSource = DataSource.SIMULATION):
        self.output_dir = output_dir
        self.data_source = data_source
        self.classifier = TrafficClassifier(data_source=data_source)
        self.capacity_estimator = LinkCapacityEstimator(data_source=data_source)
        os.makedirs(self.output_dir, exist_ok=True)

    def process_experiment(
        self,
        experiment_result: ExperimentResult,
        flow_configs: Optional[List[FlowConfig]] = None,
    ) -> Dict[str, Any]:
        """Process an entire experiment result and generate Phase 2 structured reports."""
        logger.info(f"Processing experiment `{experiment_result.experiment_id}` for Phase 2 Classification & Estimation...")

        config_map: Dict[str, FlowConfig] = {}
        if flow_configs:
            config_map = {f.flow_id: f for f in flow_configs}
        elif hasattr(experiment_result, "config") and experiment_result.config and hasattr(experiment_result.config, "flows"):
            config_map = {f.flow_id: f for f in experiment_result.config.flows}

        all_classifications: List[Dict[str, Any]] = []
        all_capacity_estimates: List[Dict[str, Any]] = []
        timeseries_rows: List[Dict[str, Any]] = []

        flow_summary_accum: Dict[str, Dict[str, Any]] = {}

        for snapshot in experiment_result.snapshots:
            # 1. Update Link Capacity Estimate
            cap_est = self.capacity_estimator.update(snapshot)
            all_capacity_estimates.append(cap_est.to_dict())

            # 2. Classify each flow in this timeslice
            for flow_id, metric in snapshot.flows.items():
                # Skip probe from application classification (or classify as interactive)
                if metric.traffic_type == TrafficType.PROBE:
                    continue

                f_cfg = config_map.get(flow_id)
                res = self.classifier.classify_metric(
                    metric=metric,
                    flow_config=f_cfg,
                    elapsed_sec=snapshot.elapsed_sec,
                )
                res_dict = res.to_dict()
                all_classifications.append({
                    "elapsed_sec": snapshot.elapsed_sec,
                    "wan_capacity_mbps": snapshot.wan_capacity_mbps,
                    "is_congested": snapshot.is_congested,
                    **res_dict,
                })

                # Accumulate for overall flow summary
                if flow_id not in flow_summary_accum:
                    expected_type = f_cfg.traffic_type.value if f_cfg else metric.traffic_type.value
                    flow_summary_accum[flow_id] = {
                        "flow_id": flow_id,
                        "expected_type": expected_type,
                        "predictions": [],
                        "confidences": [],
                        "tputs": [],
                        "sizes": [],
                        "packet_rates": [],
                    }

                flow_summary_accum[flow_id]["predictions"].append(res.classification.value)
                flow_summary_accum[flow_id]["confidences"].append(res.confidence)
                flow_summary_accum[flow_id]["tputs"].append(metric.throughput_mbps)
                flow_summary_accum[flow_id]["sizes"].append(res.features.avg_packet_size_bytes)
                flow_summary_accum[flow_id]["packet_rates"].append(res.features.packet_rate_pps)

                # Flattened timeseries row
                timeseries_rows.append({
                    "timestamp": f"{snapshot.timestamp:.3f}",
                    "elapsed_sec": f"{snapshot.elapsed_sec:.1f}",
                    "flow_id": flow_id,
                    "expected_type": f_cfg.traffic_type.value if f_cfg else metric.traffic_type.value,
                    "predicted_type": res.classification.value,
                    "confidence": f"{res.confidence:.3f}",
                    "throughput_mbps": f"{metric.throughput_mbps:.3f}",
                    "avg_packet_size": f"{res.features.avg_packet_size_bytes:.1f}",
                    "packet_rate_pps": f"{res.features.packet_rate_pps:.1f}",
                    "burstiness_ratio": f"{res.features.burstiness_ratio:.2f}",
                    "latency_ms": f"{metric.latency_ms:.2f}",
                    "jitter_ms": f"{metric.jitter_ms:.2f}",
                    "configured_capacity_mbps": f"{snapshot.wan_capacity_mbps:.1f}",
                    "estimated_capacity_mbps": f"{cap_est.estimated_capacity_mbps:.2f}",
                    "capacity_confidence": f"{cap_est.confidence:.3f}",
                    "link_state": cap_est.link_state.value,
                    "data_source": self.data_source.value,
                })

        # Generate summary table metrics
        final_flow_summary = {}
        for flow_id, d in flow_summary_accum.items():
            preds = d["predictions"]
            confs = d["confidences"]
            # Mode prediction
            pred_counts = {p: preds.count(p) for p in set(preds)}
            top_pred = max(pred_counts.items(), key=lambda x: x[1])[0]
            avg_conf = sum(confs) / len(confs) if confs else 0.0

            final_flow_summary[flow_id] = {
                "flow_id": flow_id,
                "expected_type": d["expected_type"],
                "predicted_type": top_pred,
                "avg_confidence": round(avg_conf, 3),
                "prediction_consistency": f"{(pred_counts[top_pred] / len(preds)) * 100.0:.1f}%",
                "avg_throughput_mbps": round(sum(d["tputs"]) / len(d["tputs"]), 2) if d["tputs"] else 0.0,
                "avg_packet_size_bytes": round(sum(d["sizes"]) / len(d["sizes"]), 1) if d["sizes"] else 0.0,
                "avg_packet_rate_pps": round(sum(d["packet_rates"]) / len(d["packet_rates"]), 1) if d["packet_rates"] else 0.0,
                "data_source": self.data_source.value,
            }

        # 3. Write Output Files
        json_class_path = os.path.join(self.output_dir, "classification_results.json")
        with open(json_class_path, "w", encoding="utf-8") as f:
            json.dump({
                "experiment_id": experiment_result.experiment_id,
                "data_source": self.data_source.value,
                "flow_summary": final_flow_summary,
                "timeseries_classifications": all_classifications,
            }, f, indent=2)

        json_cap_path = os.path.join(self.output_dir, "capacity_estimation.json")
        with open(json_cap_path, "w", encoding="utf-8") as f:
            json.dump({
                "experiment_id": experiment_result.experiment_id,
                "data_source": self.data_source.value,
                "capacity_timeline": all_capacity_estimates,
            }, f, indent=2)

        csv_path = os.path.join(self.output_dir, "classification_timeseries.csv")
        if timeseries_rows:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(timeseries_rows[0].keys()))
                writer.writeheader()
                writer.writerows(timeseries_rows)

        md_path = os.path.join(self.output_dir, "phase2_summary.md")
        self._write_phase2_summary_markdown(
            md_path=md_path,
            experiment_id=experiment_result.experiment_id,
            flow_summary=final_flow_summary,
            capacity_estimates=all_capacity_estimates,
        )

        logger.info(f"Phase 2 processing complete. Artifacts saved to {self.output_dir}")
        return {
            "classification_results_json": json_class_path,
            "capacity_estimation_json": json_cap_path,
            "classification_timeseries_csv": csv_path,
            "phase2_summary_md": md_path,
            "flow_summary": final_flow_summary,
        }

    def _write_phase2_summary_markdown(
        self,
        md_path: str,
        experiment_id: str,
        flow_summary: Dict[str, Dict[str, Any]],
        capacity_estimates: List[Dict[str, Any]],
    ) -> None:
        md = []
        md.append(f"# Phase 2 Summary: Traffic Classification & Link Capacity Estimation")
        md.append(f"**Experiment ID**: `{experiment_id}`  ")
        md.append(f"**Data Source**: `{self.data_source.value}`  ")
        md.append(f"**Generated**: {time.strftime('%Y-%m-%d %H:%M:%S')}  ")
        md.append("")
        md.append("## 1. Flow Classification Summary")
        md.append("")
        md.append("| Flow ID | Expected Type | Predicted Type | Confidence | Consistency | Avg Rate | Avg Size | Data Source |")
        md.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

        for fid, s in flow_summary.items():
            match_icon = "[OK]" if s["expected_type"] == s["predicted_type"] else "[X]"
            md.append(
                f"| `{fid}` | `{s['expected_type']}` | **`{s['predicted_type']}`** {match_icon} | "
                f"{s['avg_confidence']:.2f} | {s['prediction_consistency']} | "
                f"{s['avg_throughput_mbps']} Mbps | {s['avg_packet_size_bytes']} B | `{s['data_source']}` |"
            )

        md.append("")
        md.append("## 2. Link Capacity Estimation Summary")
        md.append("")
        md.append("| Phase | Configured Capacity | Estimated Capacity | Avg Confidence | Link State | Data Source |")
        md.append("| :--- | :--- | :--- | :--- | :--- | :--- |")

        # Split capacity estimates into uncongested (100M) and congested (20M)
        uncong = [c for c in capacity_estimates if (c.get("configured_capacity_mbps") or 100.0) >= 50.0]
        cong = [c for c in capacity_estimates if (c.get("configured_capacity_mbps") or 100.0) < 50.0]

        if uncong:
            avg_est = sum(c["estimated_capacity_mbps"] for c in uncong) / len(uncong)
            avg_conf = sum(c["confidence"] for c in uncong) / len(uncong)
            state = uncong[-1]["link_state"]
            md.append(f"| **Uncongested** | 100.0 Mbps | **{avg_est:.1f} Mbps** | {avg_conf:.2f} | `{state}` | `{self.data_source.value}` |")

        if cong:
            avg_est = sum(c["estimated_capacity_mbps"] for c in cong) / len(cong)
            avg_conf = sum(c["confidence"] for c in cong) / len(cong)
            state = cong[-1]["link_state"]
            md.append(f"| **Congested** | 20.0 Mbps | **{avg_est:.1f} Mbps** | {avg_conf:.2f} | `{state}` | `{self.data_source.value}` |")

        md.append("")
        md.append("## 3. Detected Capacity Step Transitions")
        transitions = [c for c in capacity_estimates if c.get("detected_change")]
        if transitions:
            for t in transitions:
                md.append(f"- **t = {t.get('timestamp', 0):.1f}s**: {t.get('change_description')} (Confidence: {t.get('confidence', 0):.2f})")
        else:
            md.append("- *No discrete transitions detected in this measurement interval.*")

        md.append("")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md))
