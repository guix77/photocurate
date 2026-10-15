# Photo Curation VLM Evaluation — Design

**Status (2026-10-15): closed technical case study.** This document records the original
v1 design. The later same-moment experiment is documented separately. Future phases
below are historical possibilities, not planned work. See the [README](README.md) for
the final results, business KILL decision and evidence available for review.

## 1. Purpose

This project is a small, source-agnostic proof of concept for evaluating whether a low-cost vision-language model can judge the album-worthiness of personal photos well enough to justify further work on automatic photo-book curation.

The first version is **not** an album generator. Its purpose is to answer one question with measured evidence:

> Can an economically realistic VLM judge individual personal photos in isolation and rank them in a way that agrees sufficiently with a human reviewer?

A positive result may justify a later phase around event grouping, redundancy handling, coverage of time periods, album composition and layout. A negative result should be sufficient to stop the project without having built those layers.

This PoC also serves as an Applied AI / Solutions Architecture proof: model selection, multimodal evaluation, cost/quality trade-offs, privacy constraints, reproducibility and evidence-based GO/NO-GO.

---

## 2. Scope

### In scope

- Indexing a local corpus of personal images from an arbitrary directory.
- Recursive discovery of supported image files.
- Reading only a minimal set of useful metadata.
- Creating reduced local proxies for model evaluation.
- Running interchangeable VLMs/providers against a fixed evaluation sample.
- Human annotation of that same sample.
- Persisting runs and their exact experimental configuration.
- Comparing model quality, cost, latency and failures.
- Re-running against the same source directory after files have been added or changed.

### Out of scope for v1

- Video processing.
- Automatic album generation.
- Comparing photos within the same moment or burst to choose the best one.
- Evaluating the quality of a final album.
- Event detection or clustering.
- Duplicate or near-duplicate detection.
- Embeddings or vector databases.
- Agentic workflows.
- Facial recognition or biometric identification.
- Product UI.
- Automatic ingestion from Google Photos, iCloud or another remote photo service.
- Provider-specific source assumptions such as Google Takeout.

The current test corpus contains roughly 5,000 images. Videos have already been removed manually from the working corpus, but the program must still ignore unsupported/non-image files cleanly.

---

## 3. Design principles

### 3.1 Source-agnostic

The input is an arbitrary local directory such as:

```text
/Volumes/Photos/Photos 2025
/Users/guix/Pictures/Famille
```

The design must not assume Google Photos, Takeout, a particular folder layout, or a particular filename convention.

The source directory is supplied at runtime. No corpus path is hard-coded.

### 3.2 Originals remain external and immutable

Source images remain where they are. The project must not move, rename, rewrite or duplicate originals as part of normal operation.

The project stores an absolute path to each source image so downstream steps can reopen the original directly if needed later.

### 3.3 Minimal persisted metadata

For each source image, persist only what is currently useful:

- absolute source path;
- file size and modification time for change detection;
- capture datetime when reliably available;
- latitude/longitude when available;
- EXIF orientation when available.

No camera make/model, focal length, ISO or similar metadata is required in v1.

A provider-specific media identifier is not part of the core model. A generic source identifier may be added later only if a concrete use case requires it.

### 3.4 Unknown means unknown

When reliable capture datetime or geolocation is absent, store `null`.

Do not silently use filesystem modification time as a capture date.

Source-specific sidecars may be supported in a later phase, but are not required by the generic v1 design.

### 3.5 Cheap derived images, untouched originals

Model calls use local image proxies rather than originals.

The initial proxy target is **512 px on the long edge**, orientation corrected, encoded in a broadly supported format suitable for VLM input.

The proxy size is an experimental variable rather than a permanent product decision. Later runs may compare 512 px with larger proxies such as 768 px.

### 3.6 Deterministic pipeline, not agentic orchestration

The v1 workflow is known in advance. It should therefore be deterministic and directly executable.

Do not introduce agents, tools selected dynamically by an LLM, planning loops, autonomous retries based on semantic judgement, or other agentic machinery unless a later measured need appears.

### 3.7 Add complexity only after evidence

Do not add embeddings, pHash, clustering, vector search, web UI, external databases, workflow frameworks or evaluation platforms pre-emptively.

Each additional component must solve an observed limitation in the evaluation results.

### 3.8 Judge realistic correction potential

The stable semantic judgement target is **`standard_non_generative_v1`**: judge each photo in isolation for a family album, using only visible content and no personal knowledge or external context. Assess album-worthiness **after ordinary, non-generative corrections that could reasonably be automated**, rather than strictly as presented. Do not assume blur, lost detail or missing content can be recovered.

Ordinary corrections can include cropping, straightening, moderate exposure and colour adjustments, red-eye correction, mild denoising and sharpening. These examples document the rubric; the per-image instructions need not repeat a catalogue of corrections or defects.

Reduced proxies limit technical judgement; they do not justify assuming that unseen detail exists in the original.

The v1 measures **estimated correction potential**. It does not apply these corrections or demonstrate that an automated correction pipeline can realize that potential.

The target ID describes the judgement task, not the exact instruction wording. Editorial simplifications and model prompt experiments can retain this ID when they preserve the target. A genuinely different judgement target requires a new ID. Findings from manually inspected failure cases remain experimental observations rather than new prompt rules.

---

## 4. Implementation constraints

The implementation should use modern Python managed with `uv`.

The media pipeline should remain Python-native unless a real compatibility problem proves that an external tool is necessary. JPEG/PNG/WebP/HEIC support, EXIF access, orientation handling and proxy generation should therefore initially be implemented through Python libraries.

Persistent state should use **SQLite**. The reason for SQLite is not corpus size; it is clean experimental tracking across multiple models, prompts, settings and human labels.

Internal module layout, class structure, function names, SQL details and CLI organization are implementation choices and should be left to the coding agent as long as the capabilities described here are satisfied.

---

## 5. Corpus indexing and incremental behavior

The system receives a source directory and scans it recursively.

For each supported image it should:

1. identify the source file by its absolute path;
2. read the minimal metadata defined above;
3. create or refresh the required local proxy;
4. register the image in persistent project state.

The same source directory may be processed again later after new photos have been added.

A repeat run must avoid unnecessary work:

- unchanged files remain indexed and their existing derived data can be reused;
- newly discovered files are added;
- changed files are refreshed;
- a single unreadable or unsupported file must not abort the whole corpus.

The implementation may use source path plus lightweight filesystem attributes such as size and modification time to detect likely changes. A content hash is not required for v1.

---

## 6. Persistent concepts

The storage model should represent the following concepts without coupling them unnecessarily.

### Media

A source image plus its minimal metadata and derived proxy information.

### Evaluation set

A reproducibly selected subset of media used for human/model comparison, with fixed membership for each comparison.

The first evaluation set should start with approximately **100 randomly sampled images** from the corpus. This is a starting point, not a mandatory final size. The selection must be reproducible so all models are tested on the same images.

The initial sample should be genuinely random rather than manually curated toward interesting or difficult cases.

A holdout can be drawn from the same corpus with `sample create --exclude-set baseline`. Exclude every media identity in the named set before the seeded random draw, including any newer versions of those images. Persist the source, seed and selected membership without changing existing sets, labels or runs. Fail if the excluded set is unknown or too few eligible images remain. This exclusion applies only to creation, not to a later extension.

If results are close or human-labelled classes `0` or `3` are too rare to support a conclusion, enlarge the same evaluation set before deciding GO/NO-GO. Keep the existing images and add further reproducibly random images from the remaining corpus. Record the additions, annotate them, and evaluate every model being compared on the same enlarged set. Preserve the membership used by earlier runs so their results remain reproducible.

A separate hard-case set may be created later if the first evaluation reveals a reason to do so.

### Human label

A human judgement attached to an image in an evaluation set.

These labels are the reference for measuring **agreement with the annotator**, not a universal ground truth about what makes a good family photo. Evaluation conclusions describe how well the model matches that annotator's judgement of photos in isolation.

The judgement uses the semantic target in section 3.8. Persist its ID and the exact human instructions displayed with each label and its correction history. Preserve existing labels and their original instruction text. An editorial simplification under the same target does not require reannotation. Earlier labels must not silently acquire a different target or new provenance.

The v1 scale is:

```text
0 = à jeter
1 = bof / seulement si utile au contexte
2 = bonne photo d'album
3 = forte / quasi certain de garder
? = impossible à juger seule
```

The meaning of these values must be displayed every time a label is requested.

Human labels are independent of any model run and must never be overwritten by model outputs.

### Run

A complete, reproducible VLM experiment.

A run must preserve enough information to understand exactly what was tested, including at least:

- model;
- provider/route;
- model settings that materially affect behavior;
- image/proxy size;
- exact prompt or prompt version/content;
- exact structured-output contract/schema;
- declared semantic judgement target ID, or an explicitly unspecified target, independently of the exact prompt;
- creation time.

The exact representation is an implementation choice.

### Result

For each image evaluated in a run, persist:

- model output;
- validation/error status;
- provider usage information when available;
- input/output token accounting when available;
- attributable inference cost when available or derivable;
- latency;
- provider/model error information.

An invalid structured response must be recorded as an error rather than silently repaired in a way that makes the evaluation irreproducible.

---

## 7. Human annotation workflow

No product UI is required in v1.

A lightweight local annotation workflow is sufficient. On macOS, the preferred interaction is to display the current image in Preview without stealing focus from the terminal, allowing the reviewer to keep typing labels in the terminal.

For every image, the annotation prompt must visibly repeat short human instructions expressing the semantic target in section 3.8 and the label scale:

```text
0 = à jeter
1 = bof / seulement si utile au contexte
2 = bonne photo d'album
3 = forte / quasi certain de garder
? = impossible à juger seule
```

The reviewer should be able to correct the immediately previous annotation.

The implementation should favor fast, low-friction annotation over presentation polish.

---

## 8. VLM evaluation contract

The exact v1 prompt and exact structured schema are **not fixed by this design**. They are part of the experiment and may change across runs.

However, every candidate prompt/schema must satisfy the following functional contract:

- input is the image proxy;
- v1 evaluation uses **image only** as model context;
- collected datetime and geolocation are intentionally withheld from the model in this phase so the experiment isolates visual judgement;
- agreement with the current human labels requires the same declared semantic judgement target (section 3.8), including its limits;
- output is structured and machine-validatable;
- output contains at least one value that allows photos to be ranked by album-worthiness;
- runs using different prompts or schemas remain comparable through the same frozen human labels when they declare the same semantic target;
- the exact prompt, exact schema and independently declared semantic target ID are persisted with each run.

The built-in default prompt is explicitly associated with `standard_non_generative_v1`. A custom `--prompt` must declare its target with `--judgement-target ID` to be eligible for agreement metrics; otherwise its target is stored as unspecified and it is excluded from agreement metrics against labelled data. Never infer semantic equivalence from prompt text or silently assign the current target to an arbitrary custom prompt. Send custom prompts exactly as supplied, without automatic instruction prefixing.

PhotoCurate's default judgement is intentionally opinionated: assess composition, technical quality and subject presentation. For photos centered on people, favor clear, flattering presentation and meaningful engagement with the camera or scene. Direct eye contact matters for posed portraits, but is not required for strong candid or action moments. The default prompt has no explicit emotional/narrative-interest criterion or blanket instruction to be conservative; it requests a brief justification and relevant uncertainty.

Review and freeze this default prompt before creating the holdout, and do not tune it using holdout results. This freeze preserves `standard_non_generative_v1`, the existing human labels and the separation between semantic target, human provenance and exact experimental prompt. It introduces no user settings or list of rules specific to development-set failure cases.

Persisted run configurations remain immutable, including their prompt and declared target. Existing convention snapshots can contain instruction strings, but those strings are provenance rather than compatibility keys. No SQLite migration or rewrite of existing labels or runs is required for this separation.

The project may later evaluate whether explicit intermediate attributes improve judgement, but no such attribute set is prescribed here.

---

## 9. Model/provider strategy

No model family is chosen in advance.

The v1 provider is **OpenRouter**. The project should allow multiple VLMs available through OpenRouter to be evaluated under the same conditions, while keeping the evaluation pipeline independent of the provider client.

The API key is loaded from a local `.env` file or the environment. Provide a committed `.env.example` documenting the required variable, but never commit the actual `.env` file.

Selection should be evidence-based on this corpus, not based only on public benchmark rankings.

Candidate models should be economical enough to make a future consumer photo workflow plausible at corpus scale.

### Privacy constraint

Personal/family images may contain children and other private material. **Zero data retention (ZDR) and no training on submitted content are mandatory.**

This policy is enforced by the OpenRouter account/API key configuration, restricted to ZDR and no-training routes. The runtime may treat that configuration as a guarantee and does not need additional application-side privacy filtering.

Credentials must not be stored in the project database or committed to source control.

---

## 10. Evaluation methodology

The core evaluation compares model ordering against human judgement on the frozen evaluation set.

Quality metrics measure agreement with the annotator. Approximately 100 images are sufficient to start measuring, but close results or too few labels in classes `0` or `3` require enlarging the same set as described above and comparing models on its expanded, fixed membership before concluding.

Agreement metrics must use labels and runs with the same explicitly recorded semantic judgement target ID, independently of exact instruction text. Different or unspecified targets are incompatible, including two unspecified targets; never infer a target from saved prompt or instruction text. Report the excluded labels explicitly. Annotation can revisit labels with different or unspecified targets while retaining previous labels and instructions in history. Editorial simplification under the existing target keeps the current 100 labels usable without reannotation.

Primary quality views should include:

### Ranking agreement

Measure whether higher human-rated photos also tend to receive higher model ranking.

A rank-correlation metric such as Spearman is appropriate, but the exact implementation is not mandated.

### Top-K usefulness

Measure the quality of the model's highest-ranked subset, since an album workflow ultimately needs to surface a relatively small number of strong candidates.

Useful views include:

- proportion of top-K model choices that humans rated `2` or `3`;
- recovery of human-rated `3` photos in the high-ranked portion of model output.

### Major disagreements

Surface the largest practical errors for manual inspection, especially:

- human `0` photos ranked very highly by the model;
- human `3` photos ranked very poorly by the model.

These examples may be more informative than a single aggregate metric when deciding whether a model understands the task.

### Operational metrics

For every run, compare at least:

- total cost;
- cost per image and normalized cost per 1,000 images;
- latency distribution;
- provider/model failure rate;
- structured-output validation failure rate.

---

## 11. GO / NO-GO decision

Do not invent a numeric success threshold before observing the first results.

The purpose of the initial evaluation is to establish a baseline and compare several economically plausible model/configuration choices on the same human-labelled sample.

A v1 GO validates only that the model can judge an isolated photo with useful agreement with the annotator. It does **not** validate the harder task of comparing photos within the same moment or burst, or the quality of a final album. Those require separate evaluations in a later phase.

Under the correction-potential convention, a GO concerns estimated potential and does not validate an actual automated correction pipeline.

The project continues only if the observed combination of:

- human agreement;
- top-K quality;
- severity and frequency of major mistakes;
- cost at realistic corpus scale;
- latency/reliability;
- privacy compatibility

is compelling enough to justify testing the next product-level question.

If no economical model produces convincingly useful ranking, the project should stop without proceeding to album-generation architecture.

A technically successful PoC may still result in a product NO-GO. That is a valid outcome.

---

## 12. What a successful v1 should produce

At the end of v1, the project should be able to demonstrate, from a real corpus of approximately 5,000 personal images:

- a reproducibly indexed corpus with untouched external originals;
- lightweight local image proxies;
- a frozen human-labelled evaluation set;
- multiple reproducible VLM runs over the same set;
- measured quality, cost, latency and error characteristics;
- concrete examples of major model/human agreements and disagreements;
- enough evidence to make a reasoned GO/NO-GO decision on continuing toward automatic album curation.

The deliverable is therefore not an album. It is **an evaluated technical hypothesis**.

---

## 13. Deferred questions for a later phase

Only if v1 is a GO should the project revisit questions such as:

- whether datetime and geolocation improve curation when supplied as explicit context;
- event segmentation;
- comparison and selection within the same moment or burst;
- burst/redundancy handling;
- duplicate detection;
- face/person continuity without biometric identification;
- coverage across time and people;
- multi-stage ranking;
- album-level evaluation;
- final full-resolution retrieval and layout;
- product UX and remote photo-provider ingestion.

None of these should influence the v1 implementation beyond avoiding decisions that would make later experimentation unnecessarily difficult.
