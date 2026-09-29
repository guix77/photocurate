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
