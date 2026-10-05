# Adaptive QoS Engine for Mixed Home Broadband Traffic

> **Phase 1 Implementation: Network Simulation Laboratory, Traffic Generation & Monitoring Engine**

---

## 1. Project Overview & Problem Statement

In mixed home broadband environments, multiple concurrent applications compete for limited WAN uplink/downlink bandwidth:
- **Gaming & Interactive Applications**: Require low latency (<30ms) and near-zero jitter.
- **Video Conferencing (Zoom/WebRTC)**: Requires steady throughput and bounded latency without frame drops.
- **Video Streaming (YouTube/Netflix DASH/HLS)**: Bursty, buffer-filling high throughput.
- **Bulk Downloads / OS Updates**: Continuous saturating TCP flows.

Without intelligent QoS, unmanaged FIFO bottleneck queues lead to **bufferbloat**: bulk flows fill router buffers, causing massive latency spikes, jitter, and packet loss for real-time interactive traffic.

**Phase 1 Goal**: Build the foundational Linux-based network simulation laboratory, multi-class synthetic traffic generators, real-time passive & active traffic monitoring engine, and reproducible baseline congestion experiments.

---

## 2. Architecture & Design

```
+-----------------------------------------------------------------------------------------+
|                                    NETWORK TOPOLOGY                                     |
|                                                                                         |
|  [LAN Client (10.0.1.2)]          [QoS Gateway Router]            [WAN Server (10.0.2.2)]|
|   +--------------------+          +--------------------+          +--------------------+|
|   | Gaming Generator   |  veth    | IP Forwarding: ON  |  veth    | Video Call Receiver|
|   | Video Call Gen     |--------->| LAN: 10.0.1.1/24   |--------->| Gaming Echo/Recv   |
|   | Video Stream Gen   | (veth0)  | WAN: 10.0.2.1/24   | (veth1)  | Bulk Sink          |
|   | Bulk TCP/UDP Gen   |          | tc / netem Shaper  |          | Active Echo Probe  |
|   +--------------------+          +--------------------+          +--------------------+|
+---------------------------------------------|-------------------------------------------+
                                              |
                                              v
                             +----------------------------------+
                             |   TRAFFIC MONITORING ENGINE      |
                             |  - Throughput (bits/sec)         |
                             |  - Latency & RTT (min/avg/p95)   |
                             |  - Jitter (RFC 3550 standard)    |
                             |  - Packet Loss % (Seq tracking)  |
                             |  - Queue Backlog & TC Drops      |
                             +----------------------------------+
                                              |
                                              v
                             +----------------------------------+
                             |   STRUCTURED METRICS STORAGE     |
                             |  - results/experiment_result.json|
                             |  - results/metrics_timeseries.csv|
                             |  - results/experiment_summary.md |
                             +----------------------------------+
```

### Key Components:
1. **Network Topology (`adaptive_qos.topology`)**:
   - **Linux Netns Topology (`linux_netns.py`)**: Isolated Linux network namespaces (`lan_client`, `qos_gw`, `wan_server`) interconnected via `veth` pairs with Linux `tc` (TBF + netem) rate shaping.
   - **Simulated Topology (`simulation.py`)**: Cross-platform bottleneck link queue simulation for CI/testing without root privileges.
2. **Traffic Generators (`adaptive_qos.traffic`)**:
   - **Gaming (`gaming.py`)**: High-frequency (60-128 Hz), small payload (96-128 bytes) UDP packets.
   - **Video Call (`video_call.py`)**: WebRTC/RTP style interactive stream with frame bursts (30 fps, ~2.5 Mbps).
   - **Video Streaming (`video_streaming.py`)**: DASH/HLS chunked burst downloads (1.5 MB chunks every 2.5s).
   - **Bulk Download (`bulk_download.py`)**: Saturating continuous data push.
   - **Wire Protocol (`base.py`)**: 22-byte binary measurement header (Magic, Hash, Sequence No, Timestamp).
3. **Monitoring Engine (`adaptive_qos.monitoring`)**:
   - **Passive Monitor (`passive.py`)**: Real-time non-intrusive stream tracking (throughput, sequence loss, delay, RFC 3550 jitter).
   - **Active Probe (`probes.py`)**: High-precision RTT and jitter probing.
   - **Queue Monitor (`queue_monitor.py`)**: Queries Linux `tc -s qdisc` for backlog bytes, packets, and drops.
   - **Storage (`storage.py`)**: Generates structured JSON, CSV timeseries, and Markdown summaries.
4. **Experiment Orchestrator (`adaptive_qos.experiments`)**:
   - Executes multi-phase baseline experiments (100 Mbps -> 20 Mbps congestion -> recovery) and logs timestamped snapshots.

---

## 3. Privacy & Architectural Constraints

- **No Traffic Decryption**: Zero payload inspection or decryption.
- **No Payload Snooping**: Measurements use non-invasive wire headers and network metadata only.
- **No LLM in the Data Path**: Pure deterministic networking.
- **No QoS Prioritization in Phase 1**: Establishes unmanaged FIFO baseline to quantify bufferbloat.

---

## 4. Setup & Installation

### Requirements
- Python 3.8+
- Linux (Ubuntu/Debian) with `iproute2` and root privileges for Linux netns mode (or Windows/macOS for simulated mode)

### Installation
```bash
# Clone the repository
git clone https://github.com/Dhanush0058/Adaptive-Qas.git
cd Adaptive-Qas

# Install package in editable mode
pip install -e .
pip install -r requirements.txt
```

---

## 5. Running the Baseline Experiment

### Option A: Cross-Platform / Simulated Mode (Windows, macOS, Linux without root)
```bash
python run_baseline.py --mode simulated --duration 20.0 --congestion-start 6.0 --congestion-end 15.0
```

### Option B: Linux Network Namespaces Mode (Native Linux with root)
```bash
# Automated single-command runner
sudo ./scripts/run_baseline.sh linux 20.0

# Or directly with Python
sudo python3 run_baseline.py --mode linux --duration 20.0
```

---

## 6. Running Tests

Run the complete test suite:
```bash
python -m pytest -v
```

---

## 7. Expected Output

When running the baseline experiment, you will observe the real-time timeslice log showing traffic metrics before and during WAN throttling:

```text
2026-10-05 09:04:47 [INFO] [experiment.runner] === Starting Experiment: Phase 1 Baseline Congestion Experiment ===
2026-10-05 09:04:47 [INFO] [experiment.runner] Duration: 12.0s | Congestion: 4.0s - 9.0s
2026-10-05 09:04:48 [INFO] [experiment.runner] [ 1.0s | NORMAL (100.0M)] Flows: flow_gaming: 0.1M / 0.2ms, flow_videocall: 2.4M / 0.1ms, flow_streaming: 8.0M / 0.2ms, flow_bulk: 16.3M / 0.2ms
2026-10-05 09:04:51 [WARNING] [experiment.runner] --- [CONGESTION INJECTED] Throttling WAN link to 20.0 Mbps ---
2026-10-05 09:04:52 [INFO] [experiment.runner] [ 5.0s | CONGESTED (20.0M)] Flows: flow_gaming: 0.1M / 45.2ms, flow_videocall: 2.3M / 42.1ms, flow_streaming: 0.2M / 48.0ms, flow_bulk: 16.1M / 50.3ms
2026-10-05 09:04:56 [INFO] [experiment.runner] --- [CONGESTION CLEARED] Restoring WAN link to 100.0 Mbps ---
2026-10-05 09:04:59 [INFO] [monitoring.storage] Experiment results saved to results/baseline_phase1_1791171287

============================================================
PHASE 1 BASELINE EXPERIMENT SUMMARY REPORT
============================================================
Artifacts saved in: results/baseline_phase1_1791171287

Flow ID            | Type             | Uncongested Latency  | Congested Latency   
--------------------------------------------------------------------------------
flow_gaming        | gaming           |   0.20 ms             |  45.20 ms
flow_videocall     | video_call       |   0.11 ms             |  42.10 ms
flow_streaming     | video_streaming  |   0.18 ms             |  48.00 ms
flow_bulk          | bulk_download    |   0.15 ms             |  50.30 ms
active_probe       | probe            |   0.00 ms             |  44.50 ms
================================================================================
```

### Stored Artifacts:
- `results/<experiment_id>/experiment_result.json`: Full configuration, snapshots, and metrics.
- `results/<experiment_id>/metrics_timeseries.csv`: Time-series data ready for charting and dashboard integration.
- `results/<experiment_id>/experiment_summary.md`: Markdown comparison table and analysis.
