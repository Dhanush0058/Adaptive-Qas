"""Active latency/RTT, jitter, and loss probing component."""

import socket
import struct
import threading
import time
from typing import Optional, List, Tuple
from adaptive_qos.common.models import FlowMetrics, TrafficType
from adaptive_qos.common.logger import get_logger
from adaptive_qos.traffic.base import compute_rfc3550_jitter

logger = get_logger("monitoring.probe")

PROBE_MAGIC = b"PR"
PROBE_FORMAT = "!2sId"  # magic, seq, send_time
PROBE_SIZE = struct.calcsize(PROBE_FORMAT)


class ProbeEchoResponder:
    """Lightweight echo responder that reflects received probe packets back to source."""

    def __init__(self, bind_ip: str = "0.0.0.0", bind_port: int = 5099):
        self.bind_ip = bind_ip
        self.bind_port = bind_port
        self.is_running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._socket.bind((self.bind_ip, self.bind_port))
            self._socket.settimeout(0.2)
            self._thread = threading.Thread(target=self._loop, name="probe-echo-responder", daemon=True)
            self._thread.start()
            logger.info(f"Probe echo responder listening on {self.bind_ip}:{self.bind_port}")
        except Exception as e:
            logger.error(f"Failed to bind probe echo responder on port {self.bind_port}: {e}")
            self.is_running = False

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                data, addr = self._socket.recvfrom(256)
                if len(data) >= PROBE_SIZE and data[:2] == PROBE_MAGIC:
                    # Echo packet straight back
                    self._socket.sendto(data, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.debug(f"Probe responder error: {e}")

    def stop(self) -> None:
        if not self.is_running:
            return
        self._stop_event.set()
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
        self.is_running = False
        logger.info("Probe echo responder stopped.")


class ActiveProbe:
    """
    Active probe sender & receiver to measure channel RTT, packet loss, and jitter.
    Works by sending periodic lightweight UDP probe packets to an echo responder or receiver.
    """

    def __init__(self, target_ip: str, target_port: int, probe_interval_ms: float = 50.0):
        self.target_ip = target_ip
        self.target_port = target_port
        self.probe_interval_ms = probe_interval_ms
        self.is_running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None

        # Stats
        self._sent_seq = 0
        self._recv_seq = 0
        self._rtts: List[float] = []
        self._window_rtts: List[float] = []
        self._last_transit: Optional[float] = None
        self._jitter: float = 0.0
        self._lock = threading.Lock()

    def start(self, local_port: int = 0) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("0.0.0.0", local_port))
        self._socket.settimeout(0.1)

        self._rx_thread = threading.Thread(target=self._rx_loop, name="probe-rx", daemon=True)
        self._rx_thread.start()

        self._thread = threading.Thread(target=self._tx_loop, name="probe-tx", daemon=True)
        self._thread.start()
        logger.info(f"Active probe started -> {self.target_ip}:{self.target_port} (interval {self.probe_interval_ms}ms)")

    def _tx_loop(self) -> None:
        interval_sec = self.probe_interval_ms / 1000.0
        while not self._stop_event.is_set():
            with self._lock:
                self._sent_seq += 1
                seq = self._sent_seq

            data = struct.pack(PROBE_FORMAT, PROBE_MAGIC, seq, time.time())
            try:
                self._socket.sendto(data, (self.target_ip, self.target_port))
            except Exception as e:
                logger.debug(f"Probe send error: {e}")

            time.sleep(interval_sec)

    def _rx_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                data, addr = self._socket.recvfrom(256)
                if len(data) >= PROBE_SIZE:
                    magic, seq, send_time = struct.unpack_from(PROBE_FORMAT, data, 0)
                    if magic == PROBE_MAGIC:
                        recv_time = time.time()
                        rtt_ms = (recv_time - send_time) * 1000.0
                        with self._lock:
                            self._recv_seq += 1
                            self._rtts.append(rtt_ms)
                            self._window_rtts.append(rtt_ms)
                            if self._last_transit is not None:
                                self._jitter = compute_rfc3550_jitter(
                                    self._jitter, self._last_transit, rtt_ms
                                )
                            self._last_transit = rtt_ms
            except socket.timeout:
                continue
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.debug(f"Probe rx error: {e}")

    def collect_and_reset_window(self) -> FlowMetrics:
        """Return metrics over the last sampling window and clear window buffer."""
        with self._lock:
            sent = self._sent_seq
            recv = self._recv_seq
            rtts = list(self._window_rtts)
            self._window_rtts.clear()
            jitter = self._jitter

        if rtts:
            avg_rtt = sum(rtts) / len(rtts)
            min_rtt = min(rtts)
            max_rtt = max(rtts)
            sorted_rtts = sorted(rtts)
            p95_rtt = sorted_rtts[int(len(sorted_rtts) * 0.95)]
        else:
            avg_rtt = min_rtt = max_rtt = p95_rtt = 0.0

        loss_pct = 0.0
        if sent > 0:
            loss_pct = max(0.0, ((sent - recv) / sent) * 100.0)

        return FlowMetrics(
            flow_id="active_probe",
            traffic_type=TrafficType.PROBE,
            packets_sent=sent,
            packets_recv=recv,
            latency_ms=round(avg_rtt, 2),
            min_latency_ms=round(min_rtt, 2),
            max_latency_ms=round(max_rtt, 2),
            p95_latency_ms=round(p95_rtt, 2),
            jitter_ms=round(jitter, 2),
            packet_loss_pct=round(loss_pct, 2)
        )

    def stop(self) -> None:
        if not self.is_running:
            return
        self._stop_event.set()
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
        self.is_running = False
        logger.info("Active probe stopped.")
