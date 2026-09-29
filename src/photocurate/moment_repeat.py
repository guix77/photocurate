"""Two additional joint requests per preselected control; initial runs stay immutable."""

import json
import random
from pathlib import Path

import httpx

from .moments import digest, load_manifest, result_rows, verify_group, winner, write_json
from .provider import evaluate_group
from .store import dumps, now


def directory_for(directory: Path) -> Path:
    return directory / "runs" / "test" / "repeat"


def verify_inputs(directory: Path, run: dict) -> None:
    for relative, checksum in run["source_checksums"].items():
        if digest(directory / relative) != checksum:
            raise ValueError(f"Repeat source changed: {relative}")
    for group in run["groups"]:
        verify_group(group)


def prepare_repeat(directory: Path) -> dict:
    path = directory_for(directory) / "run.json"
    if path.exists():
        run = json.loads(path.read_text())
        verify_inputs(directory, run)
        return run
    source_path = directory / "runs" / "test" / "run.json"
    if not source_path.exists():
        raise ValueError("Finish the initial test before preparing repeats")
    source = json.loads(source_path.read_text())
    references = result_rows(directory, "test")
    expected = {
        f"isolated/{group['id']}/{version}"
        for group in source["groups"]
        for version in group["display_order"]
    } | {f"joint/{group['id']}" for group in source["groups"]}
    expected |= {f"order/{key}" for key in source["order_checks"]}
    if not expected <= references.keys():
        raise ValueError("Finish the initial test before preparing repeats")
    indexed = {group["id"]: group for group in source["groups"]}
    controls = list(source["order_checks"])
    if not controls:
        raise ValueError("The initial test has no preselected order controls")
    seed = load_manifest(directory)["seed"]
    rng = random.Random(f"{seed}:repeat-schedule")
    rng.shuffle(controls)
    # Balance which order goes first in the new session; never select by model outcome.
    operations = []
    for index, key in enumerate(controls):
        orders = ["original", "reversed"] if index % 2 == 0 else ["reversed", "original"]
        for order in orders:
            group = indexed[key]
            operations.append(
                {
                    "request_id": f"repeat/{key}/{order}",
                    "group_id": key,
                    "method": order,
                    "presentation_order": group[
                        "display_order" if order == "original" else "check_order"
                    ],
                }
            )
    relative_sources = ("manifest.json", "runs/test/run.json", "runs/test/results.jsonl")
    run = {
        "format_version": 1,
        "created_at": now(),
        "seed": seed,
        "source_checksums": {name: digest(directory / name) for name in relative_sources},
        "groups": [indexed[key] for key in controls],
        "human_retest": source["human_retest"],
        "references": {
            key: references[key]
            for group in controls
            for key in (f"joint/{group}", f"order/{group}")
        },
        "operations": operations,
    }
    verify_inputs(directory, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, run)
    return run


async def execute_repeat(directory: Path, key: str, transport=None, progress=print) -> dict:
    if not key.strip():
        raise ValueError("OPENROUTER_API_KEY is missing")
    run = prepare_repeat(directory)
    completed = result_rows(directory, "test/repeat")
    groups = {group["id"]: group for group in run["groups"]}
    path = directory_for(directory) / "results.jsonl"
    async with httpx.AsyncClient(transport=transport) as client:
        for operation in run["operations"]:
            if operation["request_id"] in completed:
                continue
            group = groups[operation["group_id"]]
            photos = {photo["version_id"]: photo for photo in group["photos"]}
            images = [
                (chr(65 + i), Path(photos[version]["proxy_path"]))
                for i, version in enumerate(operation["presentation_order"])
            ]
            result = await evaluate_group(client, key, group["joint_config"], images)
            row = {**result, **operation, "created_at": now()}
            with path.open("a", encoding="utf-8") as stream:
                stream.write(dumps(row) + "\n")
                stream.flush()
            completed[row["request_id"]] = row
            progress(f"{row['request_id']}: {row['status']}")
            if result.get("abort_run"):
                break
    return {
        "status": "completed" if len(completed) == len(run["operations"]) else "interrupted",
        "recorded_requests": len(completed),
        "errors": sum(row["status"] != "ok" for row in completed.values()),
    }


def compare(first: dict, second: dict | None, acceptable: set[int]) -> dict:
    valid = second is not None and first["status"] == second["status"] == "ok"
    a, b = winner(first), winner(second)
    return {
        "pending": second is None,
        "valid": valid,
        "first_winner": a,
        "second_winner": b,
        "same_image": a == b if valid else None,
        "first_acceptable": a in acceptable if a is not None else None,
        "second_acceptable": b in acceptable if b is not None else None,
    }


def summarize(pairs: list[dict]) -> dict:
    valid = [row for row in pairs if row["valid"]]
    same = sum(row["same_image"] for row in valid)
    return {
        "planned": len(pairs),
        "pending": sum(row["pending"] for row in pairs),
        "valid": len(valid),
        "same_image": same,
        "same_image_rate": same / len(valid) if valid else None,
        "acceptable_both": sum(
            row["first_acceptable"] and row["second_acceptable"] for row in valid
        ),
        "acceptable_to_unacceptable": sum(
            row["first_acceptable"] and not row["second_acceptable"] for row in valid
        ),
        "unacceptable_to_acceptable": sum(
            not row["first_acceptable"] and row["second_acceptable"] for row in valid
        ),
        "alternative_only_changes": sum(
            not row["same_image"] and row["first_acceptable"] and row["second_acceptable"]
            for row in valid
        ),
    }


def repeat_report(directory: Path) -> dict:
    path = directory_for(directory)
    if not (path / "run.json").exists():
        raise ValueError("Prepare repeats before reporting")
    run = json.loads((path / "run.json").read_text())
    verify_inputs(directory, run)
    results = result_rows(directory, "test/repeat")
    groups = []
    for group in run["groups"]:
        key = group["id"]
        a1, b1 = run["references"][f"joint/{key}"], run["references"][f"order/{key}"]
        a2, b2 = results.get(f"repeat/{key}/original"), results.get(f"repeat/{key}/reversed")
        acceptable = {group["human"]["preferred"], *group["human"]["alternatives"]}
        pairs = {
            "within_original": compare(a1, a2, acceptable),
            "within_reversed": compare(b1, b2, acceptable),
            "between_initial": compare(a1, b1, acceptable),
            "between_repeat": compare(a2, b2, acceptable)
            if a2 is not None
            else {"pending": True, "valid": False},
        }
        groups.append(
            {
                "group_id": key,
                "scope_changed": run["human_retest"].get(key, {}).get("status") == "out_of_scope",
                "pairs": pairs,
            }
        )
    names = ("within_original", "within_reversed", "between_initial", "between_repeat")
    complete_cases = [g for g in groups if all(p["valid"] for p in g["pairs"].values())]
    within = sum(
        g["pairs"][name]["same_image"]
        for g in complete_cases
        for name in ("within_original", "within_reversed")
    )
    between = sum(
        g["pairs"][name]["same_image"]
        for g in complete_cases
        for name in ("between_initial", "between_repeat")
    )
    rows = list(results.values())
    costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    report = {
        "complete": len(results) == len(run["operations"]),
        "groups": groups,
        "comparisons": {name: summarize([g["pairs"][name] for g in groups]) for name in names},
        "without_scope_changes": {
            name: summarize([g["pairs"][name] for g in groups if not g["scope_changed"]])
            for name in names
        },
        "complete_cases": {
            "groups": len(complete_cases),
            "within_order_agreement": within / (2 * len(complete_cases))
            if complete_cases
            else None,
            "between_order_agreement": between / (2 * len(complete_cases))
            if complete_cases
            else None,
        },
        "operations": {
            "requests": len(rows),
            "errors": sum(row["status"] != "ok" for row in rows),
            "cost_known_usd": sum(costs),
            "cost_coverage": len(costs),
            "cost_total_usd": sum(costs) if rows and len(costs) == len(rows) else None,
        },
        "limits": [
            "Small preselected control sample; descriptive diagnosis, "
            "not proof of an order effect.",
            "Pairs within a group are dependent; the unit remains the group.",
            "Same prompt, parameters and image mappings; provider routing and time may vary.",
            "Invalid historical responses remain invalid and are not replaced.",
            "Initial primary reports and frozen human choices remain unchanged.",
        ],
    }
    write_json(path / "report.json", report)
    lines = [
        "# Joint selection repeat diagnostic",
        "",
        f"Complete: {report['complete']}; new requests: {len(rows)}; "
        f"errors: {report['operations']['errors']}",
        "",
        "| Comparison | Same image / valid pairs | Pending |",
        "| --- | ---: | ---: |",
    ]
    for name, values in report["comparisons"].items():
        lines.append(f"| {name} | {values['same_image']}/{values['valid']} | {values['pending']} |")
    lines.extend(["", *report["limits"], ""])
    (path / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
