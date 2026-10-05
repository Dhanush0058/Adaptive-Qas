"""Traffic Runner to manage and orchestrate multiple simultaneous traffic flows."""

from typing import Dict, List
import time
from adaptive_qos.common.models import FlowConfig, FlowMetrics, TrafficType
from adaptive_qos.common.logger import get_logger
from adaptive_qos.traffic.base import BaseTrafficGenerator
from adaptive_qos.traffic.gaming import GamingTrafficGenerator
from adaptive_qos.traffic.video_call import VideoCallTrafficGenerator
from adaptive_qos.traffic.video_streaming import VideoStreamingTrafficGenerator
from adaptive_qos.traffic.bulk_download import BulkDownloadTrafficGenerator

logger = get_logger("traffic.runner")


class TrafficRunner:
    """Orchestrates starting, stopping, and polling multiple traffic generator instances."""

    def __init__(self, flows: List[FlowConfig]):
        self.flow_configs = flows
        self.generators: Dict[str, BaseTrafficGenerator] = {}
        self._init_generators()

    def _init_generators(self) -> None:
        for flow in self.flow_configs:
            if flow.traffic_type == TrafficType.GAMING:
                gen = GamingTrafficGenerator(flow)
            elif flow.traffic_type == TrafficType.VIDEO_CALL:
                gen = VideoCallTrafficGenerator(flow)
            elif flow.traffic_type == TrafficType.VIDEO_STREAMING:
                gen = VideoStreamingTrafficGenerator(flow)
            elif flow.traffic_type == TrafficType.BULK_DOWNLOAD:
                gen = BulkDownloadTrafficGenerator(flow)
            else:
                logger.warning(f"Unknown traffic type {flow.traffic_type}, skipping generator.")
                continue
            self.generators[flow.flow_id] = gen

    def start_all(self) -> None:
        """Start all configured traffic generators."""
        logger.info(f"Starting {len(self.generators)} traffic flows...")
        for flow_id, gen in self.generators.items():
            gen.start()

    def stop_all(self) -> None:
        """Stop all running traffic generators."""
        logger.info(f"Stopping {len(self.generators)} traffic flows...")
        for flow_id, gen in self.generators.items():
            gen.stop()

    def get_stats(self) -> Dict[str, FlowMetrics]:
        """Collect current stats from all generators."""
        return {flow_id: gen.get_stats() for flow_id, gen in self.generators.items()}
