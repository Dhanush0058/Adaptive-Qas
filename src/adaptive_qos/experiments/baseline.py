"""Phase 1 Baseline Experiment: Unmanaged FIFO network under 100 Mbps -> 20 Mbps congestion."""

from adaptive_qos.common.models import (
    ExperimentConfig,
    FlowConfig,
    TrafficType,
    TransportProtocol,
    TopologyConfig,
)


def create_baseline_experiment_config(
    dst_ip: str = "127.0.0.1",
    total_duration_sec: float = 30.0,
    congestion_start_sec: float = 10.0,
    congestion_end_sec: float = 25.0,
) -> ExperimentConfig:
    """Build the configuration for the Phase 1 Baseline Congestion Experiment."""
    topology = TopologyConfig(
        initial_bandwidth_mbps=100.0,
        congested_bandwidth_mbps=20.0,
        wan_base_rtt_ms=15.0,
        buffer_queue_limit_packets=100,
    )

    flows = [
        FlowConfig(
            flow_id="flow_gaming",
            traffic_type=TrafficType.GAMING,
            protocol=TransportProtocol.UDP,
            src_ip="10.0.1.2",
            dst_ip=dst_ip,
            src_port=4001,
            dst_port=5001,
            target_rate_mbps=0.1,
            packet_size_bytes=96,
            duration_sec=total_duration_sec,
            extra_params={"tick_rate_hz": 60.0},
        ),
        FlowConfig(
            flow_id="flow_videocall",
            traffic_type=TrafficType.VIDEO_CALL,
            protocol=TransportProtocol.UDP,
            src_ip="10.0.1.2",
            dst_ip=dst_ip,
            src_port=4002,
            dst_port=5002,
            target_rate_mbps=2.5,
            packet_size_bytes=1200,
            duration_sec=total_duration_sec,
            extra_params={"fps": 30.0},
        ),
        FlowConfig(
            flow_id="flow_streaming",
            traffic_type=TrafficType.VIDEO_STREAMING,
            protocol=TransportProtocol.UDP,
            src_ip="10.0.1.2",
            dst_ip=dst_ip,
            src_port=4003,
            dst_port=5003,
            target_rate_mbps=8.0,
            packet_size_bytes=1400,
            duration_sec=total_duration_sec,
            extra_params={"chunk_size_bytes": 1_000_000, "chunk_interval_sec": 2.5},
        ),
        FlowConfig(
            flow_id="flow_bulk",
            traffic_type=TrafficType.BULK_DOWNLOAD,
            protocol=TransportProtocol.UDP,
            src_ip="10.0.1.2",
            dst_ip=dst_ip,
            src_port=4004,
            dst_port=5004,
            target_rate_mbps=0.0,  # Unconstrained saturating flow
            packet_size_bytes=1400,
            duration_sec=total_duration_sec,
        ),
    ]

    return ExperimentConfig(
        experiment_id="baseline_phase1",
        name="Phase 1 Baseline Congestion Experiment",
        description="Evaluates mixed broadband traffic degradation (Gaming, Video Call, Streaming, Bulk) as WAN drops from 100 Mbps to 20 Mbps without QoS.",
        topology=topology,
        total_duration_sec=total_duration_sec,
        congestion_start_sec=congestion_start_sec,
        congestion_end_sec=congestion_end_sec,
        sampling_interval_sec=1.0,
        flows=flows,
    )
