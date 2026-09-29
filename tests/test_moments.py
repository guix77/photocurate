import asyncio
import base64
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from photocurate.cli import main
from photocurate.moment_viewer import annotation_server, public_state
from photocurate.moments import (
    annotation_groups,
    execute,
    experiment_dir,
    labels,
    load_manifest,
    paired_report,
    prepare,
    prepare_run,
    read_history,
    save_annotation,
    temporal_groups,
)
from photocurate.runs import create_run
from photocurate.sets import create_set


@pytest.fixture
def moment_experiment(store, indexed, config):
    versions = list(store.db.execute("SELECT id FROM media_versions ORDER BY id"))
    with store.db:
        for i, row in enumerate(versions):
            captured = datetime(2025, 1, 1 + i // 2, 12, 0, i % 2 * 5, tzinfo=UTC)
            store.db.execute(
                "UPDATE media_versions SET capture_datetime = ? WHERE id = ?",
                (captured.isoformat(), row["id"]),
            )
    create_set(store, "old", indexed, 1, 42)
    config = {**config, "prompt": "Frozen independent scoring instructions"}
    baseline_run = create_run(store, "old", config)
    with store.db:
        store.db.execute("UPDATE runs SET status = 'completed' WHERE id = ?", (baseline_run,))
    prepare(store.directory, "moment", indexed, baseline_run, pilot_size=1, test_size=2)
    return experiment_dir(store.directory, "moment")


def choose(directory, phase, preferred_index=1):
    manifest = load_manifest(directory)
    group = next(
        g
        for g in annotation_groups(directory, manifest, phase)
        if g["id"] not in labels(directory, phase)
    )
    save_annotation(
        directory,
        phase,
        {
            "group_id": group["id"],
            "status": "selected",
            "preferred": group["display_order"][preferred_index],
            "alternatives": [],
        },
        "human",
    )
    return group


def annotate_all(directory):
    choose(directory, "pilot")
    choose(directory, "test")
    choose(directory, "test")


def test_complete_temporal_components_not_truncated_or_joined_across_naive_dates():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        {"media_id": i, "capture_datetime": (start + timedelta(seconds=i * 20)).isoformat()}
        for i in range(7)
    ]
    assert temporal_groups(rows, 30, 120) == []  # Seven images is a whole excluded burst.
    assert temporal_groups(rows[:6], 30, 80) == []  # Span cap excludes, never splits.
    naive = {"media_id": 20, "capture_datetime": "2025-01-01T00:00:01"}
    grouped = temporal_groups([rows[0], rows[1], naive], 30, 120)
    assert [[row["media_id"] for row in group] for group in grouped] == [[0, 1]]
    offset = [
        {"media_id": 1, "capture_datetime": "2025-01-01T12:00:00+01:00"},
        {"media_id": 2, "capture_datetime": "2025-01-01T13:00:05+02:00"},
    ]
    assert len(temporal_groups(offset, 30, 120)) == 1


def test_preparation_read_only_reproducible_and_disjoint_days(store, indexed, moment_experiment):
    before = list(store.db.iterdump())
    manifest = load_manifest(moment_experiment)
    prepare(store.directory, "again", indexed, manifest["baseline_run_id"], 1, 2)
    other = load_manifest(experiment_dir(store.directory, "again"))
    assert manifest["groups"] == other["groups"]
    assert list(store.db.iterdump()) == before
    assert len({group["day"] for group in manifest["groups"]}) == len(manifest["groups"])
    prior_days = {
        row[0][:10]
        for row in store.db.execute(
            "SELECT v.capture_datetime FROM evaluation_members em "
            "JOIN media_versions v ON v.id=em.version_id"
        )
    }
    assert prior_days.isdisjoint(group["day"] for group in manifest["groups"])
    assert manifest["baseline_config"]["prompt"] == "Frozen independent scoring instructions"


@pytest.mark.parametrize("excluded_status", ["out_of_scope", "heterogeneous", "unjudgeable"])
def test_annotation_history_equivalents_replacements_and_phase_gates(
    moment_experiment, excluded_status
):
    directory = moment_experiment
    with pytest.raises(ValueError, match="pilot annotations"):
        public_state(directory, "test")
    group = load_manifest(directory)["groups"][0]
    save_annotation(
        directory,
        "pilot",
        {
            "group_id": group["id"],
            "status": excluded_status,
            "preferred": None,
        },
        "human",
    )
    replacement = choose(directory, "pilot")
    assert replacement["id"] != group["id"]
    state = public_state(directory, "pilot")
    assert state["done"] == 1
    assert state["excluded"] == 1
    assert labels(directory, "pilot")[group["id"]]["status"] == excluded_status
    preferred = replacement["display_order"][0]
    alternative = replacement["display_order"][1]
    save_annotation(
        directory,
        "pilot",
        {
            "group_id": replacement["id"],
            "status": "selected",
            "preferred": preferred,
            "alternatives": [alternative],
        },
        "human",
    )
    assert len(read_history(directory)) == 3
    assert labels(directory, "pilot")[replacement["id"]]["alternatives"] == [alternative]
    with pytest.raises(ValueError, match="Duplicate"):
        save_annotation(
            directory,
            "pilot",
            {
                "group_id": replacement["id"],
                "status": "selected",
                "preferred": preferred,
                "alternatives": [preferred],
            },
            "human",
        )
    choose(directory, "test")
    with pytest.raises(ValueError, match="locked"):
        save_annotation(
            directory,
            "pilot",
            {
                "group_id": replacement["id"],
                "status": "selected",
                "preferred": preferred,
            },
            "human",
        )
    run = prepare_run(directory, "pilot")
    assert [row["id"] for row in run["groups"]] == [replacement["id"]]
    assert paired_report(directory, "pilot")["annotation_exclusions"] == {excluded_status: 1}


def test_run_requires_complete_labels_and_locks_snapshot(moment_experiment):
    directory = moment_experiment
    calls = []
    with pytest.raises(ValueError, match="Finish all"):
        asyncio.run(
            execute(
                directory,
                "pilot",
                "fake",
                transport=httpx.MockTransport(lambda request: calls.append(request)),
            )
        )
    assert calls == []
    group = choose(directory, "pilot")
    run = prepare_run(directory, "pilot")
    assert run["groups"][0]["human"]["created_at"] <= run["created_at"]
    with pytest.raises(ValueError, match="frozen"):
        save_annotation(
            directory,
            "pilot",
            {
                "group_id": group["id"],
                "status": "selected",
                "preferred": group["display_order"][0],
            },
            "human",
        )
    proxy = Path(group["photos"][0]["proxy_path"])
    proxy.write_bytes(b"changed pixels")
    with pytest.raises(ValueError, match="proxy changed"):
        asyncio.run(
            execute(
                directory,
                "pilot",
                "fake",
                transport=httpx.MockTransport(lambda request: calls.append(request)),
            )
        )
    assert calls == []


def completion(content, finish="stop"):
    return httpx.Response(
        200,
        json={
            "provider": "Test",
            "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": finish}],
            "usage": {"cost": 0.001, "prompt_tokens": 100, "completion_tokens": 20},
        },
    )


def test_paired_execution_baseline_group_pixels_relabeling_resume_and_report(
    moment_experiment,
    monkeypatch,
):
    directory = moment_experiment
    annotate_all(directory)
    choose(directory, "retest")
    manifest = load_manifest(directory)
    targets = {row["preferred"] for row in labels(directory, "test").values()}
    encoded = {
        base64.b64encode(Path(photo["proxy_path"]).read_bytes()).decode(): photo["version_id"]
        for group in manifest["groups"]
        for photo in group["photos"]
    }
    calls = []

    def handler(request):
        body = json.loads(request.content)
        assert "source_path" not in request.content.decode()
        assert "capture_datetime" not in request.content.decode()
        assert "human" not in body
        content = body["messages"][0]["content"]
        image_ids = [
            encoded[item["image_url"]["url"].split(",", 1)[1]]
            for item in content
            if item["type"] == "image_url"
        ]
        calls.append(image_ids)
        if len(image_ids) == 1:
            assert content[0]["text"] == "Frozen independent scoring instructions"
            return completion({"album_score": 20 if image_ids[0] in targets else 90, "reason": "x"})
        assert [item["text"] for item in content[1:] if item["type"] == "text"] == [
            "Photo A",
            "Photo B",
        ]
        index = next(i for i, version in enumerate(image_ids) if version in targets)
        return completion({"selected_id": chr(65 + index), "reason": "comparative choice"})

    transport = httpx.MockTransport(handler)
    frozen_run = prepare_run(directory, "test")
    assert frozen_run["groups"][0]["joint_config"]["schema"]["properties"]["selected_id"][
        "enum"
    ] == ["A", "B"]
    from photocurate import moments

    def forbidden(*args):
        raise AssertionError("Resume must use the frozen schema and configuration")

    monkeypatch.setattr(moments, "joint_config", forbidden)
    result = asyncio.run(
        execute(directory, "test", "fake", transport=transport, progress=lambda _: None)
    )
    assert result["recorded_requests"] == 8  # 4 isolated + 2 joint + 2 order checks.
    assert result["errors"] == 0
    report = paired_report(directory, "test")
    assert report["quality"]["joint"]["agreement"] == 1
    assert report["quality"]["isolated"]["agreement"] == 0
    assert report["paired"]["joint_only"] == 2
    assert report["paired"]["gain"] == 1
    assert all(group["order_check_stable"] for group in report["groups"])
    assert report["operations"]["isolated"]["cost_total_usd"] == 0.004
    assert len(prepare_run(directory, "test")["human_retest"]) == 1
    count = len(calls)
    asyncio.run(execute(directory, "test", "fake", transport=transport, progress=lambda _: None))
    assert len(calls) == count


def test_invalid_joint_choice_remains_error_and_is_not_retried(moment_experiment):
    directory = moment_experiment
    choose(directory, "pilot")
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        images = sum(item["type"] == "image_url" for item in body["messages"][0]["content"])
        return (
            completion({"selected_id": "Z", "reason": "invalid"})
            if images > 1
            else completion({"album_score": 80, "reason": "x"})
        )

    transport = httpx.MockTransport(handler)
    result = asyncio.run(
        execute(directory, "pilot", "fake", transport=transport, progress=lambda _: None)
    )
    assert result["errors"] == 1
    assert paired_report(directory, "pilot")["quality"]["joint"]["agreement"] == 0
    asyncio.run(execute(directory, "pilot", "fake", transport=transport, progress=lambda _: None))
    assert len(calls) == 3


def test_pending_report_does_not_claim_final_agreement(moment_experiment):
    choose(moment_experiment, "pilot")
    prepare_run(moment_experiment, "pilot")
    report = paired_report(moment_experiment, "pilot")
    assert report["complete"] is False
    assert report["quality"]["joint"]["agreement"] is None
    assert report["paired"]["gain"] is None
    assert report["paired"]["both_wrong"] == 0


def test_local_gallery_hides_paths_results_and_saves_to_disk(moment_experiment):
    directory = moment_experiment
    server, url = annotation_server(directory, "human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client() as client:
            response = client.get(url)
            assert response.status_code == 200 and "Ma préférée" in response.text
            state = client.get(url + "api/state?phase=pilot").json()
            assert "source_path" not in json.dumps(state)
            assert "capture_datetime" not in json.dumps(state)
            assert "baseline_config" not in state
            group = state["groups"][0]
            preferred = group["photos"][0]["version_id"]
            assert client.get(url + f"image/{preferred}").headers["content-type"] == "image/jpeg"
            data = {
                "phase": "pilot",
                "group_id": group["id"],
                "status": "selected",
                "preferred": preferred,
                "alternatives": [],
            }
            response = client.post(url + "api/annotation", json=data)
            assert response.status_code == 200 and response.json()["done"] == 1
            assert labels(directory, "pilot")[group["id"]]["preferred"] == preferred
            response = client.post(
                url + "api/annotation", json=data, headers={"Origin": "https://external.example"}
            )
            assert response.status_code == 400
            assert client.get(url + "image/../../.env").status_code != 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_moment_cli_prepare_avoids_store_initialization(
    store, indexed, moment_experiment, monkeypatch, capsys
):
    from photocurate import cli

    def forbidden(*args):
        raise AssertionError("Moment commands must not create a writable Store")

    monkeypatch.setattr(cli, "Store", forbidden)
    baseline_run = load_manifest(moment_experiment)["baseline_run_id"]
    assert (
        main(
            [
                "--state-dir",
                str(store.directory),
                "moments",
                "prepare",
                "cli",
                "--source",
                str(indexed),
                "--baseline-run",
                str(baseline_run),
                "--pilot-size",
                "1",
                "--test-size",
                "2",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["candidate_days"] >= 3
