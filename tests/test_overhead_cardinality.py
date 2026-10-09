"""Overhead measurement plumbing and bounded label cardinality (WP-3.5, AOP-053)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def measure():
    path = ROOT / "scripts/measure_overhead.py"
    loader = importlib.machinery.SourceFileLoader("hop_measure_overhead", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_soak_cardinality_is_bounded_across_run_counts(measure):
    small = measure.soak_attributes(20)
    large = measure.soak_attributes(120)
    # Label KEYS are bounded: they do not grow with the number of runs.
    assert small["attribute_key_count"] == large["attribute_key_count"]
    assert set(small["attribute_keys"]) == set(large["attribute_keys"])
    # Per-run identifiers are values, never new label keys, and no metric label
    # projection exists in 1.0 (so nothing unbounded is exported as a label).
    assert "request_id" in large["attribute_keys"]
    assert "request-119" not in large["attribute_keys"]
    assert large["metric_labels_emitted"] == 0
    assert "request_id" in large["high_cardinality_keys"]


def test_soak_bounds_value_size_and_redacts_secrets(measure):
    soak = measure.soak_attributes(3)
    # A 5000-char payload is truncated and carries a digest instead of the raw text.
    assert "runtime.run_note_truncated" in soak["attribute_keys"]
    assert "runtime.run_note_artifact_digest" in soak["attribute_keys"]

    from hop.telemetry.observe import InferenceRecord
    from hop.telemetry.redaction import sanitize_attributes

    record = InferenceRecord(
        deployment_id="fixture",
        deployment_digest="sha256:" + "a" * 64,
        model_id="fixture",
        extra={"api_key": "sk-live-should-never-appear"},
    )
    attrs = sanitize_attributes(record.to_attributes())
    assert "sk-live-should-never-appear" not in str(attrs)
    assert "runtime.api_key_digest" in attrs


def test_measurements_return_samples_and_stats(measure):
    startup = measure.measure_process(2)
    for name in ("interpreter_reference", "instrumented_import", "instrumented_cli_help"):
        series = startup[name]
        assert len(series["samples_s"]) == 2
        assert series["cold_s"] == series["samples_s"][0]
        assert series["mean_s"] > 0
    trajectory = measure.measure_trajectory(2, events=50)
    assert len(trajectory["instrumented_read_page"]["samples_s"]) == 2
    assert len(trajectory["reference_raw_scan"]["samples_s"]) == 2
    assert trajectory["events"] == 50


def test_per_run_brokers_are_isolated(measure):
    import tempfile

    from hop.harnesses.broker import Broker
    from tests.test_identity import _deployment, _shards

    deployment = _deployment(_shards())
    with (
        tempfile.TemporaryDirectory(prefix="hop-broker-a-") as first_dir,
        tempfile.TemporaryDirectory(prefix="hop-broker-b-") as second_dir,
    ):
        first = Broker(deployment, "run-a", "att-a", first_dir, port=18111)
        second = Broker(deployment, "run-b", "att-b", second_dir, port=18112)
        # Per-run isolation of the inference input path: distinct sockets,
        # distinct per-attempt tokens, distinct audit trails. A worker cannot
        # reuse another run's handle, so evaluation jobs cannot fan out into
        # extra inference connections or starve interactive capacity.
        assert first.token and second.token and first.token != second.token
        assert first.socket_path != second.socket_path
        assert first.audit_path != second.audit_path
        for broker in (first, second):
            assert stat.S_IMODE(os.stat(broker.socket_path).st_mode) == 0o600
