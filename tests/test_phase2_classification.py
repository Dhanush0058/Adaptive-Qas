"""Unit tests for Phase 2 Traffic Classification and Feature Extraction."""

import pytest
from adaptive_qos.common.models import FlowMetrics, TrafficType, FlowConfig, TransportProtocol
from adaptive_qos.classification.models import (
    ClassificationCategory,
    DataSource,
    FlowFeatures,
)
from adaptive_qos.classification.features import FlowFeatureExtractor
from adaptive_qos.classification.classifier import TrafficClassifier


def test_feature_extraction():
    extractor = FlowFeatureExtractor(history_window_size=5)
    metric = FlowMetrics(
        flow_id="f_game",
        traffic_type=TrafficType.GAMING,
        packets_recv=60,
        bytes_recv=5760,  # 96 bytes/pkt
        throughput_mbps=0.046,
        latency_ms=10.0,
        jitter_ms=1.2,
    )
    features = extractor.extract(metric, elapsed_sec=1.0)
    assert features.flow_id == "f_game"
    assert abs(features.packet_rate_pps - 60.0) < 1.0
    assert abs(features.avg_packet_size_bytes - 96.0) < 1.0
    assert features.throughput_mbps == 0.046
    assert features.burstiness_ratio == 1.0


def test_gaming_classification():
    classifier = TrafficClassifier(data_source=DataSource.LINUX_REAL)
    # Gaming profile: small packets (96B), low throughput (0.08 Mbps), 60 pps
    gaming_features = FlowFeatures(
        flow_id="test_gaming",
        protocol="UDP",
        packet_rate_pps=60.0,
        throughput_mbps=0.08,
        avg_packet_size_bytes=96.0,
        burstiness_ratio=1.0,
        duty_cycle=1.0,
    )
    res = classifier.classify_features(gaming_features)
    assert res.classification == ClassificationCategory.GAMING
    assert res.confidence >= 0.75
    assert res.data_source == DataSource.LINUX_REAL
    assert "gaming" in res.category_scores


def test_video_call_classification():
    classifier = TrafficClassifier(data_source=DataSource.LINUX_REAL)
    # Video call profile: 1200B packets, 2.5 Mbps, 260 pps
    call_features = FlowFeatures(
        flow_id="test_call",
        protocol="UDP",
        packet_rate_pps=260.0,
        throughput_mbps=2.5,
        avg_packet_size_bytes=1200.0,
        burstiness_ratio=1.1,
        duty_cycle=1.0,
    )
    res = classifier.classify_features(call_features)
    assert res.classification == ClassificationCategory.VIDEO_CALL
    assert res.confidence >= 0.70
    assert res.data_source == DataSource.LINUX_REAL


def test_video_streaming_classification():
    classifier = TrafficClassifier(data_source=DataSource.SIMULATION)
    # Video streaming profile: MTU size packets, bursty chunk pattern (burstiness ratio 2.5)
    stream_features = FlowFeatures(
        flow_id="test_streaming",
        protocol="UDP",
        packet_rate_pps=700.0,
        throughput_mbps=8.0,
        avg_packet_size_bytes=1400.0,
        burstiness_ratio=2.5,
        duty_cycle=0.4,
    )
    res = classifier.classify_features(stream_features)
    assert res.classification == ClassificationCategory.VIDEO_STREAMING
    assert res.confidence >= 0.70
    assert res.data_source == DataSource.SIMULATION


def test_bulk_download_classification():
    classifier = TrafficClassifier(data_source=DataSource.LINUX_REAL)
    # Bulk download profile: steady continuous saturating throughput (18 Mbps), duty cycle 1.0
    bulk_features = FlowFeatures(
        flow_id="test_bulk",
        protocol="UDP",
        packet_rate_pps=1600.0,
        throughput_mbps=18.0,
        avg_packet_size_bytes=1400.0,
        burstiness_ratio=1.05,
        duty_cycle=1.0,
    )
    res = classifier.classify_features(bulk_features)
    assert res.classification == ClassificationCategory.BULK_DOWNLOAD
    assert res.confidence >= 0.75
    assert res.data_source == DataSource.LINUX_REAL


def test_unknown_ambiguous_traffic_classification():
    classifier = TrafficClassifier(data_source=DataSource.SIMULATION)
    # Ambiguous/noise traffic with atypical parameters
    weird_features = FlowFeatures(
        flow_id="test_noise",
        protocol="UDP",
        packet_rate_pps=2.0,
        throughput_mbps=0.001,
        avg_packet_size_bytes=35.0,
        burstiness_ratio=5.0,
    )
    res = classifier.classify_features(weird_features)
    # Either interactive or background_unknown with moderate-to-low confidence
    assert res.confidence < 0.75
    assert res.classification in [ClassificationCategory.BACKGROUND_UNKNOWN, ClassificationCategory.INTERACTIVE]


def test_confidence_calculation_behavior():
    classifier = TrafficClassifier()
    # High confidence clear gaming flow
    clear_game = FlowFeatures(
        flow_id="f1",
        protocol="UDP",
        packet_rate_pps=60.0,
        throughput_mbps=0.06,
        avg_packet_size_bytes=96.0,
    )
    res_clear = classifier.classify_features(clear_game)

    # Ambiguous flow on the boundary between video call and bulk
    border_flow = FlowFeatures(
        flow_id="f2",
        protocol="UDP",
        packet_rate_pps=500.0,
        throughput_mbps=6.0,
        avg_packet_size_bytes=1350.0,
        burstiness_ratio=1.2,
    )
    res_border = classifier.classify_features(border_flow)

    # Dynamic confidence must be higher for the clear case than the ambiguous case
    assert res_clear.confidence > res_border.confidence
