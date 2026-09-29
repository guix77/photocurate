"""CLI entry point. Paths are supplied at runtime; credentials never enter experiment state."""

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from jsonschema.exceptions import SchemaError

from .annotate import annotate
from .convention import JUDGEMENT_TARGET_ID
from .media import index_corpus
from .provider import DEFAULT_PROMPT, DEFAULT_SCHEMA, parse_json
from .report import write_reports
from .runs import create_run, execute_run
from .sets import create_set, extend_set
from .store import Store, dumps


def positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Evaluate VLM judgement of isolated personal photos")
    cli.add_argument(
        "--state-dir",
        type=Path,
        default=Path(".photocurate"),
        help="local SQLite/proxies/reports directory (default: .photocurate)",
    )
    cli.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="API-key file (default: .env; environment takes precedence)",
    )
    commands = cli.add_subparsers(dest="command", required=True)

    index = commands.add_parser("index", help="recursively index a runtime source directory")
    index.add_argument("source", type=Path)
    index.add_argument("--proxy-size", type=positive, default=512)

    sample = commands.add_parser("sample", help="create, enlarge or inspect a frozen random set")
    sampling = sample.add_subparsers(dest="sample_command", required=True)
    create = sampling.add_parser("create")
    create.add_argument("name")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--size", type=positive, default=100)
    create.add_argument("--seed", type=int, default=42)
    extend = sampling.add_parser("extend")
    extend.add_argument("name")
    extend.add_argument("--size", type=positive, required=True, help="new total size")
    sampling.add_parser("list")
    show = sampling.add_parser("show")
    show.add_argument("name")

    label = commands.add_parser("annotate", help="label images in terminal, display in Preview")
    label.add_argument("name")
    label.add_argument("--annotator", required=True)
    label.add_argument("--review", action="store_true", help="revisit already labelled images")
    label.add_argument("--no-viewer", action="store_true")

    run = commands.add_parser("run", help="create and execute an OpenRouter experiment")
    run.add_argument("name", help="evaluation-set name")
    run.add_argument("--model", required=True, help="OpenRouter model ID; no default model")
    run.add_argument("--prompt", type=Path, help="UTF-8 prompt file; default is built in")
    run.add_argument(
        "--judgement-target",
        metavar="ID",
        help="explicit semantic target for --prompt; omitted means no agreement metrics",
    )
    run.add_argument("--schema", type=Path, help="JSON Schema file; default is built in")
    run.add_argument(
        "--score-field", default="album_score", help="numeric field; dot paths supported"
    )
    run.add_argument("--proxy-size", type=positive, default=512)
    run.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="optional sampling temperature; omitted by default for compatibility",
    )
    run.add_argument("--max-output-tokens", type=positive, default=512)
    run.add_argument("--timeout", type=float, default=60)
    run.add_argument("--concurrency", type=positive, default=1)
    run.add_argument("--input-usd-per-million", type=float)
    run.add_argument("--output-usd-per-million", type=float)
    run.add_argument(
        "--prepare-only",
        action="store_true",
        help="freeze a run without making paid requests or needing a key",
    )

    resume = commands.add_parser("resume", help="execute only missing results of an existing run")
    resume.add_argument("run_id", type=positive)
    commands.add_parser("runs", help="list experiments and their saved configurations")

    report = commands.add_parser("report", help="write agreement/cost/latency/disagreement reports")
    report.add_argument("run_ids", type=positive, nargs="+")
    report.add_argument("--top-k", type=positive, nargs="+", default=[10, 20])
    report.add_argument("--output-dir", type=Path)
    commands.add_parser("status", help="local corpus and experiment counts")
    return cli


def dispatch(args, store: Store):
    if args.command == "index":
        return index_corpus(store, args.source, args.proxy_size)
    if args.command == "sample":
        if args.sample_command == "create":
            return create_set(store, args.name, args.source, args.size, args.seed)
        if args.sample_command == "extend":
            return extend_set(store, args.name, args.size)
        if args.sample_command == "show":
            evaluation = store.get_set(args.name)
            return {
                "set": dict(evaluation),
                "members": [dict(row) for row in store.members(evaluation["id"])],
            }
        return [
            dict(row)
            for row in store.db.execute(
                """SELECT es.*, COUNT(em.position) AS size,
                      COUNT(hl.position) AS labelled
               FROM evaluation_sets es LEFT JOIN evaluation_members em ON em.set_id = es.id
               LEFT JOIN human_labels hl ON hl.set_id = em.set_id AND hl.position = em.position
               GROUP BY es.id ORDER BY es.id"""
            )
        ]
    if args.command == "annotate":
        return annotate(store, args.name, args.annotator, args.review, not args.no_viewer)
    if args.command in ("run", "resume"):
        load_dotenv(args.env_file, override=False)
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if (args.command == "resume" or not args.prepare_only) and not key:
            raise ValueError("OPENROUTER_API_KEY is missing; set it in .env or the environment")
        if args.command == "run":
            if args.judgement_target is not None and args.prompt is None:
                raise ValueError(
                    "--judgement-target requires --prompt; the built-in target is fixed"
                )
            target = args.judgement_target if args.prompt else JUDGEMENT_TARGET_ID
            config = {
                "model": args.model,
                "prompt": args.prompt.read_text(encoding="utf-8")
                if args.prompt
                else DEFAULT_PROMPT,
                "judgement_convention": {"id": target} if target is not None else None,
                "schema": parse_json(args.schema.read_text(encoding="utf-8"))
                if args.schema
                else DEFAULT_SCHEMA,
                "score_field": args.score_field,
                "proxy_size": args.proxy_size,
                "temperature": args.temperature,
                "max_tokens": args.max_output_tokens,
                "timeout_seconds": args.timeout,
                "concurrency": args.concurrency,
                "input_usd_per_million": args.input_usd_per_million,
                "output_usd_per_million": args.output_usd_per_million,
            }
            run_id = create_run(store, args.name, config)
            print(f"Created run {run_id}. Exact configuration and membership saved.")
            if args.prepare_only:
                return {"run_id": run_id, "status": "pending"}
        else:
            run_id = args.run_id
        return asyncio.run(execute_run(store, run_id, key))
    if args.command == "runs":
        rows = [
            dict(row)
            for row in store.db.execute(
                """SELECT r.*, es.name AS set_name, COUNT(rm.position) AS size
               FROM runs r JOIN evaluation_sets es ON es.id = r.set_id
               LEFT JOIN run_members rm ON rm.run_id = r.id GROUP BY r.id ORDER BY r.id"""
            )
        ]
        for row in rows:
            row["config"] = json.loads(row.pop("config_json"))
        return rows
    if args.command == "report":
        return write_reports(
            store, args.run_ids, args.top_k, args.output_dir or store.directory / "reports"
        )
    if args.command == "status":
        return {
            "state_dir": str(store.directory),
            "media": {
                row["status"]: row["count"]
                for row in store.db.execute(
                    "SELECT status, COUNT(*) AS count FROM media GROUP BY status"
                )
            },
            "evaluation_sets": store.db.execute("SELECT COUNT(*) FROM evaluation_sets").fetchone()[
                0
            ],
            "runs": store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
        }
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = None
    try:
        store = Store(args.state_dir)
        result = dispatch(args, store)
        print(dumps(result))
        if args.command in ("run", "resume") and result.get("errors", 0):
            return 1
        return 0
    except (ValueError, OSError, sqlite3.Error, SchemaError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Saved results can be resumed.", file=sys.stderr)
        return 130
    finally:
        if store is not None:
            store.close()
