import json
import socket
import subprocess
import sys

import pytest

from app.demo.evaluation import latency_summary
from app.demo.hackathon import (
    check_graph_unavailable,
    child_environment,
    run,
    wait_for_listener_exit,
)


def test_nearest_rank_latency_summary_uses_measured_samples():
    result = latency_summary([4.0, 1.0, 2.0, 3.0, 5.0])
    assert result["samples"] == 5
    assert result["median_ms"] == 3
    assert result["p95_ms"] == result["maximum_ms"] == 5
    assert result["minimum_ms"] == 1
    with pytest.raises(RuntimeError, match="missing latency"):
        latency_summary([])


def test_listener_exit_waits_for_real_socket_ownership_to_end(monkeypatch):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        pauses = []

        def release_listener(_):
            pauses.append(True)
            listener.close()

        monkeypatch.setattr("app.demo.hackathon.sleep", release_listener)
        wait_for_listener_exit(port)
        assert pauses == [True]  # A live listener must not have passed the exit check.


def test_ephemeral_baseline_restarts_without_any_persistent_store(unit_settings):
    payload = {"write": {"subject_ids": ["asset"]}, "query": {"subject_ids": ["asset"]}}
    counts = []
    for mode, data in (
        ("write-and-read", payload),
        ("read", {"query": payload["query"]}),
    ):
        result = subprocess.run(  # noqa: S603 -- fixed module; fixture via stdin
            [sys.executable, "-m", "app.demo.ephemeral_baseline", mode],
            input=json.dumps(data),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
            env=child_environment(unit_settings, False),
        )
        output = json.loads(result.stdout)
        counts.append(output["count"])
        assert not output["persistent_storage"]
    assert counts == [1, 0]


def test_graph_off_core_operations_fail_clearly(unit_settings):
    fixture = {
        "space_id": "00000000-0000-0000-0000-000000000001",
        "reader_agent_id": "00000000-0000-0000-0000-000000000002",
        "input": {"query": {"subject_ids": ["00000000-0000-0000-0000-000000000003"]}},
    }
    results = check_graph_unavailable(unit_settings, fixture)
    assert len(results) == 4
    assert {r["error_code"] for r in results} == {"dependency_unavailable"}


def test_runner_refuses_non_isolated_graph_without_connecting(unit_settings, tmp_path):
    with pytest.raises(RuntimeError, match="isolated graph"):
        run(unit_settings.model_copy(update={"falkordb_graph": "shadowchain"}), tmp_path / "demo")


def test_child_configuration_keeps_cloud_disabled_and_clears_model_key(unit_settings, monkeypatch):
    monkeypatch.setenv("AWS_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-placeholder")
    environment = child_environment(unit_settings, True)
    assert environment["AWS_ENABLED"] == environment["CLOUDTRAIL_ENABLED"] == "false"
    assert environment["AGENT_PROVIDER"] == "mock"
    assert environment["GEMINI_API_KEY"] == ""
    assert environment["WORKFLOW_RECOVERY_ENABLED"] == "true"
