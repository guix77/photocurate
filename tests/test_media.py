import hashlib
import os
from pathlib import Path

from PIL import Image

from photocurate.media import ensure_proxy, index_corpus
from photocurate.store import Store


def snapshots(root):
    return {
        str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_incremental_scan_preserves_originals_and_isolates_bad_files(store, corpus):
    (corpus / "bad.jpg").write_bytes(b"not an image")
    (corpus / "movie.mp4").write_bytes(b"ignored")
    before = snapshots(corpus)
    first = index_corpus(store, corpus, progress=lambda _: None)
    assert first == {
        "source_root": str(corpus),
        "discovered": 13,
        "added": 12,
        "refreshed": 0,
        "reused": 0,
        "errors": 1,
    }
    assert snapshots(corpus) == before
    rows = store.db.execute("SELECT * FROM media_versions").fetchall()
    assert all(row["capture_datetime"] is None and row["latitude"] is None for row in rows)
    proxies = {
        row["path"]: Path(row["path"]).stat().st_mtime_ns
        for row in store.db.execute("SELECT * FROM proxies")
    }
    second = index_corpus(store, corpus, progress=lambda _: None)
    assert second["reused"] == 12 and second["errors"] == 1
    assert all(Path(path).stat().st_mtime_ns == mtime for path, mtime in proxies.items())
    path = corpus / "nested" / "photo-00.jpg"
    Image.new("RGB", (700, 600), "yellow").save(path)
    third = index_corpus(store, corpus, progress=lambda _: None)
    assert third["refreshed"] == 1 and third["reused"] == 11
    assert store.db.execute("SELECT COUNT(*) FROM media_versions").fetchone()[0] == 13


def test_orientation_capture_time_gps_and_no_metadata_in_proxy(store, tmp_path):
    root = tmp_path / "exif"
    root.mkdir()
    exif = Image.Exif()
    exif[274] = 6
    exif[0x8769] = {36867: "2025:07:12 10:20:30", 36881: "+02:00", 37521: "123"}
    exif[0x8825] = {1: "N", 2: (48.0, 51.0, 0.0), 3: "W", 4: (2.0, 20.0, 0.0)}
    original = root / "oriented.jpg"
    Image.new("RGB", (800, 400), "red").save(original, exif=exif)
    before = snapshots(root)
    result = index_corpus(store, root, progress=lambda _: None)
    assert result["errors"] == 0
    row = store.db.execute("SELECT * FROM media_versions").fetchone()
    assert row["capture_datetime"] == "2025-07-12T10:20:30.123+02:00"
    assert row["orientation"] == 6
    assert row["latitude"] == 48.85
    assert abs(row["longitude"] + 2 + 1 / 3) < 1e-10
    proxy = ensure_proxy(store, row["id"], 512)
    with Image.open(proxy) as image:
        assert image.size == (256, 512)
        assert not image.getexif()
        assert "icc_profile" not in image.info
    assert snapshots(root) == before


def test_missing_or_unreliable_capture_date_is_not_replaced_by_mtime(store, tmp_path):
    root = tmp_path / "dates"
    root.mkdir()
    exif = Image.Exif()
    exif[306] = "2025:07:12 10:20:30"  # modification date only
    exif[0x8769] = {36867: "0000:00:00 00:00:00"}
    Image.new("RGB", (30, 20)).save(root / "unknown.jpg", exif=exif)
    index_corpus(store, root, progress=lambda _: None)
    assert store.db.execute("SELECT capture_datetime FROM media_versions").fetchone()[0] is None


def test_supported_formats_and_heic(store, tmp_path):
    import pillow_heif

    root = tmp_path / "formats"
    root.mkdir()
    for name in ("image.png", "image.webp", "image.JPEG"):
        Image.new("RGB", (30, 20), "blue").save(root / name)
    image = Image.new("RGB", (32, 24), "green")
    pillow_heif.from_pillow(image).save(root / "image.heic")
    Image.new("RGBA", (40, 20), (255, 0, 0, 0)).save(root / "transparent.png")
    result = index_corpus(store, root, progress=lambda _: None)
    assert result["added"] == 5 and result["errors"] == 0
    row = store.db.execute(
        "SELECT current_version_id FROM media WHERE source_path = ?",
        (str(root / "transparent.png"),),
    ).fetchone()
    with Image.open(ensure_proxy(store, row[0], 512)) as proxy:
        assert proxy.getpixel((0, 0)) == (255, 255, 255)


def test_state_inside_corpus_is_excluded(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    Image.new("RGB", (30, 20)).save(root / "image.jpg")
    store = Store(root / ".photocurate")
    try:
        assert index_corpus(store, root)["added"] == 1
        assert index_corpus(store, root)["discovered"] == 1
    finally:
        store.close()


def test_heic_orientation_is_preserved_as_metadata_without_rotating_twice(store, tmp_path):
    root = tmp_path / "heic-orientation"
    root.mkdir()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (800, 400), "red").save(
        root / "rotated.heic", format="HEIF", exif=exif.tobytes()
    )
    original = snapshots(root)
    result = index_corpus(store, root, progress=lambda _: None)
    assert result["added"] == 1 and result["errors"] == 0
    version = store.db.execute("SELECT * FROM media_versions").fetchone()
    assert version["orientation"] == 6
    with Image.open(ensure_proxy(store, version["id"], 512)) as proxy:
        assert proxy.size == (256, 512)
        assert not proxy.getexif()
    assert snapshots(root) == original


def test_frozen_proxy_not_recreated_from_changed_original(store, indexed):
    row = store.db.execute("SELECT * FROM media_versions LIMIT 1").fetchone()
    original = Path(
        store.db.execute(
            "SELECT source_path FROM media WHERE id = ?", (row["media_id"],)
        ).fetchone()[0]
    )
    proxy = ensure_proxy(store, row["id"], 512)
    old_bytes = proxy.read_bytes()
    Image.new("RGB", (300, 200), "black").save(original)
    os.utime(original, ns=(row["mtime_ns"] + 1_000_000, row["mtime_ns"] + 1_000_000))
    assert ensure_proxy(store, row["id"], 512).read_bytes() == old_bytes
    import pytest

    with pytest.raises(ValueError, match="Source changed"):
        ensure_proxy(store, row["id"], 768)
