# PhotoCurate

A Python proof of concept for measuring agreement between economical VLMs and a human
annotator on personal photos judged in isolation. The scope is defined in [DESIGN.md](DESIGN.md).
A v1 GO validates neither comparison within a moment or burst nor the quality of a final album.

## Experiments and development journal

PhotoCurate is developed as an evidence-driven Applied AI experiment, not only as an implementation.

- [Development journal](docs/JOURNAL.md) — chronological record of hypotheses, methodology changes, experiments, findings and decisions.

Generated run artifacts remain in the private local `.photocurate/reports/` state and are not versioned with the source repository.

## Installation

Python 3.12 or later, managed with `uv`:

```sh
uv sync
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`. The OpenRouter key/account must be restricted to
**ZDR and no training**. That configuration guarantees the privacy policy; the application
does not filter routes for these criteria.
`.env` and the local `.photocurate/` state directory are ignored by Git. An existing
environment variable takes precedence over `.env`.

No model is selected by default. Supply an OpenRouter ID for a vision model that supports
structured outputs. The `require_parameters` preference checks routes for technical
compatibility with the requested parameters; it is not a privacy filter.

## Index and select

The corpus path is always supplied at runtime. Quote paths containing spaces.

```sh
uv run photocurate index "/path/to/my photos"
uv run photocurate sample create baseline --source "/path/to/my photos" --size 100 --seed 42
uv run photocurate sample list
uv run photocurate sample show baseline
```

Scanning is recursive and supports JPEG, PNG, WebP, HEIC and HEIF, with case-insensitive
extensions. Other files are ignored. Unreadable files are recorded as errors without
stopping the scan. Directory symlinks are not followed, and files pointing outside the
corpus are excluded.

Originals remain external and untouched. SQLite stores absolute paths, file size,
mtime, reliable capture datetime, GPS and orientation; missing data remains `null`.
Filesystem modification time is never treated as capture datetime.
JPEG proxies are orientation-corrected, reduced to 512 px on the long edge without
upscaling small images, and stripped of metadata. Transparent images are composited
on a white background. HEIC files use Pillow and pillow-heif, without an external
media tool.

A repeat scan reuses unchanged files and proxies, adds new images, and creates a new
version for changed files:

```sh
uv run photocurate index "/path/to/my photos"
```

Selection is random and reproducible with the seed and the same indexed corpus.
Actual sample membership is persisted. Members remain frozen even if their originals
are subsequently changed. Earlier proxies are retained for existing annotations and
experiments.

## Annotate

On macOS, Preview displays the current image proxy without taking focus from the terminal:

```sh
uv run photocurate annotate baseline --annotator guix
```

The stable judgement target is `standard_non_generative_v1`: judge each photo in
isolation for a family album, using only visible content and no personal knowledge or
external context. Assess its potential after ordinary, non-generative corrections that
could reasonably be automated. Do not assume blur, lost detail or missing content can
be recovered. The short human instructions and the scale are displayed for every
image.

This evaluates estimated potential; it neither applies corrections nor validates an
automated correction pipeline.

The annotation scale is displayed at every prompt, in French as specified by the design:

```text
0 = à jeter
1 = bof / seulement si utile au contexte
2 = bonne photo d'album
3 = forte / quasi certain de garder
? = impossible à juger seule
```

`b` corrects the previous annotation, including the final annotation at the end of the
session; `q` quits. Running the command again resumes unlabelled images. `--review`
revisits all annotations. `--no-viewer` allows input without opening Preview.
Corrections retain a history with the annotator's name and timestamp.

The semantic target ID and exact human instructions displayed are stored with labels
and their history. Editorial changes to the instructions retain the same target ID;
existing labels and their original instructions remain untouched and do not require
reannotation. A genuinely different judgement target requires a different ID.
Labels with a different or unspecified target are offered for explicit review by
`annotate`; opening the workflow does not overwrite them. An explicit correction or
review records the newly displayed instructions while retaining the previous history.

Labels measure **agreement with the annotator**, not a universal truth about good
family photos. They remain independent of model outputs.

## Prepare and execute an experiment

The default prompt and schema are in `src/photocurate/provider.py`.
The model returns `album_score` (an integer from 0 to 100, higher means a stronger
candidate) and a short explanation in `reason`. It receives only the prompt and proxy
pixels, without datetime, GPS, filename or source path.

The built-in default prompt explicitly declares `standard_non_generative_v1` as its
target. PhotoCurate is intentionally opinionated about album-worthiness: the default
prompt assesses composition, technical quality and subject presentation. For photos
centered on people, it favors clear, flattering presentation and meaningful engagement
with the camera or scene. Direct eye contact matters for posed portraits, while strong
candid and action moments do not require it. It asks for a brief justification and
relevant uncertainty in `reason`, without an explicit emotional/narrative-interest
criterion or a blanket instruction to be conservative.

This default prompt was reviewed and frozen before creating the holdout, so holdout
results cannot guide its tuning. It adds no user settings or rules specific to reviewed
development-set cases. Prompts remain experimental configurations, independent of
human instruction wording: the exact supplied text is sent and saved without a prefix.

A custom `--prompt` has an **unspecified target by default**. Its operational metrics
remain available, but agreement metrics against human labels are excluded. To declare
that a custom prompt uses the current rubric, add
`--judgement-target standard_non_generative_v1`. This is an explicit protocol
declaration, not a semantic equivalence inferred from the prompt text. Even a file
containing the exact built-in prompt remains unspecified without this option.
Use a different target ID for a genuinely different judgement task.
`--judgement-target` requires `--prompt`; the built-in prompt's target is fixed.

Prepare an experiment without a key or paid requests:

```sh
uv run photocurate run baseline --model provider/model --prepare-only
uv run photocurate runs
```

Execute the prepared experiment using the displayed ID:

```sh
uv run photocurate resume 1
```

Or create and execute a new experiment directly:

```sh
uv run photocurate run baseline --model provider/model
```

The exact configuration (model, endpoint, prompt, schema, score field, proxy size,
settings, concurrency, timeout and declared privacy policy) and run membership are
frozen in SQLite, along with the independently declared semantic judgement target ID.
Existing runs keep their saved prompts and target metadata, including pending runs;
changing the default affects only newly created runs. No database migration is needed.
The raw response, output, tokens, provider usage, cost, latency and
errors are recorded per image. Generation IDs and the actual provider route are retained
when included in the provider response. Credentials are not stored.

Defaults: 512 px, no explicit temperature, a maximum of 512 output tokens, a 60-second
timeout, and one request at a time. Temperature is omitted so models that do not support
that parameter can be routed. The saved configuration records it as `null`, meaning the
provider's default is used. Set `--temperature` only for models/routes that support it.
Options allow comparisons with other configurations:

```sh
uv run photocurate run baseline --model provider/model \
  --prompt prompt.txt --judgement-target standard_non_generative_v1 \
  --schema schema.json --score-field ranking.value \
  --proxy-size 768 --max-output-tokens 1024 --concurrency 2
```

The schema must describe a self-contained JSON object (local references are allowed),
and the ranking field must be a finite number where higher values are better.
An additional proxy size is generated from the original only if it still matches the
selected version; otherwise preparation fails explicitly.

`resume` requests only images without a recorded result. Errors are results too and
are neither retried nor overwritten: create a new run to test them again.
Invalid, ambiguous, schema-incompatible or truncated JSON responses are retained as
errors without automatic repair. No application-level retries are performed.

Errors are printed with their recorded details. HTTP 401 (authentication), 402 (credits)
and 404 (model/route unavailable) stop further requests for the run; images not requested
remain pending. Calls already in flight with concurrent execution may still finish.
Commands exit with a nonzero status when a run has errors.

If OpenRouter reports `No endpoints found that can handle the requested parameters`,
check the chosen model's supported parameters. With `require_parameters: true`, an
unsupported explicit parameter can exclude every route. Keep ZDR/no-training enabled;
prepare a new run with compatible parameters. Resuming an older run preserves its
original configuration, including any explicitly saved temperature.

Real experiments consume OpenRouter credits. Automated tests use only a simulated
HTTP transport.

## Enlarge the same evaluation set

100 images are a starting point. If results are close or labels `0` / `3` are too rare,
add random images before drawing a conclusion:

```sh
uv run photocurate sample extend baseline --size 200
uv run photocurate annotate baseline --annotator guix
uv run photocurate run baseline --model provider/model
```

`--size` is the new total size. Existing members and labels remain intact; additions
and their seed are recorded. Earlier runs retain their original membership.
Repeat every compared configuration on the same enlarged set; reports flag comparisons
between different memberships.

## Reports

```sh
uv run photocurate report 1 --top-k 10 20
uv run photocurate report 1 2 3 --top-k 10 20
```

Markdown, JSON and CSV exports are written to `.photocurate/reports/`, or to
`--output-dir`. They include:

- Spearman correlation with average ranks for ties; `?` labels and invalid outputs excluded.
- The proportion of labels `2` / `3` in the top-K and recall of labels `3`.
- Annotation coverage, class distribution and ties at the K boundary.
- The highest-ranked human `0` photos and lowest-ranked human `3` photos, with proxy /
  original links and explanations for manual inspection.
- Observed cost, cost per inference image and per 1,000 images, p50/p90/p95 latency,
  provider failure rate and structured-output validation failure rate.

Top-K is selected among valid results before excluding photos without a numeric human
label from its precision denominator. Recall of `3` also includes photos with model
errors. Score ties are broken by sample position and flagged. Spearman is `null` when
the data does not support its calculation (fewer than two pairs or constant ranks).

Provider cost takes precedence. When it is missing, explicit
`--input-usd-per-million` and `--output-usd-per-million` prices allow an estimate from
available token counts; prices are part of the persisted configuration.
Incomplete coverage is never reported as a zero total cost: totals and normalized
costs remain unknown, and known cost is displayed separately.
The integration follows the official
[structured-output](https://openrouter.ai/docs/features/structured-outputs) and
[usage-accounting](https://openrouter.ai/docs/guides/guides/usage-accounting) formats.

Reports use current labels at creation time, including corrections, and export the
exact labels to preserve that interpretation. No numeric GO/NO-GO threshold is invented.

Agreement metrics require the same explicitly recorded semantic target ID on both
the label and the run. Exact instruction strings do not determine compatibility:
prompt experiments and editorial simplifications can use the same frozen human labels.
The existing `convention_json` and run `judgement_convention` JSON fields retain this
ID and provenance; earlier snapshots containing instruction strings remain valid.
Different or unspecified targets are excluded, including when both targets are
unspecified. Reports display the coverage gap and flag comparisons with different or
unspecified targets. No missing target is inferred from saved prompt or instruction text.

## Local state and development

```sh
uv run photocurate status
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
```

All commands accept `--state-dir` and `--env-file` **before** the subcommand:

```sh
uv run photocurate --state-dir "/path/to/state" --env-file "/path/to/.env" status
```

State contains `state.sqlite3`, `proxies/<version>/<size>.jpg` and reports.
It is local and contains private photos: keep this state out of version control.
The project does not generate albums and includes no clustering, deduplication,
embeddings, facial recognition, agents or product UI.
