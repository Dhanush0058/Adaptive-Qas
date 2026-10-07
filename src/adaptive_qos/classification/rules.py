"""Rule definitions and mathematical similarity kernels for traffic classification."""

import math
from typing import Dict, Tuple
from adaptive_qos.classification.models import FlowFeatures, ClassificationCategory


def gaussian_similarity(x: float, center: float, width: float) -> float:
    """Computes Gaussian similarity in [0, 1]. Returns 1.0 at center, decays with width."""
    if width <= 0:
        return 1.0 if x == center else 0.0
    return math.exp(-0.5 * ((x - center) / width) ** 2)


def sigmoid_threshold(x: float, midpoint: float, slope: float, higher_is_better: bool = True) -> float:
    """Sigmoidal transition function in [0, 1]."""
    z = (x - midpoint) * slope if higher_is_better else (midpoint - x) * slope
    # Clamp z to avoid math overflow
    z = max(-20.0, min(20.0, z))
    return 1.0 / (1.0 + math.exp(-z))


class ClassificationRules:
    """
    Evaluates observable non-payload features against traffic category profiles.
    Returns calculated similarity scores in [0.0, 1.0] for each category.
    """

    @staticmethod
    def score_gaming(f: FlowFeatures) -> Tuple[float, str]:
        """
        Gaming Profile:
        - Small packet size: 50 - 250 bytes (centered ~96-128 bytes)
        - Moderate/High steady packet rate: 30 - 140 pps (ticks)
        - Very low throughput: < 0.5 Mbps (typically 0.05 - 0.2 Mbps)
        - Protocol: UDP preferred
        - Low burstiness (continuous stream)
        """
        reasons = []

        # 1. Packet size score (peaks at 96B, drops above 300B)
        size_score = gaussian_similarity(f.avg_packet_size_bytes, center=100.0, width=80.0)
        if f.avg_packet_size_bytes > 300.0:
            size_score *= 0.1

        # 2. Throughput score (high when < 0.3 Mbps, penalizes > 1.0 Mbps)
        tput_score = sigmoid_threshold(f.throughput_mbps, midpoint=0.4, slope=10.0, higher_is_better=False)

        # 3. Packet rate score (peaks at 60-120 pps, penalizes < 15 pps)
        rate_score = gaussian_similarity(f.packet_rate_pps, center=60.0, width=50.0)
        if f.packet_rate_pps < 15.0:
            rate_score *= 0.1
        if f.burstiness_ratio > 1.8:
            rate_score *= 0.2

        # 4. Protocol bonus
        proto_score = 1.0 if f.protocol == "UDP" else 0.5

        # Weighted combination
        weights = [0.40, 0.30, 0.20, 0.10]
        total_score = (
            weights[0] * size_score
            + weights[1] * tput_score
            + weights[2] * rate_score
            + weights[3] * proto_score
        )

        reasons.append(f"Small pkts ({f.avg_packet_size_bytes:.0f}B), low tput ({f.throughput_mbps:.2f}M), rate {f.packet_rate_pps:.0f}pps")
        return total_score, "; ".join(reasons)

    @staticmethod
    def score_video_call(f: FlowFeatures) -> Tuple[float, str]:
        """
        Interactive Video Call Profile (Zoom / Teams / WebRTC):
        - Medium/Large packet size: 800 - 1300 bytes (video frame slices)
        - Moderate throughput: 1.0 - 4.0 Mbps (typical 720p/1080p stream)
        - Steady frame-rate transmission: 25 - 60 fps bursts (packet rate 50 - 300 pps)
        - Protocol: UDP
        """
        reasons = []

        # 1. Packet size score (peaks at 1200B)
        size_score = gaussian_similarity(f.avg_packet_size_bytes, center=1200.0, width=300.0)

        # 2. Throughput score (peaks at 2.5 Mbps, range 1.0 - 4.5 Mbps)
        tput_score = gaussian_similarity(f.throughput_mbps, center=2.5, width=1.5)

        # 3. Packet rate (peaks at 100 - 300 pps)
        rate_score = gaussian_similarity(f.packet_rate_pps, center=200.0, width=150.0)

        # 4. Protocol match
        proto_score = 1.0 if f.protocol == "UDP" else 0.6

        weights = [0.35, 0.35, 0.20, 0.10]
        total_score = (
            weights[0] * size_score
            + weights[1] * tput_score
            + weights[2] * rate_score
            + weights[3] * proto_score
        )

        reasons.append(f"MTU-slice pkts ({f.avg_packet_size_bytes:.0f}B), call bitrate ({f.throughput_mbps:.2f}M)")
        return total_score, "; ".join(reasons)

    @staticmethod
    def score_video_streaming(f: FlowFeatures) -> Tuple[float, str]:
        """
        Video Streaming Profile (DASH / HLS / YouTube / Netflix):
        - Bursty chunk download pattern: high peak-to-average ratio (burstiness_ratio > 1.5)
        - Large packet size: near MTU (1300 - 1500 bytes)
        - Moderate to high average throughput: 3.0 - 15.0 Mbps
        """
        reasons = []

        # 1. Burstiness / Chunk score (higher burstiness ratio favors streaming over continuous bulk)
        burst_score = sigmoid_threshold(f.burstiness_ratio, midpoint=1.3, slope=3.0, higher_is_better=True)

        # 2. Packet size score (near MTU 1400B)
        size_score = gaussian_similarity(f.avg_packet_size_bytes, center=1400.0, width=200.0)

        # 3. Throughput range (3.0 - 15.0 Mbps)
        tput_score = gaussian_similarity(f.throughput_mbps, center=8.0, width=6.0)

        weights = [0.45, 0.30, 0.25]
        total_score = (
            weights[0] * burst_score
            + weights[1] * size_score
            + weights[2] * tput_score
        )

        reasons.append(f"Bursty chunks (ratio {f.burstiness_ratio:.2f}), MTU pkts ({f.avg_packet_size_bytes:.0f}B)")
        return total_score, "; ".join(reasons)

    @staticmethod
    def score_bulk_download(f: FlowFeatures) -> Tuple[float, str]:
        """
        Bulk Download Profile:
        - Continuous high throughput (saturating link > 10 Mbps or consuming available bandwidth)
        - Continuous non-bursty duty cycle (burstiness_ratio ~ 1.0 - 1.2, duty_cycle ~ 1.0)
        - Large packet size: MTU 1400 - 1500 bytes
        - High packet rate: > 400 pps
        """
        reasons = []

        # 1. Sustained high throughput
        tput_score = sigmoid_threshold(f.throughput_mbps, midpoint=12.0, slope=0.5, higher_is_better=True)

        # 2. Large packet size (MTU)
        size_score = gaussian_similarity(f.avg_packet_size_bytes, center=1400.0, width=150.0)

        # 3. Continuous duty cycle (penalizes high burstiness, rewards steady stream)
        steady_score = sigmoid_threshold(f.burstiness_ratio, midpoint=1.4, slope=4.0, higher_is_better=False)

        weights = [0.45, 0.35, 0.20]
        total_score = (
            weights[0] * tput_score
            + weights[1] * size_score
            + weights[2] * steady_score
        )

        reasons.append(f"Saturating throughput ({f.throughput_mbps:.1f}M), steady duty cycle, MTU pkts ({f.avg_packet_size_bytes:.0f}B)")
        return total_score, "; ".join(reasons)

    @staticmethod
    def score_interactive(f: FlowFeatures) -> Tuple[float, str]:
        """
        Interactive / Real-time generic Profile (SSH, control channel, DNS, VoIP):
        - Low throughput (< 0.2 Mbps)
        - Low latency / low jitter requirement
        - Small-to-medium packets
        """
        tput_score = sigmoid_threshold(f.throughput_mbps, midpoint=0.2, slope=15.0, higher_is_better=False)
        size_score = sigmoid_threshold(f.avg_packet_size_bytes, midpoint=400.0, slope=0.02, higher_is_better=False)
        # Scale generic interactive score so specialized classes (gaming/video_call) take priority when they match
        total_score = (0.6 * tput_score + 0.4 * size_score) * 0.80
        return total_score, f"Low volume ({f.throughput_mbps:.2f}M), small frames ({f.avg_packet_size_bytes:.0f}B)"
