"""Frozen run configurations and resumable deterministic calls."""

import asyncio
import json

import httpx

from . import __version__
from .convention import judgement_target_id
from .media import ensure_proxy
from .provider import ENDPOINT, evaluate_image, response_format, validate_config
from .store import Store, dumps, now


def create_run(store: Store, name: str, config: dict) -> int:
    validate_config(config)
    config = dict(config)
    convention = config.get("judgement_convention")
    if convention is not None and judgement_target_id(convention) is None:
        raise ValueError("Judgement target must be a non-empty ID or unspecified")
    evaluation = store.get_set(name)
    members = store.members(evaluation["id"])
    proxies = [ensure_proxy(store, row["version_id"], config["proxy_size"]) for row in members]
    config = {
        **config,
        "provider": "openrouter",
        "endpoint": ENDPOINT,
        "response_format": response_format(config["schema"]),
        "provider_preferences": {"require_parameters": True},
        "privacy_policy": "ZDR and no training enforced by OpenRouter account/key",
        "application_version": __version__,
        "model_context": "image_only",
        "judgement_convention": {"id": judgement_target_id(convention)} if convention else None,
        "stream": False,
    }
    with store.db:
        cursor = store.db.execute(
            """INSERT INTO runs (set_id, config_json, created_at, status)
               VALUES (?, ?, ?, 'pending')""",
            (evaluation["id"], dumps(config), now()),
        )
        run_id = cursor.lastrowid
        store.db.executemany(
            "INSERT INTO run_members VALUES (?, ?, ?, ?)",
            [
                (run_id, row["position"], row["version_id"], str(proxy))
                for row, proxy in zip(members, proxies, strict=True)
            ],
        )
    return run_id


async def execute_run(store: Store, run_id: int, key: str, progress=print, transport=None) -> dict:
    if not key.strip():
        raise ValueError("OPENROUTER_API_KEY is missing; set it in .env or the environment")
    run = store.get_run(run_id)
    config = json.loads(run["config_json"])
    pending = store.db.execute(
        """SELECT rm.* FROM run_members rm LEFT JOIN results r
           ON r.run_id = rm.run_id AND r.position = rm.position
           WHERE rm.run_id = ? AND r.position IS NULL ORDER BY rm.position""",
        (run_id,),
    ).fetchall()
    if not pending:
        return finalize_run(store, run_id)
    with store.db:
        store.db.execute(
            "UPDATE runs SET status = 'running', finished_at = NULL WHERE id = ?", (run_id,)
        )
    queue = asyncio.Queue()
    for member in pending:
        queue.put_nowait(member)
    total = store.db.execute(
        "SELECT COUNT(*) FROM run_members WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    completed = total - len(pending)
    abort = asyncio.Event()

    async def worker(client):
        nonlocal completed
        while not queue.empty() and not abort.is_set():
            member = queue.get_nowait()
            try:
                # Reuse the exact immutable proxy selected at run creation.
                proxy = ensure_proxy(store, member["version_id"], config["proxy_size"])
                result = await evaluate_image(client, key, config, proxy)
            except (OSError, ValueError) as exc:
                result = {
                    "status": "image_error",
                    "latency_ms": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if result.get("abort_run"):
                abort.set()
            columns = [
                "status",
                "output_json",
                "raw_response",
                "score",
                "usage_json",
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "cost_source",
                "latency_ms",
                "error",
            ]
            with store.db:
                store.db.execute(
                    f"""INSERT INTO results (run_id, position, {", ".join(columns)}, created_at)
                        VALUES ({", ".join("?" for _ in range(len(columns) + 3))})""",
                    (
                        run_id,
                        member["position"],
                        *(result.get(column) for column in columns),
                        now(),
                    ),
                )
            completed += 1
            progress(
                f"Run {run_id}: {completed}/{total} — image {member['position']}: "
                f"{result['status']}"
            )
            if result.get("error"):
                progress(f"  {result['error']}")
            if result.get("abort_run"):
                progress(
                    "Stopped: fix the authentication, credit or routing configuration "
                    "before starting another experiment."
                )

    try:
        async with httpx.AsyncClient(transport=transport) as client:
            tasks = [
                asyncio.create_task(worker(client))
                for _ in range(min(config["concurrency"], len(pending)))
            ]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
    except BaseException:
        with store.db:
            store.db.execute("UPDATE runs SET status = 'interrupted' WHERE id = ?", (run_id,))
        raise
    return finalize_run(store, run_id)


def finalize_run(store: Store, run_id: int) -> dict:
    total = store.db.execute(
        "SELECT COUNT(*) FROM run_members WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    errors = store.db.execute(
        "SELECT COUNT(*) FROM results WHERE run_id = ? AND status != 'ok'", (run_id,)
    ).fetchone()[0]
    recorded = store.db.execute(
        "SELECT COUNT(*) FROM results WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0]
    pending = total - recorded
    status = "failed" if pending else ("completed_with_errors" if errors else "completed")
    with store.db:
        store.db.execute(
            "UPDATE runs SET status = ?, finished_at = COALESCE(finished_at, ?) WHERE id = ?",
            (status, now(), run_id),
        )
    return {
        "run_id": run_id,
        "status": status,
        "images": total,
        "errors": errors,
        "pending": pending,
    }
