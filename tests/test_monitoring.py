"""Unit tests for traffic monitoring components."""

import time
import socket
import pytest
from adaptive_qos.common.models import FlowConfig, TrafficType, TransportProtocol
from adaptive_qos.monitoring.passive import PassiveTrafficMonitor
from adaptive_qos.monitoring.queue_monitor import QueueMonitor
from adaptive_qos.traffic.base import pack_packet
import zlib


def test_passive_monitor_reception():
    flow = FlowConfig(
        flow_id="test_passive",
        traffic_type=TrafficType.GAMING,
        protocol=TransportProtocol.UDP,
        src_ip="127.0.0.1",
        dst_ip="127.0.0.1",
        src_port=4020,
        dst_port=5020,
    )
    monitor = PassiveTrafficMonitor([flow], bind_ip="127.0.0.1")
    monitor.start()

    time.sleep(0.1)

    # Send 5 test packets
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    flow_hash = zlib.crc32(b"test_passive") & 0xFFFFFFFF
    for i in range(1, 6):
        data = pack_packet(flow_hash, i, time.time(), 100)
        sock.sendto(data, ("127.0.0.1", 5020))
        time.sleep(0.01)

    time.sleep(0.1)
    snapshot = monitor.collect_snapshot(window_sec=1.0)
    monitor.stop()
    sock.close()

    assert "test_passive" in snapshot
    m = snapshot["test_passive"]
    assert m.packets_recv == 5
    assert m.throughput_mbps > 0.0


def test_queue_monitor_simulated_callback():
    from adaptive_qos.common.models import QueueMetrics
    def mock_cb():
        return QueueMetrics(
            timestamp=time.time(),
            interface_name="sim0",
            capacity_mbps=100.0,
            backlog_bytes=1000,
            backlog_packets=1,
            dropped_packets=0,
        )

    qm = QueueMonitor(sim_queue_callback=mock_cb)
    metrics = qm.collect()
    assert metrics.backlog_bytes == 1000
    assert metrics.capacity_mbps == 100.0
