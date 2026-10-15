"""Recalculate the published snapshot offline; read no private state and write no files."""

import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from photocurate.report import percentile, spearman  # noqa: E402


def operations(rows):
    attempted = [row for row in rows if row["status"] != "pending"]
    counts = Counter(row["status"] for row in attempted)
    costs = [row["cost_usd"] for row in attempted if row["cost_usd"] is not None]
    total = sum(costs) if attempted and len(costs) == len(attempted) else None
    latencies = [row["latency_ms"] for row in attempted]
    responses = counts["ok"] + counts["validation_error"]
    return {
        "attempted": len(attempted),
        "pending": len(rows) - len(attempted),
        "status_counts": dict(counts),
        "inference_attempts": len(attempted),
        "provider_failure_rate": counts["provider_error"] / len(attempted) if attempted else None,
        "validation_failure_rate": counts["validation_error"] / responses if responses else None,
        "overall_failure_rate": (len(attempted) - counts["ok"]) / len(attempted)
        if attempted
        else None,
        "cost_known_usd": sum(costs) if costs else None,
        "cost_coverage_images": len(costs),
        "cost_total_usd": total,
        "cost_per_inference_image_usd": total / len(attempted) if total is not None else None,
        "cost_per_1000_images_usd": total / len(attempted) * 1000 if total is not None else None,
        "cost_sources": dict(Counter(row["cost_source"] or "unknown" for row in attempted)),
        "latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p90": percentile(latencies, 0.9),
            "p95": percentile(latencies, 0.95),
            "mean": sum(latencies) / len(latencies) if latencies else None,
        },
    }


def holdout(run):
    rows = run["images"]
    ops = operations(rows)
    if ops["pending"]:
        return {"operations": ops, "quality": None}
    valid = [row for row in rows if row["status"] == "ok"]
    paired = [
        row for row in valid if row["label_target"] == run["config"]["judgement_convention"]["id"]
    ]
    # Snapshot order preserves the original tie-break without revealing media identity.
    ordered = sorted(valid, key=lambda row: -row["score"])
    strong_total = sum(row["label"] == 3 for row in rows)
    top = []
    for k in (10, 20):
        selected = ordered[:k]
        strong = sum(row["label"] == 3 for row in selected)
        boundary = selected[-1]["score"]
        ties = sum(row["score"] == boundary for row in ordered)
        top.append(
            {
                "k": k,
                "selected": len(selected),
                "numerically_labelled": len(selected),
                "unlabelled_or_unjudgeable": 0,
                "incompatible_labels": 0,
                "precision_human_2_or_3": sum(row["label"] in (2, 3) for row in selected)
                / len(selected),
                "human_3_found": strong,
                "human_3_total": strong_total,
                "recall_human_3": strong / strong_total,
                "boundary_tie_crosses_k": ties > sum(row["score"] == boundary for row in selected),
                "boundary_tie_size": ties,
            }
        )
    return {
        "operations": ops,
        "quality": {
            "spearman": spearman(
                [row["label"] for row in paired], [row["score"] for row in paired]
            ),
            "top_k": top,
        },
    }


def wilson(correct, total):
    z = 1.95996398454
    p = correct / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [center - half, center + half]


def moments(experiment):
    groups = experiment["groups"]
    total = len(groups)
    outcomes, quality, ops = [], {}, {}
    for group in groups:
        acceptable = {group["human"]["preferred"], *group["human"]["alternatives"]}
        isolated = group["isolated"]
        iso = (
            max(isolated, key=lambda row: row["score"])["photo_id"]
            if all(row["status"] == "ok" for row in isolated)
            else None
        )
        joint = group["joint"]["selected_id"] if group["joint"]["status"] == "ok" else None
        outcomes.append({"isolated": iso, "joint": joint, "acceptable": acceptable})
    for method in ("isolated", "joint"):
        correct = sum(row[method] in row["acceptable"] for row in outcomes)
        quality[method] = {
            "correct": correct,
            "groups": total,
            "pending": 0,
            "agreement": correct / total,
            "wilson_95": wilson(correct, total),
            "strict_preferred_agreement": sum(
                row[method] == group["human"]["preferred"]
                for row, group in zip(outcomes, groups, strict=True)
            )
            / total,
        }
    for method, key in (("isolated", "isolated"), ("joint", "joint"), ("order", "order_check")):
        batches = [
            group[key] if method == "isolated" else [group[key]] for group in groups if group[key]
        ]
        rows = [row for batch in batches for row in batch]
        costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
        latencies = [sum(row["latency_ms"] for row in batch) for batch in batches]
        known = len(costs) == len(rows)
        ops[method] = {
            "requests": len(rows),
            "errors": sum(row["status"] != "ok" for row in rows),
            "cost_known_usd": sum(costs),
            "cost_coverage": len(costs),
            "cost_total_usd": sum(costs) if rows and known else None,
            "latency_total_ms": sum(row["latency_ms"] for row in rows),
            "cost_per_group_usd": sum(costs) / len(batches) if batches and known else None,
            "group_latency_ms": {
                "p50": percentile(latencies, 0.5),
                "p95": percentile(latencies, 0.95),
            },
        }
    counts = Counter(
        (row["isolated"] in row["acceptable"], row["joint"] in row["acceptable"])
        for row in outcomes
    )
    joint_only, isolated_only = counts[False, True], counts[True, False]
    discordant = joint_only + isolated_only
    differences = [
        int(row["joint"] in row["acceptable"]) - int(row["isolated"] in row["acceptable"])
        for row in outcomes
    ]
    rng = random.Random(f"{experiment['seed']}:paired-bootstrap")
    boot = [sum(rng.choices(differences, k=total)) / total for _ in range(10_000)]
    checks = [
        (group, row) for group, row in zip(groups, outcomes, strict=True) if group["order_check"]
    ]
    retests = [
        (group, row)
        for group, row in zip(groups, outcomes, strict=True)
        if group["human_retest"] and group["human_retest"]["status"] == "selected"
    ]
    strata = {}
    for name, unique in (("unique_human_choice", True), ("equivalent_alternatives", False)):
        subset = [row for row in outcomes if (len(row["acceptable"]) == 1) == unique]
        strata[name] = {
            "groups": len(subset),
            **{
                method: sum(row[method] in row["acceptable"] for row in subset) / len(subset)
                for method in ("isolated", "joint")
            },
        }
    primary = {
        "quality": quality,
        "paired": {
            "completed_groups": total,
            "both_correct": counts[True, True],
            "joint_only": joint_only,
            "isolated_only": isolated_only,
            "both_wrong": counts[False, False],
            "gain": (joint_only - isolated_only) / total,
            "bootstrap_95": [percentile(boot, 0.025), percentile(boot, 0.975)],
            "mcnemar_exact_p": min(
                1,
                2
                * sum(math.comb(discordant, i) for i in range(min(joint_only, isolated_only) + 1))
                / 2**discordant,
            ),
        },
        "operations": ops,
        "random_expected_agreement": sum(
            len(row["acceptable"]) / len(group["isolated"])
            for group, row in zip(groups, outcomes, strict=True)
        )
        / total,
        "first_chronological_agreement": sum(
            group["chronological_order"][0] in row["acceptable"]
            for group, row in zip(groups, outcomes, strict=True)
        )
        / total,
        "stability": {
            "order_checks_planned": len(checks),
            "order_checks_attempted": len(checks),
            "order_checks_valid": sum(
                group["order_check"]["status"] == "ok" for group, _ in checks
            ),
            "order_checks_stable": sum(
                group["order_check"]["status"] == "ok"
                and group["order_check"]["selected_id"] == row["joint"]
                for group, row in checks
            ),
            "human_retest_judged": len(retests),
            "human_retest_agreement": sum(
                group["human_retest"]["preferred"] in row["acceptable"] for group, row in retests
            )
            / len(retests),
        },
        "strata": strata,
    }
    sensitivity = {}
    for name, exclude_errors, exclude_scope in (
        ("primary", False, False),
        ("without_errors", True, False),
        ("without_scope_change", False, True),
        ("both_restrictions", True, True),
    ):
        subset = [
            row
            for group, row in zip(groups, outcomes, strict=True)
            if not (exclude_errors and (row["isolated"] is None or row["joint"] is None))
            and not (
                exclude_scope
                and group["human_retest"]
                and group["human_retest"]["status"] == "out_of_scope"
            )
        ]
        isolated = sum(row["isolated"] in row["acceptable"] for row in subset)
        joint = sum(row["joint"] in row["acceptable"] for row in subset)
        sensitivity[name] = {
            "groups": len(subset),
            "isolated_correct": isolated,
            "joint_correct": joint,
            "gain": (joint - isolated) / len(subset),
        }
    valid_checks = [(group, row) for group, row in checks if group["order_check"]["status"] == "ok"]
    transitions = Counter(
        (
            row["joint"] in row["acceptable"],
            group["order_check"]["selected_id"] in row["acceptable"],
        )
        for group, row in valid_checks
    )
    return primary, {
        "sensitivity": sensitivity,
        "order_transitions": {
            "valid_pairs": len(valid_checks),
            "acceptable_both": transitions[True, True],
            "harmful": transitions[True, False],
            "helpful": transitions[False, True],
            "unacceptable_both": transitions[False, False],
            "equivalent_alternative_changes": sum(
                row["joint"] != group["order_check"]["selected_id"]
                and row["joint"] in row["acceptable"]
                and group["order_check"]["selected_id"] in row["acceptable"]
                for group, row in valid_checks
            ),
        },
    }


def compare(actual, expected, path="metrics"):
    if isinstance(expected, dict):
        if actual.keys() != expected.keys():
            raise ValueError(f"{path}: different fields")
        for key in expected:
            compare(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(f"{path}: different lengths")
        for index, (a, b) in enumerate(zip(actual, expected, strict=True)):
            compare(a, b, f"{path}[{index}]")
    elif isinstance(expected, float):
        if actual is None or not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError(f"{path}: {actual!r} != {expected!r}")
    elif actual != expected:
        raise ValueError(f"{path}: {actual!r} != {expected!r}")


def main():
    directory = Path(__file__).resolve().parent
    data = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    expected = json.loads((directory / "reference-metrics.json").read_text(encoding="utf-8"))
    primary, diagnostic = moments(data["moments"])
    actual = {
        "holdout": {str(run["run_id"]): holdout(run) for run in data["holdout"]["runs"]},
        "moments": primary,
        "diagnostic": diagnostic,
    }
    compare(actual, expected)
    print(json.dumps(actual, indent=2, allow_nan=False))
    print(
        "Verified against the recorded metrics; no inference calls or private inputs.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
