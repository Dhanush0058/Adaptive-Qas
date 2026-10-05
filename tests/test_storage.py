"""Unit tests for metrics storage (JSON, CSV, Markdown)."""

import os
import tempfile
import pytest
from adaptive_qos.common.models import (
    ExperimentConfig,
    ExperimentResult,
    TimeSliceSnapshot,
    FlowMetrics,
    TrafficType,
)
from adaptive_qos.monitoring.storage import MetricsStorage


def test_metrics_storage_writes():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = MetricsStorage(tmpdir)
        cfg = ExperimentConfig(
            experiment_id="exp_test",
            name="Test Exp",
            description="Test description",
        )
        fm = FlowMetrics(
            flow_id="f1",
            traffic_type=TrafficType.GAMING,
            packets_sent=10,
            packets_recv=10,
            throughput_mbps=1.5,
            latency_ms=10.0,
        )
        snap = TimeSliceSnapshot(
            timestamp=1000.0,
            elapsed_sec=1.0,
            wan_capacity_mbps=100.0,
            is_congested=False,
            flows={"f1": fm},
        )
        result = ExperimentResult(
            experiment_id="exp_test",
            start_time=1000.0,
            end_time=1010.0,
            config=cfg,
            snapshots=[snap],
            summary_by_flow={"f1": {"traffic_type": "gaming"}},
            summary_uncongested={"flows": {"f1": {"avg_latency_ms": 10.0}}},
            summary_congested={},
        )

        saved = storage.save_experiment_result(result)
        assert os.path.exists(saved["json_result"])
        assert os.path.exists(saved["csv_timeseries"])
        assert os.path.exists(saved["markdown_summary"])

        with open(saved["csv_timeseries"], "r", encoding="utf-8") as f:
            content = f.read()
            assert "flow_id" in content
            assert "f1" in content
            assert "gaming" in content
