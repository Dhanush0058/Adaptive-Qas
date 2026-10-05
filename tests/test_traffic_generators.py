"""Unit tests for traffic generators and packet serialization."""

import time
import zlib
import pytest
from adaptive_qos.common.models import FlowConfig, TrafficType, TransportProtocol
from adaptive_qos.traffic.base import pack_packet, unpack_packet, compute_rfc3550_jitter
from adaptive_qos.traffic.gaming import GamingTrafficGenerator
from adaptive_qos.traffic.video_call import VideoCallTrafficGenerator
from adaptive_qos.traffic.video_streaming import VideoStreamingTrafficGenerator
from adaptive_qos.traffic.bulk_download import BulkDownloadTrafficGenerator
from adaptive_qos.traffic.runner import TrafficRunner


def test_packet_pack_unpack():
    flow_hash = zlib.crc32(b"flow_test") & 0xFFFFFFFF
    seq = 42
    ts = 1700000000.12345
    size = 1200

    packed = pack_packet(flow_hash, seq, ts, size)
    assert len(packed) == size

    res = unpack_packet(packed)
    assert res is not None
    unpacked_hash, unpacked_seq, unpacked_ts, unpacked_len = res
    assert unpacked_hash == flow_hash
    assert unpacked_seq == seq
    assert abs(unpacked_ts - ts) < 1e-4
    assert unpacked_len == size


def test_rfc3550_jitter_math():
    jitter = 0.0
    # First packet delay: 10ms, second packet delay: 20ms
    d1 = 10.0
    d2 = 20.0
    jitter = compute_rfc3550_jitter(jitter, d1, d2)
    expected = 0.0 + (10.0 - 0.0) / 16.0
    assert abs(jitter - expected) < 1e-4


def test_traffic_runner_lifecycle():
    flows = [
        FlowConfig(
            flow_id="f_game",
            traffic_type=TrafficType.GAMING,
            protocol=TransportProtocol.UDP,
            src_ip="127.0.0.1",
            dst_ip="127.0.0.1",
            src_port=4010,
            dst_port=5010,
            duration_sec=0.5,
        ),
        FlowConfig(
            flow_id="f_call",
            traffic_type=TrafficType.VIDEO_CALL,
            protocol=TransportProtocol.UDP,
            src_ip="127.0.0.1",
            dst_ip="127.0.0.1",
            src_port=4011,
            dst_port=5011,
            target_rate_mbps=1.0,
            duration_sec=0.5,
        ),
    ]
    runner = TrafficRunner(flows)
    runner.start_all()
    time.sleep(0.3)
    stats = runner.get_stats()
    assert "f_game" in stats
    assert "f_call" in stats
    assert stats["f_game"].packets_sent > 0
    assert stats["f_call"].packets_sent > 0
    runner.stop_all()
