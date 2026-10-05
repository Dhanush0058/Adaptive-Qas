"""Unit tests for Adaptive QoS data models."""

import pytest
import json
from adaptive_qos.common.models import (
    TrafficType,
    TransportProtocol,
    FlowConfig,
    FlowMetrics,
    QueueMetrics,
    TimeSliceSnapshot,
    TopologyConfig,
    ExperimentConfig,
    ExperimentResult,
)


def test_flow_config_serialization():
    fc = FlowConfig(
        flow_id="test_flow",
        traffic_type=TrafficType.GAMING,
        protocol=TransportProtocol.UDP,
        src_ip="10.0.1.2",
        dst_ip="10.0.2.2",
        src_port=4001,
        dst_port=5001,
        target_rate_mbps=0.1,
    )
    d = fc.to_dict()
    assert d["flow_id"] == "test_flow"
    assert d["traffic_type"] == "gaming"
    assert d["protocol"] == "udp"
    # Ensure JSON serializable
    json_str = json.dumps(d)
    assert "test_flow" in json_str


def test_flow_metrics():
    fm = FlowMetrics(
        flow_id="flow1",
        traffic_type=TrafficType.VIDEO_CALL,
        packets_sent=100,
        packets_recv=98,
        bytes_recv=120000,
        throughput_mbps=2.4,
        latency_ms=18.5,
        jitter_ms=1.2,
        packet_loss_pct=2.0,
    )
    d = fm.to_dict()
    assert d["throughput_mbps"] == 2.4
    assert d["packet_loss_pct"] == 2.0


def test_time_slice_snapshot():
    qm = QueueMetrics(
        timestamp=1000.0,
        interface_name="veth-wan",
        capacity_mbps=100.0,
        backlog_bytes=5000,
        backlog_packets=4,
        dropped_packets=2,
        overlimit_count=5,
    )
    fm = FlowMetrics(flow_id="g1", traffic_type=TrafficType.GAMING, latency_ms=12.0)
    snap = TimeSliceSnapshot(
        timestamp=1000.0,
        elapsed_sec=5.0,
        wan_capacity_mbps=100.0,
        is_congested=False,
        flows={"g1": fm},
        queue=qm,
    )
    d = snap.to_dict()
    assert d["elapsed_sec"] == 5.0
    assert d["flows"]["g1"]["latency_ms"] == 12.0
    assert d["queue"]["backlog_bytes"] == 5000
