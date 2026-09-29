# PhotoCurate — Same-moment selection protocol v1

Date: 2026-09-29. Status: pilot and 40-group test completed; initial results and next
diagnostic recorded in [JOURNAL](../JOURNAL.md). The protocol below describes the
frozen initial experiment; supplementary analyses do not replace its primary results.

## Question and comparison

Among small groups of photos from the same moment, does GPT-6 Luna select the same
representative as the annotator? Does a joint multi-image choice improve on selecting
the highest isolated Luna score? Evaluate both methods on exactly the same groups.

The initial budget is **one representative**, even when all images are weak. Equivalent
human alternatives are allowed. Reject-all and complementary multi-photo retention are
separate future tasks: an isolated score ranking has no calibrated cardinality rule.

## Available evidence and grouping

The inspected corpus has 4,851 indexed photos and 512 px proxies; 4,373 capture dates,
including 3,683 with timezone offsets; and 3,935 GPS coordinates. Camera/source identity
and burst identifiers are not indexed. All originals occupy one annual directory.

Use timezone-aware EXIF capture dates normalized to UTC. Form maximal temporal components
with successive gaps at most 30 seconds. Keep complete components of 2–6 images spanning
at most 120 seconds; do not truncate or split oversized components to fit this limit.
Require human confirmation of visual coherence. Missing/naive dates are outside v1.

Exclude every photo in an existing evaluation set and every known day represented in
those sets. This conservatively reduces previous-moment exposure but cannot locate older
undated images. Select one complete candidate per day using seed 42. GPS divergence is
only a warning: visual inspection found a coherent scene despite a 2.8 km GPS spread.
Exact JPEG-proxy duplicates can be collapsed by SHA-256; no perceptual similarity machinery.

The initial scan yielded 364 time candidates; 153 remained after prior-media/day exclusions,
on 98 distinct days. The prepared pool contains one seeded candidate from each of those
98 days, with frozen versions, proxy checksums and shuffled presentation orders.

## Annotation and freeze

Obtain five accepted pilot groups and 40 accepted test groups on different days. Candidates
outside the family-album scope (for example, work presentation screens), incoherent groups
and illegible proxies stay in annotation history with distinct exclusion reasons and are
replaced from the reserve. Do not exclude in-scope coherent groups because the choice is
difficult or every image is weak. Scope exclusions are human filtering before evaluation;
this experiment does not measure the model’s ability to identify irrelevant moments.

Use the same 512 px images for the annotator and both model methods. Show them together
with neutral identifiers and preserve their full framing. Human instructions retain the
visible-only, ordinary non-generative correction convention, with the new semantic target
`moment_representative_v1`. Annotation records preferred image, equivalent alternatives,
scope/coherence/legibility exclusions, annotator, instruction text and timestamp.

Labels precede both isolated and joint evaluation. Preparing a phase freezes its selected
groups and human labels; corrections are locked afterward. A blind second human pass on
up to ten test groups can be recorded before the test freeze. Model outputs never appear
in the annotation page. The page has no execution endpoint.

## Model execution

Reuse the exact isolated configuration from completed run 8 (`openai/gpt-6-luna`), rerun
on each new group image. The joint request contains the comparative prompt and individually
labelled image inputs, without filenames, dates, GPS or human choices. Strict output selects
one supplied alias and gives a brief reason. Actual image identity is recovered using the
frozen alias-to-version mapping.

Persist both configurations, per-group schemas, original and reversed orders, raw responses,
provider usage, tokens, cost, errors and latency. Resume only missing requests; retain errors.
No automatic retries, output repair, agents, embeddings, clustering or album construction.

Joint test evaluation repeats up to ten groups with reversed order and reassigned aliases.
This measures stability, with order effects and sampling variability confounded.

## Analysis and decision

Primary endpoint: agreement with the preferred image or a previously annotated equivalent.
Report strict preferred-image agreement separately, unique-choice versus equivalent-choice
strata, and isolated-score tie groups. Tie-breaking uses frozen shuffled presentation order.

Compare paired outcomes: both correct, joint only, isolated only, both wrong. Report the gain,
a group bootstrap interval and exact McNemar diagnostic. Wilson intervals describe each
agreement proportion. Include expected random selection and first chronological image.
The unit is the group/day, not a photograph. A small inconclusive result remains inconclusive.

Compare cost and serial request latency per group, including failed requests and cost
coverage. Errors count as unsuccessful selections; unfinished groups do not create a final
agreement estimate. Report human-repeat and model-order-check coverage and stability.

Forty groups are a small first test, not proof of universal performance. Extend a frozen
protocol or prepare a new experiment if uncertainty prevents a decision. Do not tune on
test outcomes. No numeric product GO threshold is imposed by this implementation.

## Implementation and private artifacts

The `photocurate moments` CLI reads SQLite in read-only mode and keeps experiment state in
private, Git-ignored files. It does not migrate or modify existing SQLite state. Its loopback
gallery saves annotation history, supports resumption, and serves only frozen image proxies.

Preparation made no VLM call. Existing photo labels and model runs remain unchanged.
