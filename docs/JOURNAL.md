# PhotoCurate — Development Journal

## 2026-09-29 — Isolated-photo VLM baseline

- Indexed corpus: 4,851 photos.
- Frozen evaluation set: 100 photos, seed 42.
- Initial human annotations were reviewed after identifying a methodology issue:
  the first pass implicitly incorporated personal knowledge of people and memories.
- Revised convention: judge album-worthiness from visible image content only.
- Three VLMs evaluated on the same frozen set:
  - GPT-6 Luna — run 2
  - GLM 5.3 Flash — run 3
  - Qwen3 VL 30B A3B — run 4
- Best overall rank correlation: GPT-6 Luna, Spearman 0.647.
- Manual disagreement analysis identified recurring failure patterns:
  insufficient penalty for blur, underweighting of eye contact and facial expression,
  and overvaluation of functional or intimate family scenes.
- Decision: do not select a final model yet.
  Define a `standard_non_generative_v2` judgement convention and rerun Luna
  on the same frozen evaluation set first.

→ [Detailed experiment report](experiments/2026-09-29-isolated-photo-baseline-v1.md)

## 2026-09-29 — Judgement-target decoupling and prompt simplification

- Simplified the human annotation instructions and the default VLM prompt while preserving the same semantic judgement target.
- Decoupled **judgement-target compatibility** from the exact wording of instructions and prompts.
- `standard_non_generative_v1` now identifies the semantic target:
  isolated-photo album-worthiness, visible content only, ordinary non-generative corrections, no assumed recovery of lost information.
- Human instruction text remains stored for provenance.
- VLM prompts are now independent experimental inputs and are persisted exactly as used.
- Custom prompts are not considered compatible with labelled data unless a judgement-target ID is explicitly declared.
- Removed automatic prompt prefixing.
- Existing 100 human labels and previous runs remain unchanged and compatible.
- No SQLite migration or reannotation required.
- Updated README and DESIGN documentation.
- Validation: 61 tests passing, Ruff and formatting checks passing.
- No paid VLM calls were made during this change.

Next step: run GPT-6 Luna again on the same frozen 100-photo evaluation set using the simplified built-in prompt, then compare it directly with run 2.

## 2026-09-29 — Human ground-truth correction after manual review

- Independent manual review of disagreements led to one ground-truth correction: image 12, `3 → 2`. It remains album-worthy, but glare affecting the scene/face and imperfect readability of the swans make `2` more appropriate than `3`.
- Images 69, 95, 91, 97, 77 and 41 were confirmed at `0`; no other labels changed. The correction uses the normal annotation storage mechanism, preserving history and provenance.
- Runs 2, 3, 4 and 5 remain immutable. No VLM run was relaunched and no paid VLM call was made.
- Future reports use the corrected human ground truth: `0: 26`, `1: 32`, `2: 29`, `3: 13`. Local comparisons for runs 2/3/4, 2/5 and 2/3/4/5 were regenerated from existing outputs.
- The [baseline v1 report](experiments/2026-09-29-isolated-photo-baseline-v1.md) remains deliberately unchanged as a historical snapshot of results before this correction.

## 2026-09-29 — Default prompt reviewed and frozen before holdout creation

- Reviewed and froze the default VLM prompt before creating the holdout to prevent tuning guided by holdout results.
- The opinionated album judgement emphasizes composition, technical quality and subject presentation while retaining the limits on non-generative corrections and lost information.
- Kept `standard_non_generative_v1`, human provenance and experimental prompts separate. Existing labels remain compatible and all past runs unchanged; no user settings or development-set-specific rule list was added.
- Validation: 61 tests, Ruff and formatting checks passed. No VLM run was launched and no paid VLM call was made.

## 2026-09-29 — Disjoint holdout sampling

- Added `--exclude-set` support to `sample create`.
- Holdout sampling can now exclude every photo already present in an existing evaluation set, including newer versions of the same underlying image.
- This enables creation of an independent holdout from the same indexed corpus without overlap with the development set.
- Updated CLI, sampling logic, tests, README and DESIGN.
- Validation: 66 tests passing, Ruff, formatting and `git diff --check` passing.
- Existing SQLite state remained unchanged.
- No holdout was created and no VLM call was made.

## 2026-09-29 — Independent holdout validation

- After freezing the prompt, created a random 100-photo holdout from the same corpus with seed 42 and zero media overlap with the 100-photo development set. All holdout annotations preceded the first holdout VLM run; labels retain `standard_non_generative_v1`.
- Run 8, GPT-6 Luna: 100/100 without error, Spearman 0.694, human 2/3 precision of 70% at Top-10 and 65% at Top-20, observed cost $0.1358 per 1,000 photos.
- Run 10, Qwen3 VL 30B A3B: 100/100 without error, Spearman 0.559, 60% at Top-10 and 50% at Top-20; faster but weaker agreement with the annotator.
- Run 9, GLM 5.3 Flash: interrupted after four stored responses, including two `finish_reason='length'` failures under default OpenRouter routing. No full-holdout quality conclusion is drawn for GLM.
- Retain GPT-6 Luna for the next isolated-photo stage. Only three holdout labels are `3`, making their recall unstable. These results validate a useful isolated-photo signal, not same-moment/burst selection or final album quality.
- Added the [holdout v1 report](experiments/2026-09-29-isolated-photo-holdout-v1.md). The baseline v1 historical report, code and existing evaluation state remain unchanged; documenting this milestone launched no new VLM call.

## 2026-09-29 — Paired same-moment selection experiment prepared

- Defined the next question: agreement with a human's choice of one representative,
  and the incremental value of joint multi-image Luna selection versus the validated
  isolated Luna scoring baseline, on exactly the same groups.
- Added the file-backed `moments prepare / annotate / run / report` workflow. It reads
  existing SQLite state in read-only mode, without migration or changes to old experiments.
- Prepared a seed-42 pool of 98 candidate days from complete 2–6-image temporal groups,
  excluding prior-set media and known days. Quotas: five accepted pilot groups and 40
  accepted test groups, with unused candidates replacing visual-coherence exclusions.
- Added a loopback gallery with preserved image framing, enlargement, preferred/equivalent
  choices, history, corrections and resumption. Human annotations precede both model methods;
  the gallery contains no model execution route or model outputs.
- Model runs freeze human labels, checksums, configuration, schemas and image/alias orders.
  Reports compare paired choices, uncertainty, simple references, costs and stability.
- Added the [selection protocol](experiments/2026-09-29-moment-selection-protocol-v1.md).
- Validation: 75 tests passed, including simulated paired evaluation, resume, input/schema
  preservation and the real local gallery HTTP workflow; Ruff and formatting checks passed.
- Opened the real pilot gallery without entering human choices. No paid VLM call was made.
  Existing SQLite counts remain 4,851 media, 200 labels, 308 label-history entries, nine runs
  and 727 results. Human annotation and empirical paired results are pending.

## 2026-09-29 — Human scope exclusion in the moment gallery

- Added “Groupe hors sujet” as a separate exclusion reason for moments outside the
  family-album scope. These groups stay in history, are counted separately in reports
  and are replaced from the reserve without consuming an accepted-group quota.
- Clarified the annotation instructions and protocol: this human scope filter precedes
  evaluation and does not test the model’s ability to reject irrelevant moments.
- Following the annotator’s explicit description, reclassified pilot group g001 (work
  presentation screens) from incoherent to out of scope, retaining the original history
  and all other choices. Updated the current instructions prospectively.
- Validation: all 11 moment tests passed, covering each exclusion reason, replacement,
  quota accounting, run filtering and report counts; Ruff and formatting checks passed.
  No model run or paid call was launched.

## 2026-09-29 — Moment annotations completed; external execution pending

- The annotator completed five pilot selections, 40 test selections and ten second-pass
  annotations. Nine repeated choices agree with an acceptable first-pass alternative;
  eight retain the exact preferred image. The remaining group, g030, was reclassified
  out of scope during the repeat; its cohort treatment awaits clarification.
- Prepared and froze the pilot inputs (five groups, 16 proxies) without external calls.
  The test remains unfrozen. A private execution review records input counts, human
  repeat consistency, both methods and the OpenRouter request destination.
- Automatic approval review rejected the external execution because explicit permission
  to transmit the photos through OpenRouter was missing. Requested that permission;
  no model request or paid call occurred.

## 2026-09-29 — Paired same-moment selection results and next diagnostic

- The annotator executed both frozen phases manually through `uv run photocurate moments`.
  The pilot has five groups (16 images): isolated Luna agreement 4/5, joint Luna 3/5,
  with no request errors. This small pilot is not used to establish superiority.
- On the 40-group test (116 images), isolated scoring selects an acceptable image in
  23/40 groups (57.5%), versus 27/40 (67.5%) for joint multi-image selection. Strict
  preferred-image agreement is 50.0% versus 62.5%. Paired counts: both correct 18,
  joint only 9, isolated only 5, both wrong 8.
- The observed gain is +10 percentage points, with group-bootstrap 95% interval
  [-7.5, +27.5] points and exact McNemar p=0.424. This is a favorable signal, not
  demonstrated superiority. Unique-human-choice groups show the same direction:
  16/31 acceptable isolated selections versus 19/31 joint selections.
- Test operations: 116 isolated requests, three errors, $0.000408 per group and
  median serial group latency 16.6 seconds; 40 joint requests, no errors,
  $0.000214 per group and median latency 5.0 seconds. These compare execution from
  scratch; already available isolated scores would have a different marginal cost.
  Ten additional order-check requests cost $0.002294 and include one invalid response.
- Nine human second-pass choices remain acceptable under the first-pass annotations
  (eight retain the exact preferred image). g030 was marked out of scope during the
  second pass but remains in the frozen 40-group test; retain the primary cohort and
  show an explicitly labelled sensitivity analysis excluding it.
- Reversed-order joint checks retain the exact image in 5/9 valid responses. This
  mixes order sensitivity and model variability; changing to another human-acceptable
  alternative must be distinguished from changing to an unacceptable image.
- Decision: retain the multi-image method as a candidate, keep prompts and the primary
  test frozen, and inspect stored responses before increasing sample size. First
  quantify acceptable-choice stability, paired agreement without technical-error groups,
  and the g030 sensitivity. If uncertainty remains, specify a small repeat experiment
  on the same ten preselected control groups, comparing repeated identical and reversed
  orders. The user retains execution of any new paid model requests.
- Validation before commit: all 77 tests passed; Ruff, formatting and whitespace checks
  passed. Private photos, annotations, frozen runs and provider responses remain Git-ignored.

## 2026-09-29 — Stored-response diagnostic and joint repeat control prepared

- Committed the initial implementation, protocol and result journal as `f3b7100` before
  continuing the diagnostic, as requested.
- Existing responses show the three isolated errors affect only two groups (g043 and
  g051), both also incorrect under joint selection. Restricting to fully valid methods
  gives 23/38 versus 27/38; excluding g030 gives 22/39 versus 26/39. Neither explains
  the observed gain or resolves its uncertainty.
- Of four valid image changes under reversed order, one is an equivalent acceptable
  alternative, two are harmful and one is helpful. Exact identity is stable in 5/9;
  acceptable/unacceptable status in 6/9. Excluding g030 gives 4/8 identity stability.
- Added the [stored-response analysis and repeat protocol](experiments/2026-09-29-moment-selection-diagnostic-v1.md).
  Keep the same ten preselected controls and add one original-order and one reversed-order
  joint request each, with balanced new-session order and exact frozen configuration.
- Added `moments repeat --prepare-only / repeat / repeat-report`. Separate artifacts,
  source/proxy checksums, resumable missing requests, retained errors and coverage prevent
  changes to the initial experiment. Reports distinguish identity stability from
  acceptable-choice transitions, including a sensitivity for second-pass scope changes.
- New paid requests remain user-executed with `uv run`; preparation and mock verification
  make no external model calls.
- Prepared the real repeat snapshot: ten controls, 20 requests, five original-first and
  five reversed-first groups. Verified frozen inputs and generated the pending repeat
  report; zero new requests are recorded.
- Validation: all 84 tests passed, including exact request preservation, resumability,
  historical/new error coverage, scope sensitivity and rejection of changed inputs before
  model calls. Ruff, formatting and whitespace checks passed.
