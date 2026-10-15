# Anonymized experiment evidence

This is a fixed export of existing results, prepared on 2026-10-15 after the business
project was closed. It introduces no new experiment or product behavior.

## Recalculate offline

From the repository root, with Python 3.12 or later:

```sh
python3 -B docs/evidence/recalculate.py
```

No dependency installation, API key, photos or private state is required. The script
reads the two JSON files beside it, reuses the repository's standard-library ranking
and percentile functions, recalculates the metrics and checks them against the recorded
reference with a small floating-point tolerance. It prints JSON and exits unsuccessfully
on a mismatch. It writes no files and makes no network request.

The application tests can be run separately with `uv run pytest`; those use synthetic
images and simulated provider responses. They verify pipeline behavior, not the empirical
model quality in this export.

## Contents and provenance

- [results.json](results.json): all 100 holdout members for each of runs 8, 9 and 10,
  plus all 40 initial same-moment test groups and their 166 recorded responses
  (116 isolated, 40 joint and ten initial reversed-order checks).
- [reference-metrics.json](reference-metrics.json): numerical reference values from the
  private holdout/test reports and the versioned stored-response diagnostic.
- [recalculate.py](recalculate.py): a single-purpose verifier for this snapshot.

The export was constructed by selecting allowed fields from SQLite opened in read-only
mode and existing frozen JSON/JSONL artifacts. The completed holdout metrics were checked
against the earlier generated report. Source code revision is recorded in `results.json`;
it identifies the implementation at export, not an immutable version of the hosted models.
Private-state file hashes were checked before and after export; the source state was unchanged.

Runs 8 and 10 have 100 valid outputs each. Run 9 retains two valid outputs, two validation
failures and 96 pending members. Pending is not an error or a successful request, and
run 9 is excluded from full-holdout quality metrics. All initial same-moment requests,
including failures, are retained. The separate 20-request repeat remains **unexecuted**.
The development set and five-group pilot are outside this export; their historical
reports remain available under [experiments](../experiments/2026-09-29-isolated-photo-baseline-v1.md).

## Fields and privacy boundary

`H001`-style identifiers are local to the holdout and preserve frozen sample order for
tie-breaking. Moment group identifiers such as `g030` are neutral experiment identifiers
already used in the analysis. Within each group, `A`–`F` identify photos in the initial
shuffled order; letters have no meaning across groups. Database media IDs are omitted.

The records contain human labels/targets, preferred and equivalent choices, second-pass
annotation status, numeric model scores, selected IDs, request status, finish reason,
token counts, provider name, reported cost and latency. `chronological_order` retains
relative ordering for the simple baseline without exporting capture dates.

`response_alias` is the alias returned by a valid comparative response;
`presentation_order` maps its `A`–`F` positions to the group's stable identifiers.
`selected_id` is the recovered stable identifier. Invalid responses have no published
selection. An isolated group with any failed request counts as a failed selection,
matching the initial protocol. Model errors remain visible as structured status codes.

Exact frozen model configurations are included: generic prompts, schemas, model IDs,
resolution, token budget, timeout, concurrency, routing preferences and declared targets.
The schema's `reason` field describes the original output contract; **no returned reason
is exported**. The export contains no images, free-text photo descriptions, raw responses,
free-text errors, filenames, paths, EXIF, capture dates, GPS, credentials, generation IDs
or image checksums. It does not include the original annotation histories.

ZDR/no-training is a recorded configuration requirement enforced externally by the
OpenRouter account/key. This export does not independently attest to those settings.

## What the recalculation verifies

- Holdout Spearman, Top-10/20 precision and strong-photo recall, boundary ties,
  request/error coverage, observed inference costs and latency percentiles.
- Same-moment acceptable and strict-preferred agreement, Wilson intervals, paired
  outcomes, group bootstrap interval (10,000 draws with the original seed), exact
  McNemar diagnostic, random and chronological references, and choice strata.
- Operational costs/serial group latency, initial order-check identity stability,
  human second-pass agreement, harmful/helpful/equivalent changes, and the supplementary
  error/scope sensitivity analyses. The original cohort remains the primary result.

The verifier is intentionally limited to this completed snapshot. It does not implement
a reusable exporter or evaluation framework. Rates are descriptive; the bootstrap unit
is the group/day, not an individual image. Reversed order also changes aliases and is
confounded with model variability. Inference costs exclude engineering, annotation,
storage, integration and other operating costs; per-1,000 figures are extrapolations.

The export makes arithmetic and protocol application inspectable. Without the photos,
reviewers cannot independently judge the human labels, visual coherence, or model visual
understanding, or rerun inference. Provider executions and sampling provenance remain
supported by the private traces and project records, rather than independently proven
by this anonymized subset. Future hosted-model calls need not reproduce historical outputs.
