import asyncio
import json
from pathlib import Path

import httpx
import pytest

from photocurate.annotate import annotate
from photocurate.report import describe_run, markdown, write_reports
from photocurate.runs import create_run, execute_run
from photocurate.sets import create_set
from photocurate.store import Store, dumps

from .test_provider import completion


@pytest.mark.parametrize("custom_prompt", [False, True])
def test_annotation_instructions_and_model_request_match_persisted_convention(
    store,
    indexed,
    config,
    tmp_path,
    custom_prompt,
):
    create_set(store, "sample", indexed, 1, 42)
    output = []
    answers = iter(("2", "q"))
    annotate(
        store, "sample", "Guix", viewer=False, ask=lambda _: next(answers), output=output.append
    )
    member = store.members(store.get_set("sample")["id"])[0]
    convention = json.loads(member["convention_json"])
    assert convention["human_instructions"] in output

    if custom_prompt:
        config["prompt"] = "Rank this photo with album_score and return a short reason."
    run_id = create_run(store, "sample", config)
    saved = json.loads(store.get_run(run_id)["config_json"])
    assert saved["judgement_convention"] == convention
    assert config["prompt"] in saved["prompt"]

    def handler(request):
        prompt = json.loads(request.content)["messages"][0]["content"][0]["text"]
        assert prompt == saved["prompt"]
        assert convention["model_instructions"] in prompt
        return httpx.Response(200, json=completion('{"album_score": 70, "reason": "Crop only"}'))

    asyncio.run(
        execute_run(store, run_id, "fake-key", lambda _: None, httpx.MockTransport(handler))
    )
    reports = write_reports(store, [run_id], [1], tmp_path / "reports")
    assert (
        "label_convention_json" in Path(reports["csv"]).read_text(encoding="utf-8").splitlines()[0]
    )
    report = describe_run(store, run_id, [1])
    assert report["quality"]["paired_images"] == 1
    assert report["quality"]["convention_mismatches"] == 0


def test_changed_convention_revisits_labels_and_excludes_incompatible_agreement(
    store,
    indexed,
    config,
):
    create_set(store, "sample", indexed, 2, 42)
    set_id = store.get_set("sample")["id"]
    store.set_label(set_id, 1, "0", "Guix")
    store.set_label(set_id, 2, "3", "Guix")
    legacy_run = create_run(store, "sample", config)
    legacy_config = json.loads(store.get_run(legacy_run)["config_json"])
    legacy_config.pop("judgement_convention")
    legacy_config["prompt"] = "Evaluate this photo as presented, using album_score and reason."
    # Represent the state written by v1 before conventions were explicitly recorded.
    with store.db:
        store.db.execute("UPDATE human_labels SET convention_json = NULL")
        store.db.execute("UPDATE label_history SET convention_json = NULL")
        store.db.execute(
            "UPDATE runs SET config_json = ? WHERE id = ?", (dumps(legacy_config), legacy_run)
        )
    current_run = create_run(store, "sample", config)
    for run_id in (legacy_run, current_run):
        saved_prompt = json.loads(store.get_run(run_id)["config_json"])["prompt"]

        def handler(request, prompt=saved_prompt):
            assert json.loads(request.content)["messages"][0]["content"][0]["text"] == prompt
            return httpx.Response(200, json=completion('{"album_score": 80, "reason": "Fixture"}'))

        transport = httpx.MockTransport(handler)
        asyncio.run(execute_run(store, run_id, "fake-key", lambda _: None, transport))

    legacy = describe_run(store, legacy_run, [1])
    current = describe_run(store, current_run, [1])
    assert legacy["quality"]["paired_images"] == 2
    assert current["quality"]["paired_images"] == 0
    assert current["quality"]["convention_mismatches"] == 2
    assert current["quality"]["human_0_highest"] == []
    assert current["quality"]["human_3_lowest"] == []
    top = current["quality"]["top_k"][0]
    assert top["selected"] == 1 and top["incompatible_labels"] == 1
    assert top["precision_human_2_or_3"] is None and top["recall_human_3"] is None
    text = markdown([legacy, current])
    assert "different — agreement metrics" in text

    # Merely opening the revised workflow cannot reinterpret or overwrite old labels.
    output = []
    result = annotate(
        store, "sample", "Guix", viewer=False, ask=lambda _: "q", output=output.append
    )
    assert result["writes"] == 0 and result["labelled"] == 0
    assert [row["label"] for row in store.members(set_id)] == ["0", "3"]
    assert any("2 annotations à revoir" in line for line in output)

    answers = iter(("2", "3", "q"))
    result = annotate(
        store, "sample", "Guix", viewer=False, ask=lambda _: next(answers), output=lambda _: None
    )
    assert result["labelled"] == 2
    history = store.db.execute("SELECT label, convention_json FROM label_history ORDER BY id")
    assert [(row["label"], row["convention_json"] is None) for row in history] == [
        ("0", True),
        ("3", True),
        ("2", False),
        ("3", False),
    ]
    assert describe_run(store, current_run, [1])["quality"]["paired_images"] == 2
    assert describe_run(store, legacy_run, [1])["quality"]["convention_mismatches"] == 2


def test_v1_database_migration_preserves_existing_labels_and_run_config(store, indexed, config):
    create_set(store, "sample", indexed, 1, 42)
    set_id = store.get_set("sample")["id"]
    store.set_label(set_id, 1, "3", "Guix")
    run_id = create_run(store, "sample", config)
    original_config = store.get_run(run_id)["config_json"]
    # Remove only the new columns to produce the previous database schema.
    with store.db:
        store.db.execute("ALTER TABLE human_labels DROP COLUMN convention_json")
        store.db.execute("ALTER TABLE label_history DROP COLUMN convention_json")
        store.db.execute("PRAGMA user_version = 1")
    directory = store.directory
    store.close()
    migrated = Store(directory)
    try:
        assert migrated.db.execute("PRAGMA user_version").fetchone()[0] == 2
        member = migrated.members(set_id)[0]
        assert member["label"] == "3" and member["annotator"] == "Guix"
        assert member["convention_json"] is None
        assert (
            migrated.db.execute("SELECT convention_json FROM label_history").fetchone()[0] is None
        )
        assert migrated.get_run(run_id)["config_json"] == original_config
    finally:
        migrated.close()
