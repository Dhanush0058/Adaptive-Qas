# Adaptive QoS Engine for Mixed Home Broadband Traffic

> **Phase 1 & Phase 2: Network Simulation Laboratory, Traffic Monitoring, Non-Payload Traffic Classifier & Dynamic Link-Capacity Estimator**

---

## 1. Project Overview & Problem Statement

In mixed home broadband environments, multiple concurrent applications compete for limited WAN bandwidth:
- **Gaming & Interactive Applications**: Require low latency (<30ms) and near-zero jitter.
- **Video Conferencing (Zoom/WebRTC)**: Requires steady throughput and bounded latency without packet loss.
- **Video Streaming (YouTube/Netflix DASH/HLS)**: Bursty, chunk-based high throughput.
- **Bulk Downloads / OS Updates**: Continuous saturating bulk flows.

Without QoS, unmanaged FIFO bottleneck queues suffer from **bufferbloat**: bulk flows saturate the router's queue, resulting in severe latency spikes, jitter, and packet loss for real-time interactive traffic.

---

## 2. End-to-End System Architecture

```
                                    REAL / SIMULATED NETWORK TRAFFIC
                                                   |
                                                   v
                                 +------------------------------------+
                                 |    PHASE 1: MONITORING ENGINE      |
                                 |  - Passive per-flow socket tracker |
                                 |  - Active RTT & Jitter probes      |
                                 |  - Linux TC queue backlog & drops  |
                                 +------------------------------------+
                                                   |
                                                   v
                                 +------------------------------------+
                                 |    FEATURE EXTRACTION (No Deep)    |
                                 |  - Packet rate (pps)               |
                                 |  - Byte rate & throughput (Mbps)   |
                                 |  - Avg packet size (bytes)         |
                                 |  - Burstiness ratio & duty cycle   |
                                 |  - Latency & jitter dynamics       |
                                 +------------------------------------+
                                                   |
                        +--------------------------+--------------------------+
                        |                                                     |
                        v                                                     v
      +-----------------------------------+                 +-----------------------------------+
      |    PHASE 2: TRAFFIC CLASSIFIER    |                 |  PHASE 2: LINK CAPACITY ESTIMATOR |
      |  - Gaming                         |                 |  - Saturation Detection           |
      |  - Video Call                     |                 |  - Adaptive EWMA Smoothing        |
      |  - Video Streaming                |                 |  - Capacity Transition Detection  |
      |  - Bulk Download                  |                 |  - Configured vs Estimated Cap.   |
      |  - Interactive / Real-time        |                 |  - Physical Evidence Confidence   |
      |  - Dynamic Confidence Scoring     |                 |  - Data Source: linux / sim       |
      +-----------------------------------+                 +-----------------------------------+
                        |                                                     |
                        +--------------------------+--------------------------+
                                                   |
                                                   v
                                 +------------------------------------+
                                 |      STRUCTURED OUTPUT ARTIFACTS   |
                                 |  - results/phase2/                 |
                                 |    * classification_results.json   |
                                 |    * capacity_estimation.json      |
                                 |    * classification_timeseries.csv |
                                 |    * phase2_summary.md             |
                                 +------------------------------------+
                                                   |
                                                   v
                                  [PHASE 3 ADAPTIVE QOS DECISION ENGINE]
```

---

## 3. Phase 2 Component Details

### A. Non-Payload Traffic Classifier (`src/adaptive_qos/classification/`)
- **Zero Payload Inspection**: Strict privacy preservation—does NOT inspect, decrypt, or read private application payloads.
- **Observable Features Used**:
  - `packet_rate_pps`: Windowed packet rate (packets per second).
  - `throughput_mbps`: Aggregate byte rate.
  - `avg_packet_size_bytes`: Derived packet size ($\frac{\Delta \text{bytes}}{\Delta \text{packets}}$).
  - `burstiness_ratio`: Peak-to-average throughput ratio over sliding window.
  - `duty_cycle`: Ratio of active transmission windows to idle windows.
  - `protocol`: Transport protocol (UDP/TCP).
- **Classification Categories**:
  1. `gaming`: Small packets (~64–200B), steady 60–128 Hz packet rate, low volume (<0.5 Mbps), continuous duty cycle.
  2. `video_call`: Medium-large packets (~800–1300B), moderate steady throughput (1.5–3.5 Mbps), 25–60 fps bursts.
  3. `video_streaming`: MTU-size packets (1400–1500B), high burstiness ratio (>1.5), periodic chunk downloads followed by idle periods.
  4. `bulk_download`: MTU-size packets, continuous link-saturating throughput (>12 Mbps), low burstiness, duty cycle ~1.0.
  5. `interactive`: Generic lightweight interactive traffic (control channels, DNS, SSH).
  6. `background_unknown`: Ambiguous or unclassified flows with low match scores.
- **Dynamic Confidence Calculation**: Computed dynamically from feature distance kernels and margin over competing classes:
  $$\text{Confidence} = S_{\text{winner}} \times \left(0.70 + 0.30 \cdot \min\left(1.0, \frac{S_{\text{winner}} - S_{\text{runner\_up}}}{S_{\text{winner}}}\right)\right)$$

---

### B. Link-Capacity Estimator (`src/adaptive_qos/estimation/`)
- **Configured vs. Estimated Distinction**: Explicitly separates configured nominal values from physically observed bottleneck capacity.
- **Physical Saturation Tracking**: Observes router queue backlog (`backlog_bytes > 1500`), queue drop events (`dropped_packets > 0`), and transit latency elevation ($\Delta \text{RTT} > 15\text{ms}$).
- **Adaptive EWMA Smoothing**:
  $$\hat{C}_t = \alpha \cdot T_{\text{window}} + (1 - \alpha) \cdot \hat{C}_{t-1}$$
  Adapts $\alpha = 0.75$ during rapid bottleneck transitions (e.g. 100 Mbps $\rightarrow$ 20 Mbps drop) and $\alpha = 0.40$ in steady state.
- **Confidence Scoring**: High confidence ($0.85–0.96$) when physical queue pressure or packet drops confirm bottleneck saturation; moderate/low confidence ($0.40–0.60$) when the link is idle/lightly loaded.

---

### C. Real vs. Simulated Data Source Labeling
Every output record explicitly includes a `"data_source"` field:
- `"data_source": "linux_real"` (when measuring Linux kernel network namespaces, veth interfaces, and `tc` qdiscs).
- `"data_source": "simulation"` (when running cross-platform simulated bottleneck links).

---

## 4. Setup & Running Instructions

### Setup & Requirements
```bash
# Install package and dependencies
pip install -e .
pip install -r requirements.txt
```

### Running Phase 2 on Linux / WSL2 (Real Kernel Data)
```bash
# Automated Linux runner (requires sudo)
sudo ./scripts/run_phase2.sh linux 20.0

# Or via Python directly
sudo python3 run_phase2.py --mode linux --duration 20.0
```

### Running Phase 2 in Simulation Mode (Cross-Platform / Windows)
```bash
python run_phase2.py --mode simulated --duration 20.0
```

### Processing Pre-recorded Experiment Data
```bash
python run_phase2.py --input-json results/baseline_phase1_<timestamp>/experiment_result.json
```

---

## 5. Running the Complete Test Suite

Run all 21 unit & integration tests across Phase 1 and Phase 2:
```bash
python -m pytest -v
```

---

## 6. Expected Output & Sample Results

```text
==========================================================================================
PHASE 2: TRAFFIC CLASSIFICATION & LINK CAPACITY ESTIMATION REPORT
Data Source Tag: [SIMULATION] | Artifacts: results/phase2
==========================================================================================

[A] FLOW CLASSIFICATION RESULTS:
Flow ID            | Expected Type    | Predicted Type     | Confidence | Data Source 
------------------------------------------------------------------------------------------
flow_gaming        | gaming           | gaming           [OK] |   0.74     | simulation  
flow_videocall     | video_call       | video_call       [OK] |   0.67     | simulation  
flow_streaming     | video_streaming  | video_streaming  [OK] |   0.70     | simulation  
flow_bulk          | bulk_download    | bulk_download    [OK] |   0.74     | simulation  

[B] LINK-CAPACITY ESTIMATION RESULTS:
Phase            | Configured Capacity    | Estimated Capacity   | Data Source 
------------------------------------------------------------------------------------------
Uncongested      | 100.0 Mbps             |  61.5 Mbps (conf 0.60)  | simulation  
Congested        | 20.0 Mbps              |  27.3 Mbps (conf 0.92)  | simulation  
==========================================================================================
```

### Stored Phase 2 Artifacts:
- `results/phase2/classification_results.json`: Detailed classification results for all flows across all time steps.
- `results/phase2/capacity_estimation.json`: Time-series of capacity estimates, state transitions, and confidence.
- `results/phase2/classification_timeseries.csv`: Tabular time-series formatted for pandas and dashboards.
- `results/phase2/phase2_summary.md`: Formatted Markdown report comparing expected vs predicted classes and configured vs estimated capacities.

---

## 7. Scope & Phase 3 Boundaries

> [!IMPORTANT]
> **Phase 2 Scope Limitation**:
> - The classifier identifies **broad traffic behavioral patterns** using statistical flow metadata, not arbitrary real-world application signatures.
> - **Phase 3 Boundary**: This phase strictly stops at Classification and Capacity Estimation. No dynamic QoS decision policies, queue shaping rules, or bandwidth allocation algorithms are implemented yet.
