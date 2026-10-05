"""Unit tests for network topology implementations."""

import time
import pytest
from adaptive_qos.common.models import TopologyConfig
from adaptive_qos.topology.simulation import SimulatedTopology


def test_simulated_topology_queue_and_drop():
    cfg = TopologyConfig(
        initial_bandwidth_mbps=10.0,
        congested_bandwidth_mbps=1.0,
        buffer_queue_limit_packets=10,  # Max 10 packets * 1500 = 15000 bytes
    )
    topo = SimulatedTopology(cfg)
    topo.setup()

    # Enqueue within limit
    for _ in range(5):
        accepted = topo.enqueue_packet(1400)
        assert accepted is True

    # Enqueue past limit to force drop
    drops = 0
    for _ in range(20):
        if not topo.enqueue_packet(1400):
            drops += 1

    assert drops > 0
    q = topo.get_queue_metrics()
    assert q.dropped_packets > 0

    # Test throttle
    topo.set_wan_bandwidth(2.0)
    assert topo.current_bandwidth_mbps == 2.0
    topo.teardown()
