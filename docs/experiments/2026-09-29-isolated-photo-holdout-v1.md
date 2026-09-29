# PhotoCurate — Experiment report: isolated-photo holdout v1

**Date:** 2026-09-29  
**Decision:** retain **GPT-6 Luna** for the next isolated-photo stage.

## 1. From development to independent validation

The original `baseline` set of 100 photos served as the development set for comparing models, reviewing disagreements and revising the prompt. Its results alone could not establish generalisation to unseen photos.

The default prompt was reviewed and frozen before creating `holdout`, avoiding prompt tuning guided by holdout results. The new set contains **100 randomly selected photos** from the same indexed corpus of **4,851 images**, with seed **42** and persisted membership. Creation used `--exclude-set baseline`; SQLite confirms **zero overlapping media identities**, including across image versions. No images were manually selected.

The annotator `guix` labelled all 100 holdout photos before any VLM run on this set. The last annotation-history entry is at **10:02:11 UTC**, preceding creation of the first holdout run, run 8, at **10:02:45 UTC**. Human instruction text and annotation history remain preserved for provenance.

| Human label | Development set, current labels | Holdout |
| --- | ---: | ---: |
| 0 — discard | 26 | 36 |
| 1 — weak / context only | 32 | 33 |
| 2 — good album photo | 29 | 28 |
| 3 — strong / almost certain keep | 13 | 3 |
| Total | 100 | 100 |

The development distribution includes the previously documented human correction of image 12 from `3` to `2`. The [baseline v1 report](2026-09-29-isolated-photo-baseline-v1.md) remains unchanged as a historical snapshot taken before that correction.

## 2. Method and persisted runs

The semantic judgement target remains **`standard_non_generative_v1`**: assess album-worthiness of a photo in isolation, using visible content only and no personal knowledge or external context. Allow ordinary, non-generative corrections that could reasonably be automated, without assuming recovery of blur, lost detail or missing content. Human labels measure agreement with this annotator, not a universal truth about family photos.

The frozen prompt emphasizes composition, technical quality and subject presentation. It favors clear, flattering presentation and meaningful engagement for photos centered on people, while allowing strong candid and action moments without direct eye contact. It contains no list of rules specific to manually reviewed development images.

Runs 8, 9 and 10 share the exact same prompt and configuration except for model ID: 512 px proxies, an integer `album_score` from 0 to 100 with a short reason in strict JSON, `max_tokens=512`, concurrency 1, a 60-second timeout and no explicit temperature. Each declares the same semantic target as all holdout labels. OpenRouter used default routing with `require_parameters=true`; no provider was pinned. ZDR and no training are enforced by the account/key configuration.

| Run | Model | Status | Actual providers recorded |
| --- | --- | --- | --- |
| 8 | `openai/gpt-6-luna` | completed | Azure: 100 |
| 9 | `z-ai/glm-5.3-flash` | interrupted | Together: 4 |
| 10 | `qwen/qwen3-vl-30b-a3b-instruct` | completed | DeepInfra: 63; Novita: 37 |

## 3. Holdout results

Agreement metrics for the completed runs cover all 100 images, with no incompatible or unjudgeable labels. Top-K precision is the fraction of selected photos labelled `2` or `3` by the human annotator. Spearman measures rank agreement between model scores and the ordinal human labels.

| Run / model | Successful outputs | Spearman | Top-10 human 2/3 | Top-20 human 2/3 | Observed total cost | Cost / 1,000 photos | p50 latency | p95 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 / GPT-6 Luna | **100/100** | **0.694** | **70% (7/10)** | **65% (13/20)** | $0.0135754 | **$0.1358** | 4.98 s | 8.89 s |
| 10 / Qwen3 VL 30B A3B | **100/100** | 0.559 | 60% (6/10) | 50% (10/20) | $0.01444015 | $0.1444 | **3.39 s** | **5.88 s** |

Both completed runs had **zero provider or structured-output validation errors**. Costs are provider-reported for all 100 requests; the per-1,000 figure extrapolates the observed per-photo cost, rather than representing a separate 1,000-photo experiment. Latencies describe these executions under their observed routing.

**Luna generalises best on this holdout among the completed candidates:** it leads on global rank agreement and both Top-K precision measures, while completing every image without error. Qwen is faster, but its ranking and selection agree less well with the human labels. These are observed differences on one holdout, not a claim of statistical significance or universal model superiority.

### GLM: repeated operational failure

Run 9 was interrupted after reproducing the length-completion failure previously seen in development run 6. Only **4 of 100** images have stored results: **2 successful outputs**, **2 validation errors**, and **96 pending**. Both failures report `finish_reason='length'` and `native_finish_reason='length'` with **512 completion tokens**, exhausting the configured budget.

The saved usage records show substantial reasoning consumption. One failed response reports **512 reasoning tokens** and no final content; the other reports **427 reasoning tokens** and incomplete final content. All four responses came from Together under default OpenRouter routing. This establishes an operational failure of the tested configuration; it does not isolate prompt effects from provider or reasoning behavior, nor establish that every possible GLM configuration would fail.

The interrupted run is excluded from the full-holdout quality comparison. Two successful outputs cannot support a meaningful 100-image ranking evaluation.

## 4. Limits of the evidence

Only **3 holdout photos carry label `3`**. Luna finds 1 at Top-10 and 2 at Top-20, giving recall of **33.3%** and **66.7%**; Qwen finds none at either cutoff. A single photo changes this recall by 33.3 percentage points, so it remains unstable and cannot support a strong class-specific conclusion.

Top-K results also depend on score ties. Luna has a tie crossing Top-20; Qwen has ties crossing both Top-10 and Top-20. The existing reporting method breaks ties by frozen sample position. Precision at these boundaries should therefore be read with that ambiguity in mind.

This is a 100-photo holdout from one corpus and one annotator. It provides evidence of generalisation to disjoint images from that corpus, not to every family, corpus or album preference. Disjoint image membership does not establish independence at the moment or burst level.

## 5. Decision, scope and evidence retained

**GPT-6 Luna is retained for the next stage on isolated photos.** It supplies a useful ranking signal on unseen, independently annotated images at low observed cost and with reliable execution in this run. No prompt change is proposed from these holdout results.

This validates a useful **isolated-photo judgement signal** against the annotator's labels. It does **not yet validate selection among photos of the same moment or burst**, redundancy handling, event-level comparison, or **the quality of a final album**. It also does not verify an actual photo-correction pipeline or personal significance beyond visible content.

The private local evidence consists of frozen set membership, human labels and their history, exact persisted run configurations, and per-image outputs, usage, costs and errors in `.photocurate/state.sqlite3`. The existing `.photocurate/reports/runs-8-10.md`, `.json` and `.csv` artifacts provide the completed-run comparison, generated at **10:21:03 UTC** on 2026-09-29. Run 9's interrupted state and raw responses are retained in SQLite.

This versioned report records the results at this milestone. Preparing it did not modify code, labels, runs, SQLite or local generated reports, and did not launch any VLM call. The baseline v1 report remains intact.
