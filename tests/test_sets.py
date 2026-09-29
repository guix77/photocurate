import json

import pytest
from PIL import Image

from photocurate.cli import main
from photocurate.media import index_corpus
from photocurate.runs import create_run
from photocurate.sets import create_set, extend_set


def test_random_sample_reproducible_and_extension_preserves_members_labels_runs(
    store,
    indexed,
    config,
):
    create_set(store, "first", indexed, 5, 42)
    create_set(store, "same-seed", indexed, 5, 42)
    first = store.get_set("first")
    members = store.members(first["id"])
    assert [row["media_id"] for row in members] == [
        row["media_id"] for row in store.members(store.get_set("same-seed")["id"])
    ]
    store.set_label(first["id"], 1, "3", "Guix")
    run_id = create_run(store, "first", config)
    extend_set(store, "first", 9)
    expanded = store.members(first["id"])
    assert [(row["media_id"], row["version_id"]) for row in expanded[:5]] == [
        (row["media_id"], row["version_id"]) for row in members
    ]
    assert expanded[0]["label"] == "3"
    assert len({row["media_id"] for row in expanded}) == 9
    assert (
        store.db.execute("SELECT COUNT(*) FROM run_members WHERE run_id = ?", (run_id,)).fetchone()[
            0
        ]
        == 5
    )
    assert json.loads(store.get_run(run_id)["config_json"])["prompt"] == config["prompt"]
    with pytest.raises(ValueError, match="already exists"):
        create_set(store, "first", indexed, 5, 42)
    with pytest.raises(ValueError, match="exceed"):
        extend_set(store, "first", 9)


def test_rescan_does_not_replace_selected_version(store, indexed):
    create_set(store, "sample", indexed, 5, 1)
    evaluation = store.get_set("sample")
    frozen = store.members(evaluation["id"])[0]
    Image.new("RGB", (100, 100), "yellow").save(frozen["source_path"])
    index_corpus(store, indexed, progress=lambda _: None)
    current = store.db.execute(
        "SELECT current_version_id FROM media WHERE id = ?", (frozen["media_id"],)
    ).fetchone()[0]
    assert current != frozen["version_id"]
    assert store.members(evaluation["id"])[0]["version_id"] == frozen["version_id"]


def test_sampling_does_not_include_missing_or_stale_images(store, indexed):
    from pathlib import Path

    paths = list(indexed.rglob("*.jpg"))
    paths[0].unlink()
    Image.new("RGB", (100, 100)).save(paths[1])
    with pytest.raises(ValueError, match="only 10"):
        create_set(store, "too-large", indexed, 11, 42)
    create_set(store, "available", indexed, 10, 42)
    chosen = {Path(row["source_path"]) for row in store.members(store.get_set("available")["id"])}
    assert paths[0] not in chosen and paths[1] not in chosen


def test_exclusion_covers_new_versions_and_preserves_existing_labels_and_runs(
    store, indexed, config
):
    create_set(store, "baseline", indexed, 5, 42)
    baseline = store.get_set("baseline")
    members = store.members(baseline["id"])
    store.set_label(baseline["id"], 1, "3", "Guix")
    run_id = create_run(store, "baseline", config)
    with store.db:
        store.db.execute(
            """INSERT INTO results
               (run_id, position, status, raw_response, score, latency_ms, created_at)
               VALUES (?, 1, 'ok', 'saved response', 87, 100, 'saved timestamp')""",
            (run_id,),
        )
    frozen = members[0]
    Image.new("RGB", (100, 100), "yellow").save(frozen["source_path"])
    index_corpus(store, indexed, progress=lambda _: None)
    assert (
        store.db.execute(
            "SELECT current_version_id FROM media WHERE id = ?", (frozen["media_id"],)
        ).fetchone()[0]
        != frozen["version_id"]
    )

    tables = ("human_labels", "label_history", "runs", "run_members", "results")
    before = {
        table: [tuple(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    }
    baseline_members = [dict(row) for row in store.members(baseline["id"])]
    create_set(store, "holdout", indexed, 7, 42, exclude_set="baseline")
    chosen = store.members(store.get_set("holdout")["id"])
    assert len(chosen) == 7
    assert {row["media_id"] for row in members}.isdisjoint(row["media_id"] for row in chosen)
    assert dict(store.get_set("baseline")) == dict(baseline)
    assert [dict(row) for row in store.members(baseline["id"])] == baseline_members
    assert {
        table: [tuple(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    } == before


def test_excluded_sample_is_reproducible_and_persists_source_and_seed(store, indexed):
    create_set(store, "baseline", indexed, 5, 42)
    for name in ("holdout", "same-seed"):
        create_set(store, name, indexed, 3, 123, exclude_set="baseline")
    holdout = store.get_set("holdout")
    assert holdout["source_root"] == str(indexed.resolve())
    assert holdout["seed"] == 123
    assert [row["media_id"] for row in store.members(holdout["id"])] == [
        row["media_id"] for row in store.members(store.get_set("same-seed")["id"])
    ]


@pytest.mark.parametrize(
    ("excluded", "size", "message"),
    [
        ("missing", 3, "Unknown evaluation set: missing"),
        ("baseline", 8, "only 7 are indexed and unchanged after excluding baseline"),
    ],
)
def test_invalid_exclusion_fails_without_creating_a_sample(store, indexed, excluded, size, message):
    create_set(store, "baseline", indexed, 5, 42)
    tables = ("evaluation_sets", "evaluation_members", "proxies")
    before = {
        table: [tuple(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    }
    with pytest.raises(ValueError, match=message):
        create_set(store, "holdout", indexed, size, 42, exclude_set=excluded)
    assert {
        table: [tuple(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    } == before


def test_cli_create_sample_with_exclusion(store, indexed, capsys):
    create_set(store, "baseline", indexed, 5, 42)
    assert (
        main(
            [
                "--state-dir",
                str(store.directory),
                "sample",
                "create",
                "holdout",
                "--source",
                str(indexed),
                "--size",
                "7",
                "--seed",
                "123",
                "--exclude-set",
                "baseline",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["exclude_set"] == "baseline"
    holdout = store.get_set("holdout")
    assert holdout["seed"] == 123
    members = store.members(holdout["id"])
    assert len(members) == 7
    assert {row["media_id"] for row in store.members(store.get_set("baseline")["id"])}.isdisjoint(
        row["media_id"] for row in members
    )
