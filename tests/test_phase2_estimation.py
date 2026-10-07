"""Unit tests for Phase 2 Link Capacity Estimation and Pipeline Integration."""

import os
import tempfile
import pytest
from adaptive_qos.common.models import (
    TimeSliceSnapshot,
    FlowMetrics,
    QueueMetrics,
    TrafficType,
    ExperimentResult,
    ExperimentConfig,
)
from adaptive_qos.classification.models import DataSource, LinkState
from adaptive_qos.estimation.estimator import LinkCapacityEstimator
from adaptive_qos.experiments.phase2_pipeline import Phase2Pipeline


def test_capacity_estimation_saturation():
    estimator = LinkCapacityEstimator(
        window_size_sec=3.0,
        base_rtt_ms=15.0,
        data_source=DataSource.LINUX_REAL,
    )

    # Simulate 3 seconds of saturated 20 Mbps link with queue backlog
    for sec in range(1, 4):
        snap = TimeSliceSnapshot(
            timestamp=1000.0 + sec,
            elapsed_sec=float(sec),
            wan_capacity_mbps=20.0,
            is_congested=True,
            flows={
                "bulk": FlowMetrics(flow_id="bulk", traffic_type=TrafficType.BULK_DOWNLOAD, throughput_mbps=18.5, latency_ms=55.0)
            },
            queue=QueueMetrics(timestamp=1000.0 + sec, interface_name="veth-wan-gw", capacity_mbps=20.0, backlog_bytes=45000, backlog_packets=30, dropped_packets=5),
        )
        est = estimator.update(snap)

    # Estimated capacity should track near ~18-20 Mbps with high confidence and real linux tag
    assert est.estimated_capacity_mbps < 35.0
    assert est.confidence >= 0.80
    assert est.data_source == DataSource.LINUX_REAL
    assert est.link_state in [LinkState.CONGESTED_SATURATED, LinkState.CONGESTED_DROPPING]


def test_capacity_change_detection():
    estimator = LinkCapacityEstimator(
        window_size_sec=2.0,
        data_source=DataSource.LINUX_REAL,
    )

    # Initial 100 Mbps state
    for sec in range(1, 4):
        snap = TimeSliceSnapshot(
            timestamp=1000.0 + sec,
            elapsed_sec=float(sec),
            wan_capacity_mbps=100.0,
            is_congested=False,
            flows={
                "bulk": FlowMetrics(flow_id="bulk", traffic_type=TrafficType.BULK_DOWNLOAD, throughput_mbps=85.0, latency_ms=16.0)
            },
            queue=QueueMetrics(timestamp=1000.0 + sec, interface_name="veth-wan-gw", capacity_mbps=100.0, backlog_bytes=0),
        )
        estimator.update(snap)

    # Step down to 20 Mbps throttled state with queue pressure
    step_down_detected = False
    for sec in range(4, 8):
        snap = TimeSliceSnapshot(
            timestamp=1000.0 + sec,
            elapsed_sec=float(sec),
            wan_capacity_mbps=20.0,
            is_congested=True,
            flows={
                "bulk": FlowMetrics(flow_id="bulk", traffic_type=TrafficType.BULK_DOWNLOAD, throughput_mbps=18.0, latency_ms=60.0)
            },
            queue=QueueMetrics(timestamp=1000.0 + sec, interface_name="veth-wan-gw", capacity_mbps=20.0, backlog_bytes=50000, dropped_packets=12),
        )
        est = estimator.update(snap)
        if est.detected_change:
            step_down_detected = True

    assert step_down_detected is True
    assert est.estimated_capacity_mbps < 30.0


def test_phase2_pipeline_end_to_end():
    with tempfile.TemporaryDirectory() as tmpdir:
        pipeline = Phase2Pipeline(output_dir=tmpdir, data_source=DataSource.SIMULATION)

        # Build mock experiment result
        snaps = [
            TimeSliceSnapshot(
                timestamp=100.0,
                elapsed_sec=1.0,
                wan_capacity_mbps=100.0,
                is_congested=False,
                flows={
                    "flow_gaming": FlowMetrics(flow_id="flow_gaming", traffic_type=TrafficType.GAMING, packets_recv=60, bytes_recv=5760, throughput_mbps=0.046),
                    "flow_bulk": FlowMetrics(flow_id="flow_bulk", traffic_type=TrafficType.BULK_DOWNLOAD, packets_recv=1500, bytes_recv=2100000, throughput_mbps=16.8),
                },
            ),
            TimeSliceSnapshot(
                timestamp=101.0,
                elapsed_sec=2.0,
                wan_capacity_mbps=20.0,
                is_congested=True,
                flows={
                    "flow_gaming": FlowMetrics(flow_id="flow_gaming", traffic_type=TrafficType.GAMING, packets_recv=60, bytes_recv=5760, throughput_mbps=0.046),
                    "flow_bulk": FlowMetrics(flow_id="flow_bulk", traffic_type=TrafficType.BULK_DOWNLOAD, packets_recv=1400, bytes_recv=1960000, throughput_mbps=15.7),
                },
                queue=QueueMetrics(timestamp=101.0, interface_name="sim0", capacity_mbps=20.0, backlog_bytes=20000),
            ),
        ]

        exp_res = ExperimentResult(
            experiment_id="test_exp",
            start_time=100.0,
            end_time=102.0,
            config=ExperimentConfig(experiment_id="test_exp", name="Test", description=""),
            snapshots=snaps,
        )

        artifacts = pipeline.process_experiment(exp_res)
        assert os.path.exists(artifacts["classification_results_json"])
        assert os.path.exists(artifacts["capacity_estimation_json"])
        assert os.path.exists(artifacts["classification_timeseries_csv"])
        assert os.path.exists(artifacts["phase2_summary_md"])
        assert "flow_gaming" in artifacts["flow_summary"]
        assert artifacts["flow_summary"]["flow_gaming"]["predicted_type"] == "gaming"
