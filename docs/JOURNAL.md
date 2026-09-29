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