"""Reproducible random selection and append-only enlargement of the same set."""

import random
from pathlib import Path

from .media import ensure_proxy, source_root
from .store import Store, now


def candidates(store: Store, root: Path) -> list:
    rows = store.db.execute(
        """SELECT m.id AS media_id, m.source_path, v.id AS version_id,
                  v.file_size, v.mtime_ns
           FROM media m JOIN media_versions v ON v.id = m.current_version_id
           WHERE m.status = 'indexed' ORDER BY m.source_path"""
    ).fetchall()
    eligible = []
    for row in rows:
        path = Path(row["source_path"])
        if not path.is_relative_to(root):
            continue
        try:
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) == (row["file_size"], row["mtime_ns"]):
                eligible.append(row)
        except OSError:
            continue
    return eligible


def create_set(
    store: Store,
    name: str,
    source: Path,
    size: int,
    seed: int,
    *,
    exclude_set: str | None = None,
) -> dict:
    if size < 1 or not name.strip():
        raise ValueError("Set name and positive size are required")
    if store.db.execute("SELECT 1 FROM evaluation_sets WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"Evaluation set already exists: {name}; use sample extend")
    excluded = set()
    if exclude_set is not None:
        evaluation = store.get_set(exclude_set)
        excluded = {row["media_id"] for row in store.members(evaluation["id"])}
    root = source_root(source)
    available = [row for row in candidates(store, root) if row["media_id"] not in excluded]
    if size > len(available):
        exclusion = f" after excluding {exclude_set}" if exclude_set is not None else ""
        raise ValueError(
            f"Requested {size} images, but only {len(available)} "
            f"are indexed and unchanged{exclusion}"
        )
    chosen = random.Random(seed).sample(available, size)
    for row in chosen:
        ensure_proxy(store, row["version_id"], 512)
    with store.db:
        cursor = store.db.execute(
            "INSERT INTO evaluation_sets (name, source_root, seed, created_at) VALUES (?, ?, ?, ?)",
            (name, str(root), seed, now()),
        )
        set_id = cursor.lastrowid
        store.db.executemany(
            "INSERT INTO evaluation_members VALUES (?, ?, ?, ?)",
            [(set_id, i + 1, row["media_id"], row["version_id"]) for i, row in enumerate(chosen)],
        )
    result = {"name": name, "size": size, "seed": seed, "source_root": str(root)}
    if exclude_set is not None:
        result["exclude_set"] = exclude_set
    return result


def extend_set(store: Store, name: str, size: int) -> dict:
    evaluation = store.get_set(name)
    members = store.members(evaluation["id"])
    previous = len(members)
    if size <= previous:
        raise ValueError(f"New total size must exceed the current size ({previous})")
    excluded = {row["media_id"] for row in members}
    available = [
        row
        for row in candidates(store, Path(evaluation["source_root"]))
        if row["media_id"] not in excluded
    ]
    extra = size - previous
    if extra > len(available):
        raise ValueError(f"Need {extra} new images, but only {len(available)} are available")
    seed = f"{evaluation['seed']}:{previous}:{size}"
    chosen = random.Random(seed).sample(available, extra)
    for row in chosen:
        ensure_proxy(store, row["version_id"], 512)
    with store.db:
        store.db.executemany(
            "INSERT INTO evaluation_members VALUES (?, ?, ?, ?)",
            [
                (evaluation["id"], previous + i + 1, row["media_id"], row["version_id"])
                for i, row in enumerate(chosen)
            ],
        )
        store.db.execute(
            """INSERT INTO evaluation_extensions
               (set_id, previous_size, new_size, seed, created_at) VALUES (?, ?, ?, ?, ?)""",
            (evaluation["id"], previous, size, seed, now()),
        )
    return {"name": name, "previous_size": previous, "size": size, "added": extra}
