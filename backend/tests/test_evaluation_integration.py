import json
from unittest.mock import patch

import pytest

from app.db.falkordb import GraphDatabase
from app.demo.evaluation import load_document, prepare, save_document
from app.demo.hackathon import run

pytestmark = pytest.mark.integration


def test_five_demos_real_backend_restart_and_measured_ablation(isolated_graph_settings, tmp_path):
    output = tmp_path / "evaluation"
    # Parent orchestration may never construct an AWS SDK session. Child backend
    # independently receives AWS_ENABLED=false, mock provider, and an empty LLM key.
    with patch("boto3.Session", side_effect=AssertionError("AWS must not be contacted")):
        result = run(isolated_graph_settings, output, repetitions=5, progress=lambda _: None)
    assert result["verified"]
    assert [t["path_count"] for t in result["demo1"]["timeline"]] == [0, 0, 0, 1]
    assert result["demo2_and_3"]["completed_runs_preserved"] == 3
    assert result["demo2_and_3"]["internal_handoffs_consumed"] == 5
    assert result["demo2_and_3"]["unique_task_count"] == 7
    assert result["backend_process_restart"]["startup_recovered_without_resume_request"]
    assert result["backend_process_restart"]["first_listener_closed"]
    assert (
        result["backend_process_restart"]["first_pid"]
        != result["backend_process_restart"]["second_pid"]
    )
    assert result["demo4"]["verification_required"]
    assert result["demo4"]["definitive_fact_count"] == 0
    assert len(result["demo4"]["observations"]) == 2
    observations = result["demo4"]["observations"]
    assert (
        observations[0]["provenance"]["observed_at"] != observations[1]["provenance"]["observed_at"]
    )
    assert not result["demo5"]["graph_mutated"]
    assert result["demo5"]["before_path_count"] == 1
    assert result["demo5"]["after_path_count"] == 0
    assert (
        result["demo5"]["source_graph_sha256_before"]
        == result["demo5"]["source_graph_sha256_after"]
    )
    assert result["ablation"]["after_restart_count"] == 0
    assert result["ablation"]["persistent_after_restart_count"] == 1
    assert not result["ablation"]["graph_reproduced"]
    for measurement in result["graph_query_latency"]["operations"].values():
        assert measurement["operation_latency"]["samples"] == 5
        assert measurement["graph_query_latency"]["samples"] >= 5
        assert measurement["graph_query_latency"]["minimum_ms"] > 0
    assert json.loads((output / "evaluation.json").read_text()) == result
    detail = json.loads((output / "sample-investigation-detail.json").read_text())
    assert detail["status"] == "AWAITING_REVIEW"
    # A completely new connection reconstructs the report from graph evidence,
    # even after exported reports are gone. Files are not recovery checkpoints.
    (output / "evaluation.json").unlink()
    fresh = GraphDatabase(isolated_graph_settings)
    try:
        assert load_document(fresh, "result") == result
        with pytest.raises(RuntimeError, match="fresh isolated"):
            prepare(fresh, isolated_graph_settings)
    finally:
        fresh.close()


def test_evaluation_documents_are_graph_persistent_and_bounded(
    live_database, isolated_graph_settings
):
    payload = {"synthetic": True, "observations": ["observation/" + str(i) for i in range(2000)]}
    save_document(live_database, "bounded-document", payload)
    fresh = GraphDatabase(isolated_graph_settings)
    try:
        assert load_document(fresh, "bounded-document") == payload
    finally:
        fresh.close()
