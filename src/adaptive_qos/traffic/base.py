"""Base abstractions and wire protocol headers for traffic generation."""

import abc
import struct
import time
from typing import Optional, Tuple
from adaptive_qos.common.models import FlowConfig, FlowMetrics, TrafficType

# Compact binary header for active measurement without payload inspection:
# - magic: 2 bytes (0xAQ)
# - flow_id_hash: 4 bytes uint32
# - seq_num: 8 bytes uint64
# - timestamp_sec: 8 bytes double (float64)
# Header format: '!2sIQd' (22 bytes)
HEADER_FORMAT = "!2sIQd"
HEADER_MAGIC = b"AQ"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def pack_packet(flow_id_hash: int, seq_num: int, timestamp: float, payload_size: int) -> bytes:
    """Pack measurement header and pad to desired packet size."""
    hdr = struct.pack(HEADER_FORMAT, HEADER_MAGIC, flow_id_hash, seq_num, timestamp)
    pad_len = max(0, payload_size - len(hdr))
    return hdr + (b"\x00" * pad_len)


def unpack_packet(data: bytes) -> Optional[Tuple[int, int, float, int]]:
    """Unpack header returning (flow_id_hash, seq_num, timestamp, total_len) or None if invalid."""
    if len(data) < HEADER_SIZE:
        return None
    magic, flow_id_hash, seq_num, timestamp = struct.unpack_from(HEADER_FORMAT, data, 0)
    if magic != HEADER_MAGIC:
        return None
    return flow_id_hash, seq_num, timestamp, len(data)


def compute_rfc3550_jitter(old_jitter: float, prev_transit: float, current_transit: float) -> float:
    """
    Compute interarrival jitter according to RFC 3550:
    D(i, j) = (R_j - S_j) - (R_i - S_i)
    J(i) = J(i-1) + (|D(i-1, i)| - J(i-1)) / 16
    """
    d = abs(current_transit - prev_transit)
    return old_jitter + (d - old_jitter) / 16.0


class BaseTrafficGenerator(abc.ABC):
    """Abstract base class for all synthetic traffic generators."""

    def __init__(self, config: FlowConfig):
        self.config = config
        self.is_running = False
        self.packets_sent = 0
        self.bytes_sent = 0
        self.start_time: Optional[float] = None
        self.stop_time: Optional[float] = None

    @abc.abstractmethod
    def start(self) -> None:
        """Start transmitting traffic."""
        pass

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop transmitting traffic."""
        pass

    @abc.abstractmethod
    def get_stats(self) -> FlowMetrics:
        """Retrieve current sender-side statistics."""
        pass
