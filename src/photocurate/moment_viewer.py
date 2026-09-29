"""Loopback-only annotation page with persisted history and no model execution route."""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .moments import (
    annotation_groups,
    digest,
    frozen,
    labels,
    load_manifest,
    save_annotation,
    selected_labels,
)
from .store import dumps


def public_state(directory: Path, phase: str) -> dict:
    manifest = load_manifest(directory)
    groups = annotation_groups(directory, manifest, phase)
    annotations = labels(directory, phase)
    result = []
    for group in groups:
        photos = {photo["version_id"]: photo for photo in group["photos"]}
        order = group["display_order"]
        if phase == "retest":
            order = list(reversed(order))
        annotation = annotations.get(group["id"])
        result.append(
            {
                "id": group["id"],
                "gps_warning": group["gps_warning"],
                "photos": [
                    {
                        "version_id": version,
                        "label": chr(65 + i),
                        "width": photos[version]["width"],
                        "height": photos[version]["height"],
                    }
                    for i, version in enumerate(order)
                ],
                "annotation": {
                    key: annotation[key] for key in ("status", "preferred", "alternatives")
                }
                if annotation
                else None,
            }
        )
    quota = len(groups) if phase == "retest" else manifest["quotas"][phase]
    done = len(annotations) if phase == "retest" else len(selected_labels(directory, phase))
    locked = frozen(directory, "test" if phase == "retest" else phase)
    if phase == "pilot" and labels(directory, "test"):
        locked = True
    return {
        "name": manifest["name"],
        "phase": phase,
        "instructions": manifest["human_instructions"],
        "groups": result,
        "done": done,
        "quota": quota,
        "locked": locked,
        "excluded": sum(row["status"] != "selected" for row in annotations.values()),
        "pilot_complete": len(selected_labels(directory, "pilot")) == manifest["quotas"]["pilot"],
        "test_complete": len(selected_labels(directory, "test")) == manifest["quotas"]["test"],
    }


def annotation_server(directory: Path, annotator: str, port: int = 0):
    if not annotator.strip():
        raise ValueError("An annotator name is required")
    manifest = load_manifest(directory)
    photos = {
        str(photo["version_id"]): photo for group in manifest["groups"] for photo in group["photos"]
    }
    prefix = "/" + secrets.token_urlsafe(24)
    lock = threading.Lock()
    page = Path(__file__).with_name("moment_annotation.html").read_text()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, body, content_type="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self'; frame-ancestors 'none'; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(data)

        def route(self):
            parsed = urlsplit(self.path)
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host:
                raise ValueError("Unexpected Host header")
            if not parsed.path.startswith(prefix + "/"):
                raise ValueError("Unknown local annotation URL")
            return parsed.path[len(prefix) :], parse_qs(parsed.query)

        def do_GET(self):
            try:
                route, query = self.route()
                if route == "/":
                    self.reply(200, page.replace("__PREFIX__", prefix), "text/html; charset=utf-8")
                elif route == "/api/state":
                    with lock:
                        state = public_state(directory, query.get("phase", ["pilot"])[0])
                    self.reply(200, dumps(state))
                elif route.startswith("/image/"):
                    photo = photos.get(route.removeprefix("/image/"))
                    if photo is None:
                        self.reply(404, dumps({"error": "Unknown image"}))
                        return
                    path = Path(photo["proxy_path"])
                    if digest(path) != photo["sha256"]:
                        raise ValueError("This proxy changed since preparation")
                    self.reply(200, path.read_bytes(), "image/jpeg")
                else:
                    self.reply(404, dumps({"error": "Unknown annotation route"}))
            except (ValueError, OSError) as exc:
                self.reply(400, dumps({"error": str(exc)}))

        def do_POST(self):
            try:
                route, _ = self.route()
                origin = self.headers.get("Origin")
                if origin not in (None, f"http://127.0.0.1:{self.server.server_port}"):
                    raise ValueError("Unexpected request origin")
                if route != "/api/annotation":
                    self.reply(404, dumps({"error": "Unknown annotation route"}))
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384:
                    raise ValueError("Invalid annotation size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Annotation must be an object")
                phase = data.get("phase", "pilot")
                with lock:
                    save_annotation(directory, phase, data, annotator)
                    state = public_state(directory, phase)
                self.reply(200, dumps(state))
            except (ValueError, OSError) as exc:
                self.reply(400, dumps({"error": str(exc)}))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, f"http://127.0.0.1:{server.server_port}{prefix}/"


def serve(directory: Path, annotator: str, phase: str = "pilot", port: int = 0) -> None:
    public_state(directory, phase)
    server, url = annotation_server(directory, annotator, port)
    print(f"Annotation locale : {url}?phase={phase}", flush=True)
    print(
        "Choix enregistrés automatiquement. Ctrl-C ferme le serveur ; la session reprend.",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
