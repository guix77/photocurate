import asyncio
import json
from pathlib import Path

import httpx
import pytest

from photocurate.report import describe_run, markdown, spearman, write_reports
from photocurate.runs import create_run, execute_run
from photocurate.sets import create_set, extend_set

from .test_provider import completion


def test_interrupted_run_resumes_only_missing_results(store, indexed, config):
    create_set(store, "sample", indexed, 3, 42)
    run_id = create_run(store, "sample", config)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion('{"album_score": 80, "reason": "x"}'))

    def interrupted(_):
        raise RuntimeError("Simulated process interruption after recording first result")

    with pytest.raises(RuntimeError, match="interruption"):
        asyncio.run(
            execute_run(store, run_id, "fake-key", interrupted, httpx.MockTransport(handler))
        )
    assert len(calls) == 1
    assert store.get_run(run_id)["status"] == "interrupted"
    result = asyncio.run(
        execute_run(store, run_id, "fake-key", lambda _: None, httpx.MockTransport(handler))
    )
    assert len(calls) == 3 and result["status"] == "completed"
    asyncio.run(
        execute_run(store, run_id, "fake-key", lambda _: None, httpx.MockTransport(handler))
    )
    assert len(calls) == 3
    assert "fake-key" not in store.get_run(run_id)["config_json"]


def test_concurrency_and_failures_do_not_overwrite_human_labels(store, indexed, config):
    create_set(store, "sample", indexed, 6, 42)
    evaluation = store.get_set("sample")
    store.set_label(evaluation["id"], 1, "0", "Guix")
    config["concurrency"] = 3
    run_id = create_run(store, "sample", config)
    active, peak, calls = 0, 0, 0

    async def handler(request):
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=completion('{"album_score": "bad", "reason": "x"}'))

    result = asyncio.run(
        execute_run(store, run_id, "fake-key", lambda _: None, httpx.MockTransport(handler))
    )
    assert result["errors"] == 6 and peak == 3
    assert store.members(evaluation["id"])[0]["label"] == "0"
    asyncio.run(
        execute_run(store, run_id, "fake-key", lambda _: None, httpx.MockTransport(handler))
    )
    assert calls == 6  # Failed results are immutable too; a new experiment is required to retry.


def test_report_metrics_and_comparison_membership(store, indexed, config, tmp_path):
    create_set(store, "sample", indexed, 5, 42)
    evaluation = store.get_set("sample")
    for i, label in enumerate(("0", "3", "2", "?", "3"), 1):
        store.set_label(evaluation["id"], i, label, "Guix")
    run_id = create_run(store, "sample", config)
    contents = iter(
        (
            '{"album_score": 90, "reason": "bad high"}',
            '{"album_score": 10, "reason": "strong low"}',
            '{"album_score": 80, "reason": "good"}',
            '{"album_score": 95, "reason": "uncertain"}',
            '{"album_score": "invalid", "reason": "error"}',
        )
    )
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=completion(next(contents))))
    asyncio.run(execute_run(store, run_id, "fake-key", lambda _: None, transport))
    report = describe_run(store, run_id, [3])
    assert report["quality"]["paired_images"] == 3
    assert report["quality"]["spearman"] == pytest.approx(-1)
    top = report["quality"]["top_k"][0]
    assert top["selected"] == 3 and top["numerically_labelled"] == 2
    assert top["precision_human_2_or_3"] == 0.5
    assert top["human_3_total"] == 2 and top["recall_human_3"] == 0
    assert report["operations"]["cost_total_usd"] == 0.005
    assert report["operations"]["cost_per_1000_images_usd"] == 1
    assert report["operations"]["validation_failure_rate"] == 0.2
    assert report["quality"]["human_0_highest"][0]["position"] == 1
    assert report["quality"]["human_3_lowest"][0]["position"] == 2
    files = write_reports(store, [run_id], [3], tmp_path / "reports")
    assert all(Path(path).is_file() for path in files.values())
    payload = json.loads(Path(files["json"]).read_text())
    assert payload["runs"][0]["images"][3]["label"] == "?"
    extend_set(store, "sample", 6)
    expanded_run = create_run(store, "sample", config)
    text = markdown([describe_run(store, run_id, [3]), describe_run(store, expanded_run, [3])])
    assert "different" in text and "same enlarged set" in text
    assert describe_run(store, run_id, [3])["run_size"] == 5


def test_partial_costs_remain_unknown_instead_of_being_reported_as_zero(store, indexed, config):
    create_set(store, "sample", indexed, 2, 42)
    run_id = create_run(store, "sample", config)
    replies = iter(
        (
            httpx.Response(200, json=completion('{"album_score": 80, "reason": "x"}')),
            httpx.Response(503, json={"error": {"message": "Unavailable"}}),
        )
    )
    asyncio.run(
        execute_run(
            store, run_id, "fake-key", lambda _: None, httpx.MockTransport(lambda _: next(replies))
        )
    )
    metrics = describe_run(store, run_id, [1])["operations"]
    assert metrics["cost_known_usd"] == 0.001
    assert metrics["cost_total_usd"] is None and metrics["cost_per_1000_images_usd"] is None
    assert metrics["cost_coverage_images"] == 1 and metrics["provider_failure_rate"] == 0.5


def test_ties_and_degenerate_spearman():
    assert spearman([0, 1, 1, 3], [10, 20, 20, 30]) == pytest.approx(1)
    assert spearman([0, 1, 1, 3], [30, 20, 20, 10]) == pytest.approx(-1)
    assert spearman([1, 1], [10, 20]) is None
    assert spearman([], []) is None


def test_deleted_original_can_still_be_evaluated_from_frozen_proxy(store, indexed, config):
    create_set(store, "sample", indexed, 1, 42)
    run_id = create_run(store, "sample", config)
    Path(store.members(store.get_set("sample")["id"])[0]["source_path"]).unlink()
    result = asyncio.run(
        execute_run(
            store,
            run_id,
            "fake-key",
            lambda _: None,
            httpx.MockTransport(
                lambda _: httpx.Response(200, json=completion('{"album_score": 80, "reason": "x"}'))
            ),
        )
    )
    assert result["status"] == "completed"


def test_resume_finalizes_run_interrupted_after_last_result_without_another_call(
    store,
    indexed,
    config,
):
    create_set(store, "sample", indexed, 1, 42)
    run_id = create_run(store, "sample", config)

    def interrupted(_):
        raise RuntimeError("Interrupted after last commit")

    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json=completion('{"album_score": 80, "reason": "x"}'))
    )
    with pytest.raises(RuntimeError):
        asyncio.run(execute_run(store, run_id, "fake-key", interrupted, transport))
    assert store.get_run(run_id)["status"] == "interrupted"

    def never_called(_):
        raise AssertionError("No results missing; must not send another request")

    result = asyncio.run(
        execute_run(store, run_id, "fake-key", lambda _: None, httpx.MockTransport(never_called))
    )
    assert result["status"] == "completed" and result["images"] == 1


@pytest.mark.parametrize("http_status", [401, 402, 404])
def test_global_provider_failure_stops_requests_and_surfaces_error(
    store,
    indexed,
    config,
    http_status,
):
    create_set(store, "sample", indexed, 4, 42)
    run_id = create_run(store, "sample", config)
    calls = []
    progress = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            http_status,
            json={
                "error": {
                    "code": http_status,
                    "message": "No usable endpoint for this configuration",
                }
            },
        )

    result = asyncio.run(
        execute_run(store, run_id, "fake-key", progress.append, httpx.MockTransport(handler))
    )
    assert len(calls) == 1
    assert result == {"run_id": run_id, "status": "failed", "images": 4, "errors": 1, "pending": 3}
    assert store.get_run(run_id)["status"] == "failed"
    assert any(f"HTTP {http_status}" in line for line in progress)
    assert any("Stopped:" in line for line in progress)
    report = describe_run(store, run_id, [1])
    assert report["operations"]["attempted"] == 1
    assert report["operations"]["pending"] == 3
