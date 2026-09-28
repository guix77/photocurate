import json

import pytest
from PIL import Image

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
