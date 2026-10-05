"""Video streaming traffic generator (DASH/HLS chunked bursty HTTP-style traffic)."""

import socket
import threading
import time
import zlib
from adaptive_qos.common.models import FlowConfig, FlowMetrics, TrafficType
from adaptive_qos.common.logger import get_logger
from adaptive_qos.traffic.base import BaseTrafficGenerator, pack_packet

logger = get_logger("traffic.video_streaming")


class VideoStreamingTrafficGenerator(BaseTrafficGenerator):
    """
    Simulates adaptive video streaming (DASH / HLS):
    - Bursty chunk download behavior: every chunk_interval_sec, a chunk of size (e.g. 2-5 MB)
      is downloaded as fast as possible, followed by an idle/sleep period while playing.
    - Represents dynamic media streaming that creates severe buffer fluctuations.
    """

    def __init__(self, config: FlowConfig):
        super().__init__(config)
        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._flow_hash = zlib.crc32(self.config.flow_id.encode("utf-8")) & 0xFFFFFFFF
        self._chunk_size_bytes = self.config.extra_params.get("chunk_size_bytes", 1_500_000)  # 1.5MB per chunk
        self._chunk_interval_sec = self.config.extra_params.get("chunk_interval_sec", 3.0)
        self._packet_size = self.config.packet_size_bytes or 1400
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
            name=f"video-stream-gen-{self.config.flow_id}",
            daemon=True
        )
        self._thread.start()
        logger.info(
            f"Started video streaming generator for {self.config.flow_id} "
            f"({self._chunk_size_bytes / 1e6:.1f} MB chunk every {self._chunk_interval_sec}s)"
        )

    def _run_loop(self) -> None:
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if self.config.start_offset_sec > 0:
                time.sleep(self.config.start_offset_sec)

            while not self._stop_event.is_set():
                now = time.time()
                if self.config.duration_sec and (now - self.start_time) >= self.config.duration_sec:
                    break

                chunk_start = time.time()
                # Transmit an entire chunk in a burst
                bytes_sent_in_chunk = 0
                while bytes_sent_in_chunk < self._chunk_size_bytes and not self._stop_event.is_set():
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
                        bytes_sent_in_chunk += len(pkt_data)
                    except Exception as e:
                        logger.debug(f"Video streaming send error: {e}")

                    # Micro-pace inside chunk to avoid instantaneous kernel drop
                    time.sleep(0.00005)

                chunk_duration = time.time() - chunk_start
                sleep_needed = max(0.1, self._chunk_interval_sec - chunk_duration)
                
                # Sleep until next chunk or stop
                self._stop_event.wait(timeout=sleep_needed)

        except Exception as e:
            logger.error(f"Error in video streaming generator loop: {e}")
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
        logger.info(f"Stopped video streaming generator {self.config.flow_id}. Sent {self.packets_sent} pkts")

    def get_stats(self) -> FlowMetrics:
        now = time.time()
        duration = max(0.001, (self.stop_time or now) - (self.start_time or now))
        throughput_mbps = (self.bytes_sent * 8.0) / (duration * 1_000_000.0)
        return FlowMetrics(
            flow_id=self.config.flow_id,
            traffic_type=TrafficType.VIDEO_STREAMING,
            packets_sent=self.packets_sent,
            bytes_sent=self.bytes_sent,
            throughput_mbps=round(throughput_mbps, 3)
        )
