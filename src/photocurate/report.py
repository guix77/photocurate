"""Annotator agreement, top-K utility and operational metrics; no automatic GO threshold."""

import csv
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean

from .store import Store, dumps, now


def ranks(values: list[float]) -> list[float]:
    """Average ranks for ties; deterministic with no dependency on scipy."""
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + 1 + end) / 2
        for i in ordered[start:end]:
            result[i] = rank
        start = end
    return result


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y):
        raise ValueError("Rank vectors must have equal length")
    if len(x) < 2:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = mean(rx), mean(ry)
    xx = sum((value - mx) ** 2 for value in rx)
    yy = sum((value - my) ** 2 for value in ry)
    if not xx or not yy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / math.sqrt(xx * yy)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    offset = (len(ordered) - 1) * q
    lower = int(offset)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (offset - lower)


def describe_run(store: Store, run_id: int, top_k: list[int]) -> dict:
    if not top_k or any(k < 1 for k in top_k):
        raise ValueError("Top-K values must be positive")
    run = store.get_run(run_id)
    config = json.loads(run["config_json"])
    evaluation = store.db.execute(
        "SELECT * FROM evaluation_sets WHERE id = ?", (run["set_id"],)
    ).fetchone()
    rows = [
        dict(row)
        for row in store.db.execute(
            """SELECT rm.position, rm.version_id, rm.proxy_path, m.source_path,
                  hl.label, hl.annotator, hl.updated_at AS label_updated_at,
                  hl.convention_json AS label_convention_json,
                  r.status, r.score, r.output_json, r.latency_ms, r.cost_usd, r.cost_source,
                  r.input_tokens, r.output_tokens, r.error
           FROM run_members rm
           JOIN media_versions v ON v.id = rm.version_id
           JOIN media m ON m.id = v.media_id
           JOIN runs run ON run.id = rm.run_id
           LEFT JOIN human_labels hl ON hl.set_id = run.set_id AND hl.position = rm.position
           LEFT JOIN results r ON r.run_id = rm.run_id AND r.position = rm.position
           WHERE rm.run_id = ? ORDER BY rm.position""",
            (run_id,),
        ).fetchall()
    ]
    for row in rows:
        label_convention = (
            json.loads(row["label_convention_json"]) if row["label_convention_json"] else None
        )
        matches = label_convention == config.get("judgement_convention")
        row["label_matches_convention"] = row["label"] is not None and matches

    def numerically_labelled(row):
        return row["label_matches_convention"] and row["label"] in ("0", "1", "2", "3")

    successes = [row for row in rows if row["status"] == "ok"]
    paired = [row for row in successes if numerically_labelled(row)]
    ordered = sorted(successes, key=lambda row: (-row["score"], row["position"]))
    numeric_labels = [row for row in rows if numerically_labelled(row)]
    strong_total = sum(row["label"] == "3" for row in numeric_labels)
    top_metrics = []
    for k in sorted(set(top_k)):
        selected = ordered[:k]
        judged = [row for row in selected if numerically_labelled(row)]
        strong = sum(row["label"] == "3" for row in judged)
        boundary = selected[-1]["score"] if selected else None
        tie_total = sum(row["score"] == boundary for row in ordered) if selected else 0
        tie_inside = sum(row["score"] == boundary for row in selected) if selected else 0
        top_metrics.append(
            {
                "k": k,
                "selected": len(selected),
                "numerically_labelled": len(judged),
                "unlabelled_or_unjudgeable": len(selected) - len(judged),
                "incompatible_labels": sum(
                    row["label"] is not None and not row["label_matches_convention"]
                    for row in selected
                ),
                "precision_human_2_or_3": (
                    sum(row["label"] in ("2", "3") for row in judged) / len(judged)
                    if judged
                    else None
                ),
                "human_3_found": strong,
                "human_3_total": strong_total,
                "recall_human_3": strong / strong_total if strong_total else None,
                "boundary_tie_crosses_k": tie_total > tie_inside,
                "boundary_tie_size": tie_total,
            }
        )
    ranked = ranks([row["score"] for row in successes])
    for row, rank in zip(successes, ranked, strict=True):
        row["rank_percentile"] = (rank - 1) / (len(successes) - 1) if len(successes) > 1 else 0.5
    bad_high = sorted(
        [row for row in paired if row["label"] == "0"],
        key=lambda row: (-row["rank_percentile"], row["position"]),
    )[:10]
    strong_low = sorted(
        [row for row in paired if row["label"] == "3"],
        key=lambda row: (row["rank_percentile"], row["position"]),
    )[:10]
    attempted = [row for row in rows if row["status"] is not None]
    inferred = [row for row in attempted if row["status"] != "image_error"]
    counts = Counter(row["status"] for row in attempted)
    latencies = [row["latency_ms"] for row in inferred]
    costs = [row["cost_usd"] for row in inferred if row["cost_usd"] is not None]
    all_costs_known = bool(inferred) and len(costs) == len(inferred)
    cost_total = sum(costs) if all_costs_known else None
    provider_errors = counts["provider_error"]
    validation_errors = counts["validation_error"]
    responses = counts["ok"] + validation_errors
    label_counts = dict(
        Counter(row["label"] if row["label"] is not None else "unlabelled" for row in rows)
    )
    return {
        "run_id": run_id,
        "set_name": evaluation["name"],
        "status": run["status"],
        "created_at": run["created_at"],
        "finished_at": run["finished_at"],
        "config": config,
        "membership": [
            {"position": row["position"], "version_id": row["version_id"]} for row in rows
        ],
        "current_set_size": len(store.members(run["set_id"])),
        "run_size": len(rows),
        "label_counts": label_counts,
        "annotators": sorted({row["annotator"] for row in rows if row["annotator"]}),
        "quality": {
            "paired_images": len(paired),
            "convention_mismatches": sum(
                row["label"] is not None and not row["label_matches_convention"] for row in rows
            ),
            "spearman": spearman(
                [int(row["label"]) for row in paired], [row["score"] for row in paired]
            ),
            "top_k": top_metrics,
            "human_0_highest": bad_high,
            "human_3_lowest": strong_low,
        },
        "operations": {
            "attempted": len(attempted),
            "pending": len(rows) - len(attempted),
            "status_counts": dict(counts),
            "inference_attempts": len(inferred),
            "provider_failure_rate": provider_errors / len(inferred) if inferred else None,
            "validation_failure_rate": validation_errors / responses if responses else None,
            "overall_failure_rate": (len(attempted) - len(successes)) / len(attempted)
            if attempted
            else None,
            "cost_known_usd": sum(costs) if costs else None,
            "cost_coverage_images": len(costs),
            "cost_total_usd": cost_total,
            "cost_per_inference_image_usd": cost_total / len(inferred) if all_costs_known else None,
            "cost_per_1000_images_usd": cost_total / len(inferred) * 1000
            if all_costs_known
            else None,
            "cost_sources": dict(Counter(row["cost_source"] or "unknown" for row in inferred)),
            "latency_ms": {
                "p50": percentile(latencies, 0.5),
                "p90": percentile(latencies, 0.9),
                "p95": percentile(latencies, 0.95),
                "mean": mean(latencies) if latencies else None,
            },
        },
        "images": rows,
    }


def number(value, digits=3) -> str:
    return "unknown" if value is None else f"{value:.{digits}f}"


def cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def image_link(path: str, label: str) -> str:
    return f"[{label}](<{Path(path)}>)"


def markdown(reports: list[dict]) -> str:
    lines = [
        "# Photo curation evaluation",
        "",
        "Quality measures agreement with the annotator on isolated photos. "
        "It is not universal ground truth about family photos. A v1 GO does not validate "
        "selection within moments/bursts or the quality of a final album.",
        "",
        "100 images are a starting point. Enlarge the same set before concluding when "
        "results are close or classes 0/3 are too rare. No automatic GO/NO-GO threshold.",
        "",
        "Metrics use labels current at report creation; exact labels and membership "
        "are included in the JSON and CSV exports.",
        "",
    ]
    if len(reports) > 1:
        same = all(
            report["set_name"] == reports[0]["set_name"]
            and report["membership"] == reports[0]["membership"]
            for report in reports[1:]
        )
        lines += [
            "Comparison membership: "
            + (
                "identical."
                if same
                else "**different — evaluate all compared models on the same enlarged set "
                "before drawing a conclusion.**"
            ),
            "",
        ]
        same_convention = all(
            report["config"].get("judgement_convention")
            == reports[0]["config"].get("judgement_convention")
            for report in reports[1:]
        )
        lines += [
            "Comparison judgement conventions: "
            + (
                "identical."
                if same_convention
                else "**different — agreement metrics do not measure the same judgement target.**"
            ),
            "",
        ]
    lines += [
        "| Run | Model | Images | Paired | Spearman | Cost USD | USD / 1,000 | p50 ms | Errors |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        op, quality = report["operations"], report["quality"]
        lines.append(
            f"| {report['run_id']} | {cell(report['config']['model'])} | "
            f"{report['run_size']} | {quality['paired_images']} | "
            f"{number(quality['spearman'])} | {number(op['cost_total_usd'], 6)} | "
            f"{number(op['cost_per_1000_images_usd'], 4)} | "
            f"{number(op['latency_ms']['p50'], 1)} | "
            f"{number(op['overall_failure_rate'])} |"
        )
    for report in reports:
        op, quality = report["operations"], report["quality"]
        lines += [
            "",
            f"## Run {report['run_id']}: {cell(report['config']['model'])}",
            "",
            f"Set: {cell(report['set_name'])}; run status: {report['status']}; "
            f"frozen size: {report['run_size']}; current set size: {report['current_set_size']}.",
            "",
            "Judgement convention: "
            + cell(
                (report["config"].get("judgement_convention") or {}).get(
                    "id", "unspecified (legacy)"
                )
            )
            + ". Recorded correction-potential conventions estimate what reasonable standard "
            "non-generative corrections could achieve; they do not validate a correction pipeline.",
            "",
            f"Labels: {cell(dumps(report['label_counts']))}. "
            f"Annotators: {cell(', '.join(report['annotators'])) or 'none'}.",
            "",
            f"Labels excluded for incompatible judgement conventions: "
            f"{quality['convention_mismatches']}. Stored label counts above include these labels.",
            "",
            f"Attempts: {op['attempted']}; pending: {op['pending']}; "
            f"outcomes: {cell(dumps(op['status_counts']))}.",
            "",
            f"Provider failure rate: {number(op['provider_failure_rate'])}; "
            f"validation failure rate: {number(op['validation_failure_rate'])}.",
            "",
            f"Cost coverage: {op['cost_coverage_images']}/{op['inference_attempts']} "
            f"inference attempts; known cost: {number(op['cost_known_usd'], 6)} USD. "
            f"Sources: {cell(dumps(op['cost_sources']))}. Missing costs remain unknown.",
            "",
            f"Latency (ms): p50 {number(op['latency_ms']['p50'], 1)}, "
            f"p90 {number(op['latency_ms']['p90'], 1)}, "
            f"p95 {number(op['latency_ms']['p95'], 1)}.",
            "",
            "Spearman uses successful outputs with numeric human labels; '?' is excluded. "
            "All agreement metrics require matching judgement conventions. "
            "Top-K is selected among all successful outputs before excluding unlabelled "
            "images, '?' labels or incompatible labels from its precision denominator. "
            "Recall includes all compatibly labelled "
            "3 images in the frozen run, including failed model evaluations.",
            "",
            "| K | Selected | Judged | Human 2/3 precision | Human 3 recall | Boundary tie |",
            "| ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for metric in quality["top_k"]:
            lines.append(
                f"| {metric['k']} | {metric['selected']} | "
                f"{metric['numerically_labelled']} | "
                f"{number(metric['precision_human_2_or_3'])} | "
                f"{number(metric['recall_human_3'])} | "
                f"{'yes' if metric['boundary_tie_crosses_k'] else 'no'} |"
            )
        lines += [
            "",
            "Score ties are broken by frozen sample position for Top-K; "
            "a tie crossing K makes those choices ambiguous.",
            "",
        ]
        for title, key in (
            ("Human 0, highest model ranks", "human_0_highest"),
            ("Human 3, lowest model ranks", "human_3_lowest"),
        ):
            lines += [
                f"### {title}",
                "",
                "| Image | Model score | Rank percentile | Proxy | Original | Reason |",
                "| ---: | ---: | ---: | --- | --- | --- |",
            ]
            for row in quality[key]:
                output = json.loads(row["output_json"]) if row["output_json"] else {}
                lines.append(
                    f"| {row['position']} | {row['score']} | "
                    f"{number(row['rank_percentile'])} | "
                    f"{image_link(row['proxy_path'], 'view')} | "
                    f"{image_link(row['source_path'], 'open')} | "
                    f"{cell(output.get('reason', dumps(output)))} |"
                )
            if not quality[key]:
                lines += ["", "No labelled examples available."]
        lines += [
            "",
            "These lists show the extremes in each human class for manual review; "
            "they do not apply a fixed major-error threshold.",
        ]
    return "\n".join(lines) + "\n"


def write_reports(store: Store, run_ids: list[int], top_k: list[int], destination: Path) -> dict:
    reports = [describe_run(store, run_id, top_k) for run_id in run_ids]
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stem = "runs-" + "-".join(str(run_id) for run_id in run_ids)
    md_path, json_path, csv_path = (
        destination / f"{stem}.{suffix}" for suffix in ("md", "json", "csv")
    )
    md_path.write_text(markdown(reports), encoding="utf-8")
    json_path.write_text(dumps({"generated_at": now(), "runs": reports}) + "\n", encoding="utf-8")
    columns = [
        "run_id",
        "position",
        "version_id",
        "source_path",
        "proxy_path",
        "label",
        "annotator",
        "label_updated_at",
        "label_convention_json",
        "label_matches_convention",
        "status",
        "score",
        "latency_ms",
        "cost_usd",
        "cost_source",
        "input_tokens",
        "output_tokens",
        "output_json",
        "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for report in reports:
            writer.writerows({"run_id": report["run_id"], **row} for row in report["images"])
    return {"markdown": str(md_path), "json": str(json_path), "csv": str(csv_path)}
