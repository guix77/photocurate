"""Small, file-backed, paired experiment. The existing SQLite state is read-only."""

import hashlib
import json
import math
import random
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .provider import ENDPOINT, evaluate_group, evaluate_image, response_format, validate_config
from .report import percentile
from .store import dumps, now

TARGET = "moment_representative_v1"
INSTRUCTIONS = (
    "Choisis une seule photo pour représenter ce moment dans un album familial, "
    "uniquement d’après le contenu visible, sans connaissances personnelles. "
    "Juge le potentiel après corrections ordinaires non génératives, sans supposer "
    "que le flou, les détails perdus ou le contenu absent puissent être récupérés. "
    "Même si toutes les photos sont faibles, choisis la meilleure du groupe. "
    "Marque les alternatives qui conviendraient aussi bien : il s’agit de garder "
    "l’une ou l’autre, pas plusieurs photos. Signale les groupes hors sujet pour "
    "l’album familial, les groupes incohérents ou les détails illisibles au lieu "
    "de forcer un jugement."
)
JOINT_PROMPT = """Choose exactly one photo to represent this same moment in a family photo book.
Compare all the supplied photos. Use visible content only, without personal significance
or external context. Judge their potential after ordinary, non-generative corrections
that could reasonably be automated. Do not assume blur, lost detail or missing content
can be recovered. Assess composition, technical quality and subject presentation.
For photos centered on people, favor clear, flattering presentation and meaningful
engagement with the camera or scene. Direct eye contact matters for posed portraits,
but is not required for strong candid or action moments.
Even if all photos are weak, choose the strongest available representative.
Return only JSON: selected_id is the identifier of exactly one supplied photo;
reason briefly explains the comparative choice and any relevant uncertainty."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(dumps(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def experiment_dir(state_dir: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", name):
        raise ValueError("Experiment name must contain only letters, digits, '-' and '_'")
    return state_dir.expanduser().resolve() / "moments" / name


def load_manifest(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def read_history(directory: Path) -> list[dict]:
    path = directory / "annotations.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def labels(directory: Path, phase: str | None = None) -> dict[str, dict]:
    latest = {}
    for entry in read_history(directory):
        if phase is None or entry["phase"] == phase:
            latest[(entry["phase"], entry["group_id"])] = entry
    return {group_id: row for (_, group_id), row in latest.items()}


def temporal_groups(rows: list[dict], gap: int, span: int) -> list[list[dict]]:
    timed = []
    for row in rows:
        if row["capture_datetime"]:
            captured = datetime.fromisoformat(row["capture_datetime"])
            if captured.tzinfo is not None:
                timed.append((captured.astimezone(UTC), row))
    timed.sort(key=lambda item: (item[0], item[1]["media_id"]))
    components = []
    current = []
    for captured, row in timed:
        if current and (captured - current[-1][0]).total_seconds() > gap:
            components.append(current)
            current = []
        current.append((captured, row))
    if current:
        components.append(current)
    # Keep complete components. Never chop a burst to fit an image budget.
    return [
        [row for _, row in component]
        for component in components
        if 2 <= len(component) <= 6 and (component[-1][0] - component[0][0]).total_seconds() <= span
    ]


def gps_warning(rows: list[dict]) -> bool:
    coordinates = [
        (math.radians(row["latitude"]), math.radians(row["longitude"]))
        for row in rows
        if row["latitude"] is not None and row["longitude"] is not None
    ]
    for a, b in coordinates:
        for x, y in coordinates:
            h = math.sin((x - a) / 2) ** 2 + math.cos(a) * math.cos(x) * math.sin((y - b) / 2) ** 2
            if 12_742_000 * math.asin(min(1, math.sqrt(h))) > 100:
                return True
    return False


def prepare(
    state_dir: Path,
    name: str,
    source: Path,
    baseline_run: int,
    pilot_size: int = 5,
    test_size: int = 40,
    seed: int = 42,
    gap: int = 30,
    span: int = 120,
) -> dict:
    directory = experiment_dir(state_dir, name)
    if directory.exists():
        raise ValueError(f"Experiment already exists: {name}")
    if min(pilot_size, test_size, gap, span) < 1:
        raise ValueError("Sizes and temporal bounds must be positive")
    root = source.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Source must be a directory")
    database = state_dir.expanduser().resolve() / "state.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        run = connection.execute("SELECT * FROM runs WHERE id = ?", (baseline_run,)).fetchone()
        if run is None or run["status"] != "completed":
            raise ValueError("Baseline must be an existing completed isolated-photo run")
        baseline = json.loads(run["config_json"])
        validate_config(baseline)
        if baseline["proxy_size"] != 512 or baseline.get("endpoint", ENDPOINT) != ENDPOINT:
            raise ValueError("This experiment requires the existing 512 px OpenRouter baseline")
        rows = [
            dict(row)
            for row in connection.execute(
                """SELECT m.id media_id, m.source_path, v.id version_id, v.file_size,
                      v.mtime_ns, v.capture_datetime, v.latitude, v.longitude,
                      p.path proxy_path, p.width, p.height
               FROM media m JOIN media_versions v ON v.id = m.current_version_id
               LEFT JOIN proxies p ON p.version_id = v.id AND p.long_edge = 512
               WHERE m.status = 'indexed' ORDER BY m.source_path"""
            )
            if Path(row["source_path"]).is_relative_to(root)
        ]
        prior = list(
            connection.execute(
                """SELECT em.media_id, v.capture_datetime FROM evaluation_members em
               JOIN media_versions v ON v.id = em.version_id"""
            )
        )
    prior_ids = {row["media_id"] for row in prior}
    prior_days = {row["capture_datetime"][:10] for row in prior if row["capture_datetime"]}
    candidates = temporal_groups(rows, gap, span)
    by_day = defaultdict(list)
    exclusions = Counter()
    for group in candidates:
        if any(row["media_id"] in prior_ids for row in group):
            exclusions["prior_media"] += 1
            continue
        if any(row["capture_datetime"][:10] in prior_days for row in group):
            exclusions["prior_day"] += 1
            continue
        photos = []
        seen = set()
        try:
            for row in group:
                stat = Path(row["source_path"]).stat()
                if (stat.st_size, stat.st_mtime_ns) != (row["file_size"], row["mtime_ns"]):
                    raise ValueError("Changed original")
                checksum = digest(Path(row["proxy_path"]))
                if checksum not in seen:
                    photos.append({**row, "sha256": checksum})
                    seen.add(checksum)
            if len(photos) < 2:
                exclusions["identical_proxies"] += 1
                continue
        except (OSError, TypeError, ValueError):
            exclusions["missing_or_changed_image"] += 1
            continue
        day = group[0]["capture_datetime"][:10]
        by_day[day].append({"photos": photos, "gps_warning": gps_warning(group)})
    rng = random.Random(seed)
    days = sorted(by_day)
    rng.shuffle(days)
    pool = []
    for index, day in enumerate(days):
        group = rng.choice(by_day[day])
        order = [photo["version_id"] for photo in group["photos"]]
        rng.shuffle(order)
        pool.append({**group, "id": f"g{index + 1:03}", "day": day, "display_order": order})
    if len(pool) < pilot_size + test_size:
        raise ValueError(f"Need {pilot_size + test_size} distinct days, only {len(pool)} available")
    manifest = {
        "format_version": 1,
        "name": name,
        "created_at": now(),
        "source_root": str(root),
        "seed": seed,
        "target": TARGET,
        "human_instructions": INSTRUCTIONS,
        "baseline_run_id": baseline_run,
        "baseline_config": baseline,
        "joint_prompt": JOINT_PROMPT,
        "quotas": {"pilot": pilot_size, "test": test_size},
        "grouping": {
            "gap_seconds": gap,
            "max_span_seconds": span,
            "min_images": 2,
            "max_images": 6,
            "one_group_per_day": True,
            "exclude_prior_days": True,
        },
        "candidate_count": len(candidates),
        "exclusions": dict(exclusions),
        "groups": pool,
    }
    directory.mkdir(parents=True)
    write_json(directory / "manifest.json", manifest)
    return {
        "experiment_dir": str(directory),
        "candidate_days": len(pool),
        "pilot_size": pilot_size,
        "test_size": test_size,
        "baseline_run_id": baseline_run,
    }


def selected_labels(directory: Path, phase: str) -> list[dict]:
    return [row for row in labels(directory, phase).values() if row["status"] == "selected"]


def frozen(directory: Path, phase: str) -> bool:
    return (directory / "runs" / phase / "run.json").exists()


def order_checks(manifest: dict, selected: list[dict]) -> list[str]:
    ids = sorted(row["group_id"] for row in selected)
    random.Random(f"{manifest['seed']}:order-checks").shuffle(ids)
    return ids[:10]


def annotation_groups(directory: Path, manifest: dict, phase: str) -> list[dict]:
    if phase == "retest":
        if len(selected_labels(directory, "test")) != manifest["quotas"]["test"]:
            raise ValueError("Finish test annotations before the blind second pass")
        ids = order_checks(manifest, selected_labels(directory, "test"))
        return [group for group in manifest["groups"] if group["id"] in ids]
    if phase not in manifest["quotas"]:
        raise ValueError("Unknown annotation phase")
    if phase == "test" and len(selected_labels(directory, "pilot")) < manifest["quotas"]["pilot"]:
        raise ValueError("Finish pilot annotations first")
    visited = labels(directory, phase)
    groups = [group for group in manifest["groups"] if group["id"] in visited]
    if len(selected_labels(directory, phase)) < manifest["quotas"][phase]:
        assigned = set(labels(directory, "pilot")) | set(labels(directory, "test"))
        available = next((g for g in manifest["groups"] if g["id"] not in assigned), None)
        if available is None:
            raise ValueError("No unused candidate days remain; prepare a new experiment")
        groups.append(available)
    return groups


def save_annotation(directory: Path, phase: str, data: dict, annotator: str) -> None:
    manifest = load_manifest(directory)
    if not annotator.strip():
        raise ValueError("An annotator name is required")
    if frozen(directory, "test" if phase == "retest" else phase):
        raise ValueError("Annotations are frozen for this run")
    if phase == "pilot" and labels(directory, "test"):
        raise ValueError("Pilot annotations are locked after starting the test")
    allowed = {g["id"]: g for g in annotation_groups(directory, manifest, phase)}
    group = allowed.get(data.get("group_id"))
    if group is None:
        raise ValueError("Group does not belong to this annotation phase")
    status = data.get("status")
    if status not in ("selected", "out_of_scope", "heterogeneous", "unjudgeable"):
        raise ValueError("Unknown annotation status")
    preferred = data.get("preferred")
    alternatives = data.get("alternatives", [])
    if not isinstance(alternatives, list):
        raise ValueError("Alternatives must be a list")
    ids = {photo["version_id"] for photo in group["photos"]}
    if status == "selected":
        chosen = [preferred, *alternatives]
        if any(type(value) is not int or value not in ids for value in chosen):
            raise ValueError("Choose only photos belonging to this group")
        if len(chosen) != len(set(chosen)):
            raise ValueError("Duplicate photo choices")
    elif preferred is not None or alternatives:
        raise ValueError("Excluded groups must have no photo selection")
    entry = {
        "group_id": group["id"],
        "phase": phase,
        "status": status,
        "preferred": preferred,
        "alternatives": alternatives,
        "annotator": annotator,
        "created_at": now(),
        "target": TARGET,
        "instructions": manifest["human_instructions"],
    }
    with (directory / "annotations.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(dumps(entry) + "\n")
        stream.flush()


def joint_config(baseline: dict, prompt: str, count: int) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "selected_id": {"type": "string", "enum": list("ABCDEF"[:count])},
            "reason": {"type": "string"},
        },
        "required": ["selected_id", "reason"],
        "additionalProperties": False,
    }
    return {
        **baseline,
        "prompt": prompt,
        "score_field": None,
        "judgement_convention": {"id": TARGET},
        "model_context": "multiple_images",
        "schema": schema,
        "response_format": response_format(schema),
    }


def verify_group(group: dict) -> None:
    for photo in group["photos"]:
        if digest(Path(photo["proxy_path"])) != photo["sha256"]:
            raise ValueError(f"Frozen proxy changed for version {photo['version_id']}")


def prepare_run(directory: Path, phase: str, prompt: str | None = None) -> dict:
    if phase not in ("pilot", "test"):
        raise ValueError("Runs use pilot or test annotations")
    path = directory / "runs" / phase / "run.json"
    if path.exists():
        if prompt is not None:
            raise ValueError("Existing run is frozen; cannot replace its prompt")
        return json.loads(path.read_text())
    manifest = load_manifest(directory)
    joint_prompt = prompt if prompt is not None else manifest["joint_prompt"]
    if not joint_prompt.strip():
        raise ValueError("Joint prompt must not be empty")
    selected = selected_labels(directory, phase)
    if len(selected) != manifest["quotas"][phase]:
        raise ValueError(
            f"Finish all {manifest['quotas'][phase]} {phase} selections before running Luna"
        )
    indexed = {group["id"]: group for group in manifest["groups"]}
    groups = [{**indexed[row["group_id"]], "human": row} for row in selected]
    for group in groups:
        verify_group(group)
    checks = order_checks(manifest, selected) if phase == "test" else []
    for group in groups:
        group["check_order"] = list(reversed(group["display_order"]))
        group["joint_config"] = joint_config(
            manifest["baseline_config"], joint_prompt, len(group["photos"])
        )
    run = {
        "created_at": now(),
        "phase": phase,
        "target": TARGET,
        "baseline_run_id": manifest["baseline_run_id"],
        "baseline": manifest["baseline_config"],
        "joint_prompt": joint_prompt,
        "groups": groups,
        "order_checks": checks,
        "human_retest": labels(directory, "retest"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, run)
    return run


def result_rows(directory: Path, phase: str) -> dict[str, dict]:
    path = directory / "runs" / phase / "results.jsonl"
    return (
        {
            row["request_id"]: row
            for row in (json.loads(line) for line in path.read_text().splitlines())
        }
        if path.exists()
        else {}
    )


async def execute(
    directory: Path, phase: str, key: str, prompt: str | None = None, transport=None, progress=print
) -> dict:
    if not key.strip():
        raise ValueError("OPENROUTER_API_KEY is missing")
    run = prepare_run(directory, phase, prompt)
    completed = result_rows(directory, phase)
    path = directory / "runs" / phase / "results.jsonl"
    # Verify every input before the first paid request, including on resume.
    for group in run["groups"]:
        verify_group(group)
    async with httpx.AsyncClient(transport=transport) as client:
        for group in run["groups"]:
            photos = {photo["version_id"]: photo for photo in group["photos"]}
            requests = [
                (f"isolated/{group['id']}/{version}", "isolated", [version])
                for version in group["display_order"]
            ]
            requests.append((f"joint/{group['id']}", "joint", group["display_order"]))
            if group["id"] in run["order_checks"]:
                requests.append((f"order/{group['id']}", "order", group["check_order"]))
            for request_id, method, order in requests:
                if request_id in completed:
                    continue
                if method == "isolated":
                    result = await evaluate_image(
                        client, key, run["baseline"], Path(photos[order[0]]["proxy_path"])
                    )
                else:
                    config = group["joint_config"]
                    images = [
                        (chr(65 + i), Path(photos[version]["proxy_path"]))
                        for i, version in enumerate(order)
                    ]
                    result = await evaluate_group(client, key, config, images)
                row = {
                    **result,
                    "request_id": request_id,
                    "group_id": group["id"],
                    "method": method,
                    "presentation_order": order,
                    "created_at": now(),
                }
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(dumps(row) + "\n")
                    stream.flush()
                completed[request_id] = row
                progress(f"{phase} {request_id}: {result['status']}")
                if result.get("abort_run"):
                    return {
                        "status": "interrupted",
                        "recorded_requests": len(completed),
                        "errors": sum(r["status"] != "ok" for r in completed.values()),
                    }
    return {
        "status": "completed",
        "recorded_requests": len(completed),
        "errors": sum(r["status"] != "ok" for r in completed.values()),
    }


def wilson(correct: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.95996398454
    p = correct / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [center - half, center + half]


def winner(row: dict | None) -> int | None:
    if row is None or row["status"] != "ok":
        return None
    identifier = json.loads(row["output_json"])["selected_id"]
    return row["presentation_order"][ord(identifier) - 65]


def paired_report(directory: Path, phase: str) -> dict:
    manifest = load_manifest(directory)
    path = directory / "runs" / phase / "run.json"
    if not path.exists():
        raise ValueError("Prepare or execute this phase before reporting")
    run = json.loads(path.read_text())
    results = result_rows(directory, phase)
    groups = []
    for group in run["groups"]:
        human = group["human"]
        acceptable = {human["preferred"], *human["alternatives"]}
        isolated = [
            results.get(f"isolated/{group['id']}/{version}") for version in group["display_order"]
        ]
        iso_pending = any(row is None for row in isolated)
        iso_ok = not iso_pending and all(row["status"] == "ok" for row in isolated)
        tied = []
        if iso_ok:
            best_score = max(row["score"] for row in isolated)
            tied = [row["presentation_order"][0] for row in isolated if row["score"] == best_score]
        iso_winner = tied[0] if tied else None
        joint = results.get(f"joint/{group['id']}")
        joint_winner = winner(joint)
        check = results.get(f"order/{group['id']}")
        retest = run["human_retest"].get(group["id"])
        groups.append(
            {
                "group_id": group["id"],
                "image_count": len(group["photos"]),
                "acceptable": sorted(acceptable),
                "isolated_winner": iso_winner,
                "isolated_ties": tied,
                "joint_winner": joint_winner,
                "isolated_pending": iso_pending,
                "joint_pending": joint is None,
                "isolated_correct": iso_winner in acceptable,
                "joint_correct": joint_winner in acceptable,
                "isolated_strict_correct": iso_winner == human["preferred"],
                "joint_strict_correct": joint_winner == human["preferred"],
                "first_chronological_correct": group["photos"][0]["version_id"] in acceptable,
                "chance": len(acceptable) / len(group["photos"]),
                "order_check_attempted": check is not None,
                "order_check_valid": check is not None and check["status"] == "ok",
                "order_check_stable": check is not None
                and winner(check) is not None
                and winner(check) == joint_winner,
                "human_retest_correct": retest["preferred"] in acceptable
                if retest and retest["status"] == "selected"
                else None,
            }
        )
    total = len(groups)
    quality = {}
    for method in ("isolated", "joint"):
        pending = sum(row[f"{method}_pending"] for row in groups)
        correct = sum(row[f"{method}_correct"] for row in groups)
        quality[method] = {
            "correct": correct,
            "groups": total,
            "pending": pending,
            "agreement": correct / total if total and not pending else None,
            "wilson_95": wilson(correct, total) if not pending else None,
            "strict_preferred_agreement": sum(row[f"{method}_strict_correct"] for row in groups)
            / total
            if not pending
            else None,
        }
    complete = not any(row["isolated_pending"] or row["joint_pending"] for row in groups)
    paired_groups = [
        row for row in groups if not row["isolated_pending"] and not row["joint_pending"]
    ]
    counts = Counter((row["isolated_correct"], row["joint_correct"]) for row in paired_groups)
    joint_only, isolated_only = counts[False, True], counts[True, False]
    discordant = joint_only + isolated_only
    differences = [int(row["joint_correct"]) - int(row["isolated_correct"]) for row in groups]
    rng = random.Random(f"{manifest['seed']}:paired-bootstrap")
    boot = (
        [sum(rng.choices(differences, k=total)) / total for _ in range(10_000)] if complete else []
    )
    operations = {}
    for method in ("isolated", "joint", "order"):
        rows = [row for row in results.values() if row["method"] == method]
        known = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
        group_latencies = [
            sum(row["latency_ms"] for row in rows if row["group_id"] == group["group_id"])
            for group in groups
            if any(row["group_id"] == group["group_id"] for row in rows)
        ]
        method_complete = complete if method != "order" else len(rows) == len(run["order_checks"])
        operations[method] = {
            "requests": len(rows),
            "errors": sum(row["status"] != "ok" for row in rows),
            "cost_known_usd": sum(known),
            "cost_coverage": len(known),
            "cost_total_usd": sum(known) if rows and len(known) == len(rows) else None,
            "latency_total_ms": sum(row["latency_ms"] for row in rows),
            "cost_per_group_usd": sum(known) / len(group_latencies)
            if method_complete and group_latencies and len(known) == len(rows)
            else None,
            "group_latency_ms": {
                "p50": percentile(group_latencies, 0.5),
                "p95": percentile(group_latencies, 0.95),
            },
        }
    checks = [row for row in groups if row["order_check_attempted"]]
    retests = [row for row in groups if row["human_retest_correct"] is not None]
    strata = {}
    for name, subset in (
        ("unique_human_choice", [row for row in groups if len(row["acceptable"]) == 1]),
        ("equivalent_alternatives", [row for row in groups if len(row["acceptable"]) > 1]),
    ):
        strata[name] = {
            "groups": len(subset),
            **{
                method: sum(row[f"{method}_correct"] for row in subset) / len(subset)
                if subset and complete
                else None
                for method in ("isolated", "joint")
            },
        }
    report = {
        "phase": phase,
        "target": TARGET,
        "baseline_run_id": run["baseline_run_id"],
        "complete": complete,
        "groups": groups,
        "quality": quality,
        "paired": {
            "completed_groups": len(paired_groups),
            "both_correct": counts[True, True],
            "joint_only": joint_only,
            "isolated_only": isolated_only,
            "both_wrong": counts[False, False],
            "gain": (joint_only - isolated_only) / total if complete and total else None,
            "bootstrap_95": [percentile(boot, 0.025), percentile(boot, 0.975)] if boot else None,
            "mcnemar_exact_p": min(
                1,
                2
                * sum(math.comb(discordant, i) for i in range(min(joint_only, isolated_only) + 1))
                / 2**discordant,
            )
            if complete and discordant
            else None,
        },
        "random_expected_agreement": sum(row["chance"] for row in groups) / total,
        "first_chronological_agreement": sum(row["first_chronological_correct"] for row in groups)
        / total,
        "operations": operations,
        "strata": strata,
        "stability": {
            "order_checks_planned": len(run["order_checks"]),
            "order_checks_attempted": len(checks),
            "order_checks_valid": sum(row["order_check_valid"] for row in checks),
            "order_checks_stable": sum(row["order_check_stable"] for row in checks),
            "human_retest_judged": len(retests),
            "human_retest_agreement": sum(row["human_retest_correct"] for row in retests)
            / len(retests)
            if retests
            else None,
        },
        "annotation_exclusions": dict(
            Counter(
                row["status"]
                for row in labels(directory, phase).values()
                if row["status"] != "selected"
            )
        ),
        "limits": [
            "One corpus and annotator; small complete groups with known timezone.",
            "One representative only; no rejection or multi-photo retention evaluation.",
            "Bootstrap and paired counts use days/groups, not individual photos.",
            "Isolated score ties use the frozen shuffled presentation order.",
            "Order checks also include model nondeterminism.",
        ],
    }
    output = directory / "runs" / phase / "report.json"
    write_json(output, report)
    lines = [
        f"# Same-moment selection — {phase}",
        "",
        f"Groups: {total}; complete: {complete}",
        "",
        "| Method | Correct | Agreement |",
        "| --- | ---: | ---: |",
    ]
    for method, metrics in quality.items():
        agreement = f"{metrics['agreement']:.1%}" if metrics["agreement"] is not None else "pending"
        lines.append(f"| {method} | {metrics['correct']}/{total} | {agreement} |")
    lines.extend(
        [
            "",
            f"Joint only: {joint_only}; isolated only: {isolated_only}.",
            "",
            "Errors count as failed selections. Pending groups do not yield a final agreement.",
            "",
            "Configuration, frozen human labels and group details: run.json / report.json.",
        ]
    )
    (output.parent / "report.md").write_text("\n".join(lines) + "\n")
    return report
