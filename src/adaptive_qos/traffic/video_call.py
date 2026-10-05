"""Video conferencing / interactive call traffic generator (WebRTC/RTP style UDP stream)."""

import socket
import threading
import time
import zlib
from adaptive_qos.common.models import FlowConfig, FlowMetrics, TrafficType
from adaptive_qos.common.logger import get_logger
from adaptive_qos.traffic.base import BaseTrafficGenerator, pack_packet

logger = get_logger("traffic.video_call")


class VideoCallTrafficGenerator(BaseTrafficGenerator):
    """
    Simulates interactive video conferencing (Zoom / Teams / WebRTC):
    - Video frames generated at frame_rate_fps (e.g. 30 fps) split across packet bursts.
    - Audio packets generated every 20 ms.
    - Target aggregate bitrate ~ 1.5 - 3.5 Mbps.
    """

    def __init__(self, config: FlowConfig):
        super().__init__(config)
        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._flow_hash = zlib.crc32(self.config.flow_id.encode("utf-8")) & 0xFFFFFFFF
        self._fps = self.config.extra_params.get("fps", 30.0)
        self._target_mbps = self.config.target_rate_mbps or 2.5
        self._packet_size = self.config.packet_size_bytes or 1200
        self._socket: socket.socket = None

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()
        self.start_time = time.time()
        self.packets_sent = 0
        self.bytes_sent = 0

        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"video-call-gen-{self.config.flow_id}",
            daemon=True
        )
        self._thread.start()
        logger.info(f"Started video call traffic generator for {self.config.flow_id} (~{self._target_mbps} Mbps)")

    def _run_loop(self) -> None:
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if self.config.start_offset_sec > 0:
                time.sleep(self.config.start_offset_sec)

            frame_interval = 1.0 / self._fps
            # Calculate bytes per frame needed to achieve target_mbps
            bytes_per_second = (self._target_mbps * 1_000_000.0) / 8.0
            bytes_per_frame = bytes_per_second / self._fps
            packets_per_frame = max(1, int(bytes_per_frame / self._packet_size))

            next_frame_time = time.time()

            while not self._stop_event.is_set():
                now = time.time()
                if self.config.duration_sec and (now - self.start_time) >= self.config.duration_sec:
                    break

                # Send frame packet burst (simulating encoded video frame slice delivery)
                for _ in range(packets_per_frame):
                    if self._stop_event.is_set():
                        break
                    self.packets_sent += 1
                    pkt_data = pack_packet(
                        flow_id_hash=self._flow_hash,
                        seq_num=self.packets_sent,
                        timestamp=time.time(),
                        payload_size=self._packet_size
                    )
                    try:
                        self._socket.sendto(pkt_data, (self.config.dst_ip, self.config.dst_port))
                        self.bytes_sent += len(pkt_data)
                    except Exception as e:
                        logger.debug(f"Video call send error: {e}")

                next_frame_time += frame_interval
                sleep_time = next_frame_time - time.time()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    next_frame_time = time.time()

        except Exception as e:
            logger.error(f"Error in video call generator loop: {e}")
        finally:
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
            self.stop_time = time.time()
            self.is_running = False

    def stop(self) -> None:
        if not self.is_running:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.is_running = False
        self.stop_time = time.time()
        logger.info(f"Stopped video call generator {self.config.flow_id}. Sent {self.packets_sent} pkts")

    def get_stats(self) -> FlowMetrics:
        now = time.time()
        duration = max(0.001, (self.stop_time or now) - (self.start_time or now))
        throughput_mbps = (self.bytes_sent * 8.0) / (duration * 1_000_000.0)
        return FlowMetrics(
            flow_id=self.config.flow_id,
            traffic_type=TrafficType.VIDEO_CALL,
            packets_sent=self.packets_sent,
            bytes_sent=self.bytes_sent,
            throughput_mbps=round(throughput_mbps, 3)
        )
