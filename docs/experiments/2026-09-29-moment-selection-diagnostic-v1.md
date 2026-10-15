# PhotoCurate — Same-moment selection diagnostic v1

**Final status (2026-10-15).** The analysis of existing responses below is complete.
The additional 20-request protocol was prepared but never executed and is discontinued.
The [business project is closed](../../README.md); original findings remain unchanged.
[Anonymized records and offline recalculation](../evidence/README.md) cover the initial test.

Date: 2026-09-29. Initial source: frozen `moments-v1` pilot and test responses.
This supplements the [initial protocol](2026-09-29-moment-selection-protocol-v1.md).
Initial reports and human labels remain unchanged. No new model call was made to
produce the analysis below.

## Initial result and interpretation

On 40 test groups, isolated Luna scoring selects a human-acceptable representative in
23 cases (57.5%), versus 27 (67.5%) for joint multi-image Luna selection. There are nine
joint-only successes and five isolated-only successes. The observed gain is +10 points,
with a group-bootstrap 95% interval of [-7.5, +27.5] points and exact McNemar p=0.424.
The pilot's five groups gave 80% isolated and 60% joint agreement.

Keep joint selection as a candidate. Its observed test gain is not demonstrated quality
superiority. Lower cost and latency apply to execution from scratch; reusing precomputed
isolated scores would change the marginal-cost comparison. Keep the initial prompts
and cohort frozen rather than tuning them on these results.

## Technical errors and scope sensitivity

Three isolated request errors occurred in two groups: g043 has two incomplete responses
(`finish_reason=length`), and g051 has one provider-filtered response
(`finish_reason=content_filter`). Joint selection is also human-incorrect on both groups.
Thus these errors do not account for the four additional joint successes.

The second human pass marked g030 out of scope, while the first pass accepted both of its
images. Both methods succeeded on this group under the original labels. It remains in
the frozen primary cohort; removing it is an explicitly supplementary sensitivity.

| Analysis | Groups | Isolated acceptable | Joint acceptable | Observed gain |
| --- | ---: | ---: | ---: | ---: |
| Frozen primary cohort | 40 | 23/40 (57.5%) | 27/40 (67.5%) | +10.0 points |
| Both methods without technical errors | 38 | 23/38 (60.5%) | 27/38 (71.1%) | +10.5 points |
| Excluding g030 | 39 | 22/39 (56.4%) | 26/39 (66.7%) | +10.3 points |
| Both restrictions | 37 | 22/37 (59.5%) | 26/37 (70.3%) | +10.8 points |

All four analyses retain nine joint-only and five isolated-only successes. They do not
resolve the primary uncertainty. Restricting to successful requests is a diagnostic,
not a replacement for operational performance where errors count as failures.

## What changes when order is reversed?

The ten preselected controls have nine valid reversed-order outputs; g010 is incomplete
(`finish_reason=length`). Across the nine valid comparisons, five retain the same image
and four change it. The changed outputs are not all interchangeable human alternatives:

| Group | Initial choice acceptable | Reversed choice acceptable | Interpretation |
| --- | --- | --- | --- |
| g040 | Yes | No | Harmful change |
| g053 | Yes | Yes | Equivalent acceptable alternative |
| g050 | Yes | No | Harmful change |
| g020 | No | Yes | Helpful change |

The full nine valid comparisons contain five acceptable-to-acceptable pairs, two harmful
changes, one helpful change and one unacceptable-to-unacceptable pair. Initial choices
are acceptable in 7/9, reversed choices in 6/9. Six of nine preserve acceptable versus
unacceptable status, while five of nine preserve exact image identity.

The six controls with a unique acceptable image retain identity in 3/6; the three with
equivalent acceptable alternatives retain identity in 2/3 and remain acceptable in 3/3.
Excluding g030 leaves 4/8 exact-image agreement, with 6/8 initially acceptable and 5/8
reversed acceptable. Consequently, alternatives and the scope change do not explain
away the observed instability. The controls are too few to estimate its prevalence
precisely, and a single reversal confounds order sensitivity with model variability.

## Minimal next diagnostic: twenty additional joint requests

Use exactly the ten controls already preselected for the initial test. Do not pick only
unstable or failed groups. For each group, keep the historical original-order output A1
and reversed-order output B1, then request:

- A2: one new request with the identical original order and aliases.
- B2: one new request with the identical reversed order and aliases.

This adds 20 joint requests. Keep the exact frozen comparative prompt, model parameters,
schema and existing 512 px proxies. There is no new isolated scoring, human annotation,
sample expansion, prompt tuning or album construction. A seeded schedule balances five
original-first and five reversed-first groups in the new session. The g030 control stays
in the technical sample; human-acceptability analyses also exclude its scope change.

Compare A1–A2 and B1–B2 for identical-order stability, and A1–B1 and A2–B2 for
between-order stability. Report valid-pair coverage and exact identity separately from
acceptable-choice transitions. Retain historical and new errors; do not substitute a
new answer for an invalid old answer. A group with an invalid historical response cannot
enter the four-response complete-case comparison. Repeated pairs are dependent; the
unit remains the group. Interpret the within/between comparison descriptively rather
than as a powered causal test. Time and provider routing may vary between sessions.

Prepare, execute manually, and report:

```sh
uv run photocurate moments repeat moments-v1 --prepare-only
uv run photocurate moments repeat moments-v1
uv run photocurate moments repeat-report moments-v1
```

Preparation makes no model request. Execution sends the control proxies through
OpenRouter to the model, as in the initial experiment; the user retains this execution.
New artifacts are separate in `.photocurate/moments/moments-v1/runs/test/repeat/`.
Source and proxy checksums are verified before execution and resume. The initial test
reports, responses, annotations and SQLite state remain unchanged.
