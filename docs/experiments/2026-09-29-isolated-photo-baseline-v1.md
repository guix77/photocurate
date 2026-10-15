# PhotoCurate — Experiment report: isolated-photo baseline v1

**Historical snapshot.** The proposed v2 work below was superseded by the
[target/prompt decisions and holdout](../JOURNAL.md). The business project is now closed;
see the [case-study summary](../../README.md). Original results and analysis are preserved.

**Date:** 2026-09-29  
**Scope:** evaluation of economical vision-language models (VLMs) for ranking personal photos for possible inclusion in a family photo album, judging each image in isolation.

## 1. Objective

The goal of this experiment is to test whether a low-cost VLM can reproduce a human annotator's judgement of **album-worthiness from visible image content alone**.

This experiment deliberately does **not** validate:

- selection among several photos of the same moment or burst;
- event clustering;
- personal significance inferred from family history;
- final album composition or layout;
- the effectiveness of an actual image-correction pipeline.

The repository implementation is intentionally non-agentic and minimal: deterministic indexing, proxy generation, annotation, model execution, and reporting.

## 2. Corpus and evaluation set

- Source corpus: **4,851 personal images**
- Evaluation set: **100 images**
- Sampling: random, fixed seed, persisted/frozen membership
- Proxy size: **512 px on the long edge**
- Human annotator: `guix`
- Human scale:
  - `0` — discard for album
  - `1` — weak / only useful for context
  - `2` — good album photo
  - `3` — strong / almost certain keep
- Final label distribution:
  - `0`: 26
  - `1`: 32
  - `2`: 28
  - `3`: 14

The first annotation pass was not used as the clean quality reference because the annotator naturally incorporated personal knowledge of people and memories. The labels were then reviewed using a stricter rule: **judge only what is visible, as if the people and personal context were unknown**.

## 3. Judgement convention

Model and human judgement use the same high-level convention:

- judge the visible image only;
- allow reasonable standard **non-generative** corrections such as crop, straightening, moderate exposure/colour correction, red-eye correction, mild denoise and sharpening;
- do not assume missing content can be reconstructed;
- do not assume severe blur or destroyed highlight/shadow detail can be recovered;
- do not invent identities, relationships, personal importance, or external context.

The baseline prompt asks the model for an integer `album_score` from 0 to 100 plus a short reason.

## 4. Runs and evidence identifiers

This report analyses three persisted experiment runs from the local PhotoCurate evaluation state:

| Run | Model | Purpose |
| --- | --- | --- |
| **2** | `openai/gpt-6-luna` | baseline candidate |
| **3** | `z-ai/glm-5.3-flash` | second-model comparison |
| **4** | `qwen/qwen3-vl-30b-a3b-instruct` | third-model comparison |

The generated raw comparison artifacts for these runs are kept in the private local state directory:

- `.photocurate/reports/runs-2-3-4.md`
- `.photocurate/reports/runs-2-3-4.json`
- `.photocurate/reports/runs-2-3-4.csv`

Individual run reports are also generated under `.photocurate/reports/`.

These raw artifacts, the SQLite state, proxies, and original photos are intentionally **not required to be versioned in Git**. This document is the versioned experiment analysis; run identifiers provide traceability back to the local persisted executions.

## 5. Models tested

All models were evaluated on the **same frozen 100-image set**, with the same judgement convention and 512 px proxies.

| Run | Model | Successful | Spearman | Cost / 100 | Cost / 1,000 | p50 latency | p95 latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | `openai/gpt-6-luna` | 99/100 | **0.647** | $0.018648 | **$0.1865** | 4.78 s | 9.04 s |
| 3 | `z-ai/glm-5.3-flash` | 100/100 | 0.616 | $0.029668 | $0.2967 | **4.36 s** | 9.47 s |
| 4 | `qwen/qwen3-vl-30b-a3b-instruct` | 100/100 | 0.565 | $0.021470 | $0.2147 | 4.59 s | 10.36 s |

Provider failure rate was 0% for all runs. Run 2 had one structured-output validation failure; runs 3 and 4 had none.

## 6. Top-K behaviour

| Model | Top-10 human 2/3 precision | Top-10 human 3 recall | Top-20 human 2/3 precision | Top-20 human 3 recall |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Luna | 60% | 21.4% | 75% | 50.0% |
| GLM 5.3 Flash | 80% | 35.7% | 75% | **57.1%** |
| Qwen3 VL 30B A3B | **90%** | **50.0%** | 70% | **57.1%** |

Interpretation:

- Luna has the strongest **global ranking agreement** with the human annotator.
- GLM performs better than Luna on the very top of the ranking, but produces many score ties.
- Qwen is strongest on Top-10 precision in this small sample, but its global ordering is weaker.
- Top-K results are still noisy at `n=100`, especially with ties at the decision boundary.

## 7. Manual disagreement analysis

A manual review was performed on several images where model scores diverged strongly from the human label or from each other.

### Image 12 — human `3` initially, reconsidered as possibly `2`

- Run 2 / Luna: 48
- Run 3 / GLM: 68
- Run 4 / Qwen: 67

The image shows a child watching swans. The annotator maintained that it belongs in an album, while noting glare affecting the scene and face, and the swans having their heads in the water.

Important finding: Luna's textual description did not correctly identify the scene, whereas GLM and Qwen did. This is a **visual-understanding failure**, not merely a scoring disagreement.

### Image 69 — human `0`

- Run 2 / Luna: 44
- Run 3 / GLM: 72
- Run 4 / Qwen: 75

Human judgement: blurred, poorly framed, and tilted.

GLM and Qwen strongly overvalued the fact that the image depicts an active childhood scene. Their reasons treated mild sharpening/correction as sufficient even though the human considered the blur unrecoverable for album use.

### Image 95 — human `0`

- Run 2 / Luna: 43
- Run 3 / GLM: 72
- Run 4 / Qwen: 75

Human judgement: blurred, therefore discard.

This reinforced the same pattern: GLM and Qwen under-penalised significant blur when the scene itself appeared emotionally or narratively interesting.

### Image 91 — human `0`

- Run 2 / Luna: 68
- Run 3 / GLM: 72
- Run 4 / Qwen: 85

The annotator considered `0` appropriate for album selection because two of three people were not looking at the camera.

The models broadly agreed that the image had positive expressions and group value, revealing that the baseline prompt underweights **subject engagement / eye contact** for posed or quasi-posed group photos.

### Image 97 — human `0`

- Run 2 / Luna: 68
- Run 3 / GLM: 72
- Run 4 / Qwen: 78

The image shows an adult changing a child. Neither person is looking at the camera.

The annotator rejected it because it is a functional/intimate care moment and not the kind of scene wanted in an album. All three models interpreted the same scene as warm family interaction.

This is not a missing-context problem: the relevant cues are visible. It is a mismatch between the generic prompt's notion of “family-album value” and the annotator's actual selection criteria.

### Image 77 — human `0`

- Run 2 / Luna: 67
- Run 3 / GLM: 72
- Run 4 / Qwen: 75

Human judgement: neither subject looks at the camera and the child is crying.

All three models again overvalued relational warmth and contextual narrative relative to facial expression and subject engagement.

### Image 41 — human `0`

- Run 2 / Luna: 65
- Run 3 / GLM: 62
- Run 4 / Qwen: 75

Human judgement: poor framing, insufficient contextual information, blurred foreground arm, and an unflattering result.

The models focused primarily on the joyful/expressive moment and underweighted framing quality and the visual impact of the blurred foreground element.

## 8. Error patterns identified

The manual review reveals recurring criteria that are visible in the image but insufficiently represented in the baseline prompt:

1. **Significant blur on the subject or an important foreground element should be strongly penalised.** Mild sharpening must not be treated as recovery from real blur.
2. For portrait or group-like images, **eye contact and subject engagement matter strongly**. If most subjects are disengaged from the camera, the image should usually be penalised unless the candid/action scene itself is exceptionally strong.
3. **Crying, visibly negative expressions, or unflattering poses** should receive a strong penalty for album selection.
4. **Functional or intimate care moments** may be poor album candidates even when they display warmth or family interaction.
5. **Framing must communicate enough information about the scene.** A technically recoverable image is not necessarily album-worthy.
6. Generic “emotional/narrative interest” is currently overweighted by all three models, especially GLM and Qwen.
7. Luna appears more conservative on technical defects, but it can still fail at scene understanding.

## 9. Interim conclusion

The experiment demonstrates that economical VLM inference is technically viable for this task:

- inference cost is far below $1 per 1,000 photos for all tested models;
- provider reliability is high;
- structured-output reliability is high;
- end-to-end latency is acceptable for an offline batch pipeline and can later be parallelised.

The main limitation is **not cost or infrastructure**. It is the definition and calibration of the judgement function.

Among the tested baseline models:

- **GPT-6 Luna** currently gives the best overall rank correlation (`0.647`) and the lowest cost;
- **GLM 5.3 Flash** gives stronger Top-K behaviour than Luna on this sample but tends to overvalue narrative scenes and quantise scores heavily;
- **Qwen3 VL 30B A3B** gives the best Top-10 precision but the weakest global rank correlation and the strongest tendency to infer contextual meaning beyond the strict visible-only requirement.

No model is selected as final at this stage.

## 10. Next experiment

Create a `standard_non_generative_v2` judgement convention that keeps the original visible-only and correction-potential rules, while explicitly encoding the recurrent album-selection criteria observed in the manual review:

- stronger penalty for unrecoverable blur;
- stronger treatment of eye contact / engagement in posed or group photos;
- stronger penalty for crying or unflattering expressions;
- lower value for merely functional/intimate care scenes;
- stronger requirement that framing communicates a useful, visually coherent moment.

The v1 runs remain preserved as a baseline.

The first v2 experiment should rerun **GPT-6 Luna only** on the same frozen 100-image set. If agreement improves materially and the reviewed failure cases are corrected without introducing new obvious errors, the same v2 prompt can then be tested on GLM and Qwen.

## 11. Reproducibility and evidence retained

The experiment is reproducible from repository state plus the private evaluation state:

- frozen evaluation-set membership;
- human labels and annotation history;
- persisted model/run configuration;
- exact prompt and structured-output schema;
- per-image model scores and reasons;
- provider/model routing metadata;
- latency, token usage, cost, and errors;
- generated Markdown/JSON/CSV reports for the persisted runs.

The versioned report should live in:

`docs/experiments/2026-09-29-isolated-photo-baseline-v1.md`

The local generated run artifacts remain under:

`.photocurate/reports/`

This separation keeps private source material and execution state out of Git while preserving a reviewable record of the experimental method, evidence, findings, and decisions.

The experiment therefore serves both as product validation and as evidence of an Applied AI / Solutions Architecture workflow: hypothesis definition, privacy-aware model selection, reproducible evaluation, quantitative comparison, qualitative error analysis, and evidence-driven iteration.
