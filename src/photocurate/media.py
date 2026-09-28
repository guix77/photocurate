"""Python-native image discovery, minimal EXIF extraction and immutable JPEG proxies."""

import math
import os
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from .store import Store, now

register_heif_opener()
SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def source_root(path: Path) -> Path:
    root = path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Source must be a directory: {root}")
    return root


def discover(root: Path, excluded: Path):
    """Do not follow directory symlinks or index the application's derived files."""
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(
            folder
            for folder in folders
            if not (Path(directory) / folder).resolve().is_relative_to(excluded)
        )
        for filename in sorted(files):
            path = Path(directory) / filename
            if path.suffix.lower() in SUPPORTED:
                try:
                    resolved = path.resolve()
                    if resolved.is_relative_to(root) and not resolved.is_relative_to(excluded):
                        yield resolved
                except (OSError, RuntimeError):
                    # A broken symlink must not stop scanning the rest of the corpus.
                    continue


def capture_datetime(exif: Image.Exif) -> str | None:
    # DateTimeOriginal and DateTimeDigitized are capture-related. EXIF DateTime
    # alone can describe editing, so neither it nor filesystem mtime is a fallback.
    try:
        tags = exif.get_ifd(0x8769)
    except (KeyError, TypeError, ValueError, SyntaxError):
        tags = {}
    for date_tag, offset_tag, subsecond_tag in ((36867, 36881, 37521), (36868, 36882, 37522)):
        value = tags.get(date_tag, exif.get(date_tag))
        if isinstance(value, bytes):
            value = value.decode("ascii", errors="replace")
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.strptime(value.strip("\x00 "), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
        result = parsed.isoformat()
        fraction = str(tags.get(subsecond_tag, exif.get(subsecond_tag, ""))).strip("\x00 ")
        if re.fullmatch(r"\d{1,6}", fraction):
            result += "." + fraction
        offset = tags.get(offset_tag, exif.get(offset_tag))
        if isinstance(offset, str) and re.fullmatch(r"[+-](?:[01]\d|2[0-3]):[0-5]\d", offset):
            result += offset
        return result
    return None


def coordinates(exif: Image.Exif) -> tuple[float | None, float | None]:
    try:
        gps = exif.get_ifd(0x8825)

        def coordinate(tag: int, ref_tag: int, positive: str, negative: str) -> float:
            ref = gps[ref_tag]
            if isinstance(ref, bytes):
                ref = ref.decode("ascii")
            if ref not in (positive, negative):
                raise ValueError("Invalid GPS reference")
            degrees, minutes, seconds = (float(value) for value in gps[tag])
            if degrees < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
                raise ValueError("Invalid GPS components")
            result = degrees + minutes / 60 + seconds / 3600
            return -result if ref == negative else result

        lat = coordinate(2, 1, "N", "S")
        lon = coordinate(4, 3, "E", "W")
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (KeyError, TypeError, ValueError, ZeroDivisionError, UnicodeError, SyntaxError):
        pass
    return None, None


def metadata(image: Image.Image) -> dict:
    try:
        exif = image.getexif()
        # pillow-heif applies container rotation and resets the EXIF orientation.
        # Keep its original tag as metadata; proxy generation uses the decoded pixels.
        orientation = image.info.get("original_orientation") or exif.get(274)
        lat, lon = coordinates(exif)
        return {
            "capture_datetime": capture_datetime(exif),
            "latitude": lat,
            "longitude": lon,
            "orientation": orientation if orientation in range(1, 9) else None,
        }
    except (OSError, ValueError, TypeError, SyntaxError):
        return dict.fromkeys(("capture_datetime", "latitude", "longitude", "orientation"))


def write_proxy(image: Image.Image, target: Path, long_edge: int) -> tuple[int, int]:
    oriented = ImageOps.exif_transpose(image)
    oriented.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    if "A" in oriented.getbands() or "transparency" in oriented.info:
        rgba = oriented.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
    else:
        rgb = oriented.convert("RGB")
    # Explicitly clear all metadata: model inputs contain pixels only.
    rgb.info.clear()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    try:
        rgb.save(temporary, format="JPEG", quality=85, optimize=True, exif=b"")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return rgb.size


def ensure_proxy(store: Store, version_id: int, long_edge: int) -> Path:
    if long_edge < 1:
        raise ValueError("Proxy long edge must be positive")
    row = store.db.execute(
        "SELECT * FROM proxies WHERE version_id = ? AND long_edge = ?",
        (version_id, long_edge),
    ).fetchone()
    if row and Path(row["path"]).is_file():
        return Path(row["path"])
    version = store.db.execute(
        """SELECT v.*, m.source_path FROM media_versions v
           JOIN media m ON m.id = v.media_id WHERE v.id = ?""",
        (version_id,),
    ).fetchone()
    if version is None:
        raise ValueError(f"Unknown media version: {version_id}")
    path = Path(version["source_path"])
    before = path.stat()
    if (before.st_size, before.st_mtime_ns) != (version["file_size"], version["mtime_ns"]):
        raise ValueError(f"Source changed since selection; cannot recreate this proxy: {path}")
    target = store.directory / "proxies" / str(version_id) / f"{long_edge}.jpg"
    with Image.open(path) as image:
        width, height = write_proxy(image, target, long_edge)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        target.unlink(missing_ok=True)
        raise ValueError(f"Source changed during proxy generation: {path}")
    with store.db:
        store.db.execute(
            "INSERT OR REPLACE INTO proxies VALUES (?, ?, ?, ?, ?)",
            (version_id, long_edge, str(target), width, height),
        )
    return target


def index_corpus(store: Store, source: Path, long_edge: int = 512, progress=print) -> dict:
    root = source_root(source)
    if root.is_relative_to(store.directory):
        raise ValueError("Source directory cannot be inside the application state directory")
    if long_edge < 1:
        raise ValueError("Proxy long edge must be positive")
    counts = dict.fromkeys(("added", "refreshed", "reused", "errors"), 0)
    seen = set()
    for path in discover(root, store.directory):
        if path in seen:
            continue
        seen.add(path)
        row = store.db.execute(
            """SELECT m.*, v.file_size, v.mtime_ns FROM media m
               LEFT JOIN media_versions v ON v.id = m.current_version_id
               WHERE m.source_path = ?""",
            (str(path),),
        ).fetchone()
        try:
            before = path.stat()
            if (
                row
                and row["status"] == "indexed"
                and (before.st_size, before.st_mtime_ns) == (row["file_size"], row["mtime_ns"])
            ):
                ensure_proxy(store, row["current_version_id"], long_edge)
                counts["reused"] += 1
                continue
            with Image.open(path) as image:
                image.load()
                info = metadata(image)
            with store.db:
                if row is None:
                    cursor = store.db.execute(
                        "INSERT INTO media (source_path, status, last_seen_at) VALUES (?, ?, ?)",
                        (str(path), "indexing", now()),
                    )
                    media_id = cursor.lastrowid
                else:
                    media_id = row["id"]
                cursor = store.db.execute(
                    """INSERT INTO media_versions
                       (media_id, file_size, mtime_ns, capture_datetime, latitude, longitude,
                        orientation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        media_id,
                        before.st_size,
                        before.st_mtime_ns,
                        info["capture_datetime"],
                        info["latitude"],
                        info["longitude"],
                        info["orientation"],
                        now(),
                    ),
                )
                version_id = cursor.lastrowid
            ensure_proxy(store, version_id, long_edge)
            with store.db:
                store.db.execute(
                    """UPDATE media SET current_version_id = ?, status = 'indexed',
                       error = NULL, last_seen_at = ? WHERE id = ?""",
                    (version_id, now(), media_id),
                )
            counts[
                "added" if row is None or row["current_version_id"] is None else "refreshed"
            ] += 1
        except Exception as exc:
            # Image decoders can raise multiple exception types. Isolate per-file failures.
            counts["errors"] += 1
            with store.db:
                store.db.execute(
                    """INSERT INTO media (source_path, status, error, last_seen_at)
                       VALUES (?, 'error', ?, ?) ON CONFLICT(source_path) DO UPDATE SET
                       status = 'error', error = excluded.error,
                       last_seen_at = excluded.last_seen_at""",
                    (str(path), f"{type(exc).__name__}: {exc}", now()),
                )
            progress(f"Unreadable image: {path}: {exc}")
        if len(seen) % 100 == 0:
            progress(f"Scanned {len(seen)} images: {counts}")
    return {"source_root": str(root), "discovered": len(seen), **counts}
