"""Passive traffic monitoring receiver and per-flow metric aggregator."""

import socket
import select
import threading
import time
import zlib
from typing import Dict, List, Optional, Tuple
from adaptive_qos.common.models import FlowConfig, FlowMetrics, TrafficType
from adaptive_qos.common.logger import get_logger
from adaptive_qos.traffic.base import unpack_packet, compute_rfc3550_jitter

logger = get_logger("monitoring.passive")


class FlowReceiverTracker:
    """Tracks metrics for a single flow at the receiver."""

    def __init__(self, config: FlowConfig):
        self.config = config
        self.flow_hash = zlib.crc32(config.flow_id.encode("utf-8")) & 0xFFFFFFFF
        self.packets_recv = 0
        self.bytes_recv = 0
        self.expected_seq = 1
        self.lost_packets = 0
        self.out_of_order = 0
        self.last_seq = 0

        # Transit delays (recv_time - send_time) in ms
        self.window_delays: List[float] = []
        self.window_bytes = 0
        self.window_packets = 0
        self.last_transit_ms: Optional[float] = None
        self.jitter_ms: float = 0.0
        self.last_window_time = time.time()
        self.lock = threading.Lock()

    def process_packet(self, seq_num: int, send_timestamp: float, packet_len: int, recv_time: float) -> None:
        delay_ms = max(0.0, (recv_time - send_timestamp) * 1000.0)

        with self.lock:
            self.packets_recv += 1
            self.bytes_recv += packet_len
            self.window_bytes += packet_len
            self.window_packets += 1
            self.window_delays.append(delay_ms)

            # Check sequence progression
            if self.last_seq > 0:
                if seq_num > self.last_seq + 1:
                    self.lost_packets += (seq_num - self.last_seq - 1)
                elif seq_num < self.last_seq:
                    self.out_of_order += 1
            self.last_seq = max(self.last_seq, seq_num)

            # Jitter calculation (RFC 3550)
            if self.last_transit_ms is not None:
                self.jitter_ms = compute_rfc3550_jitter(
                    self.jitter_ms, self.last_transit_ms, delay_ms
                )
            self.last_transit_ms = delay_ms

    def snapshot_and_reset_window(self, window_duration_sec: float) -> FlowMetrics:
        """Produce snapshot for the past time window and reset window counters."""
        with self.lock:
            dur = max(0.001, window_duration_sec)
            throughput_mbps = (self.window_bytes * 8.0) / (dur * 1_000_000.0)
            delays = list(self.window_delays)
            self.window_delays.clear()
            self.window_bytes = 0
            self.window_packets = 0
            jitter = self.jitter_ms
            total_recv = self.packets_recv
            total_lost = self.lost_packets

        if delays:
            avg_delay = sum(delays) / len(delays)
            min_delay = min(delays)
            max_delay = max(delays)
            sorted_delays = sorted(delays)
            p95_delay = sorted_delays[int(len(sorted_delays) * 0.95)]
        else:
            avg_delay = min_delay = max_delay = p95_delay = 0.0

        total_expected = total_recv + total_lost
        loss_pct = ((total_lost) / total_expected * 100.0) if total_expected > 0 else 0.0

        return FlowMetrics(
            flow_id=self.config.flow_id,
            traffic_type=self.config.traffic_type,
            packets_sent=total_expected,
            packets_recv=total_recv,
            bytes_recv=self.bytes_recv,
            throughput_mbps=round(throughput_mbps, 3),
            latency_ms=round(avg_delay, 2),
            min_latency_ms=round(min_delay, 2),
            max_latency_ms=round(max_delay, 2),
            p95_latency_ms=round(p95_delay, 2),
            jitter_ms=round(jitter, 2),
            packet_loss_pct=round(loss_pct, 2),
        )


class PassiveTrafficMonitor:
    """
    Listens on ports for incoming traffic flows, extracts wire protocol headers,
    and updates per-flow metrics in real-time.
    """

    def __init__(self, flows: List[FlowConfig], bind_ip: str = "0.0.0.0"):
        self.flow_configs = flows
        self.bind_ip = bind_ip
        self.trackers: Dict[str, FlowReceiverTracker] = {}
        self.hash_to_flow_id: Dict[int, str] = {}
        self.sockets: List[socket.socket] = []
        self.is_running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        for flow in flows:
            tracker = FlowReceiverTracker(flow)
            self.trackers[flow.flow_id] = tracker
            self.hash_to_flow_id[tracker.flow_hash] = flow.flow_id

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()

        # Bind UDP sockets for all unique destination ports
        ports = set(f.dst_port for f in self.flow_configs)
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self.bind_ip, port))
                sock.setblocking(False)
                self.sockets.append(sock)
                logger.info(f"Passive monitor listening on {self.bind_ip}:{port}")
            except Exception as e:
                logger.error(f"Failed to bind socket on port {port}: {e}")

        self._thread = threading.Thread(target=self._listen_loop, name="passive-monitor", daemon=True)
        self._thread.start()

    def _listen_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.sockets:
                break
            try:
                readable, _, _ = select.select(self.sockets, [], [], 0.1)
                recv_time = time.time()
                for sock in readable:
                    while True:
                        try:
                            data, addr = sock.recvfrom(65535)
                            unpacked = unpack_packet(data)
                            if unpacked is not None:
                                flow_hash, seq_num, send_time, pkt_len = unpacked
                                flow_id = self.hash_to_flow_id.get(flow_hash)
                                if flow_id and flow_id in self.trackers:
                                    self.trackers[flow_id].process_packet(
                                        seq_num, send_time, pkt_len, recv_time
                                    )
                        except (BlockingIOError, socket.error):
                            break
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.debug(f"Passive monitor error: {e}")

    def collect_snapshot(self, window_sec: float = 1.0) -> Dict[str, FlowMetrics]:
        """Collect current metrics snapshot across all tracked flows."""
        snapshot = {}
        for flow_id, tracker in self.trackers.items():
            snapshot[flow_id] = tracker.snapshot_and_reset_window(window_sec)
        return snapshot

    def stop(self) -> None:
        if not self.is_running:
            return
        self._stop_event.set()
        for sock in self.sockets:
            try:
                sock.close()
            except Exception:
                pass
        self.sockets.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.is_running = False
        logger.info("Passive traffic monitor stopped.")
