"""Bulk download traffic generator (saturating TCP/UDP bulk flow)."""

import socket
import threading
import time
import zlib
from adaptive_qos.common.models import FlowConfig, FlowMetrics, TrafficType
from adaptive_qos.common.logger import get_logger
from adaptive_qos.traffic.base import BaseTrafficGenerator, pack_packet

logger = get_logger("traffic.bulk_download")


class BulkDownloadTrafficGenerator(BaseTrafficGenerator):
    """
    Simulates large file downloads / ISO / OS updates / cloud backup:
    - High-volume, continuous throughput designed to consume all available capacity.
    - Generates saturating packet stream with minimal inter-packet delay.
    """

    def __init__(self, config: FlowConfig):
        super().__init__(config)
        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._flow_hash = zlib.crc32(self.config.flow_id.encode("utf-8")) & 0xFFFFFFFF
        self._packet_size = self.config.packet_size_bytes or 1400
        self._target_rate_mbps = self.config.target_rate_mbps or 0.0  # 0 means unconstrained
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
            name=f"bulk-gen-{self.config.flow_id}",
            daemon=True
        )
        self._thread.start()
        logger.info(f"Started bulk download generator for {self.config.flow_id}")

    def _run_loop(self) -> None:
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if self.config.start_offset_sec > 0:
                time.sleep(self.config.start_offset_sec)

            # If rate limited, calculate inter-packet interval
            interval = 0.0
            if self._target_rate_mbps > 0:
                bytes_per_sec = (self._target_rate_mbps * 1_000_000.0) / 8.0
                pkts_per_sec = bytes_per_sec / self._packet_size
                interval = 1.0 / pkts_per_sec

            next_send = time.time()

            while not self._stop_event.is_set():
                now = time.time()
                if self.config.duration_sec and (now - self.start_time) >= self.config.duration_sec:
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
                    logger.debug(f"Bulk download send error: {e}")

                if interval > 0:
                    next_send += interval
                    sleep_time = next_send - time.time()
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    else:
                        next_send = time.time()
                else:
                    # Saturating tight loop: tiny yield to prevent CPU lockup
                    time.sleep(0.00002)

        except Exception as e:
            logger.error(f"Error in bulk download loop: {e}")
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
        logger.info(f"Stopped bulk download generator {self.config.flow_id}. Sent {self.packets_sent} pkts")

    def get_stats(self) -> FlowMetrics:
        now = time.time()
        duration = max(0.001, (self.stop_time or now) - (self.start_time or now))
        throughput_mbps = (self.bytes_sent * 8.0) / (duration * 1_000_000.0)
        return FlowMetrics(
            flow_id=self.config.flow_id,
            traffic_type=TrafficType.BULK_DOWNLOAD,
            packets_sent=self.packets_sent,
            bytes_sent=self.bytes_sent,
            throughput_mbps=round(throughput_mbps, 3)
        )
