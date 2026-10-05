"""Unit tests for the experiment runner orchestrator."""

import os
import tempfile
import pytest
from adaptive_qos.experiments.baseline import create_baseline_experiment_config
from adaptive_qos.experiments.runner import ExperimentRunner
from adaptive_qos.topology.simulation import SimulatedTopology


def test_baseline_experiment_short_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Short 2.5s run for unit test
        cfg = create_baseline_experiment_config(
            dst_ip="127.0.0.1",
            total_duration_sec=2.5,
            congestion_start_sec=1.0,
            congestion_end_sec=2.0,
        )
        cfg.sampling_interval_sec = 0.5
        topo = SimulatedTopology(cfg.topology)

        runner = ExperimentRunner(config=cfg, topology=topo, output_base_dir=tmpdir)
        result = runner.run()

        assert result is not None
        assert len(result.snapshots) >= 3
        assert "flow_gaming" in result.summary_by_flow
        assert "flow_bulk" in result.summary_by_flow
        assert os.path.exists(os.path.join(runner.output_dir, "experiment_result.json"))
        assert os.path.exists(os.path.join(runner.output_dir, "metrics_timeseries.csv"))
        assert os.path.exists(os.path.join(runner.output_dir, "experiment_summary.md"))
