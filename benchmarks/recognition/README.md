# Frozen recognition benchmark material

**This directory is intended for Git, not a local cache.** It contains the recording excerpts and human references approved by their owner for inclusion, plus portable manifests, scoring definitions and historical aggregate results. Nothing here requires a private archive or machine-specific path.

This is **inspected development data**, not fresh validation. The three-conversation study covers one primary speaker across three recordings, not a broad speaker population. Presence negatives are reviewed candidate-text insertions/repetition surpluses, not 20 independent naturally occurring ASR errors. It does not qualify B2/B3 or authorize another experiment. The [research recap](../../RECOGNITION-RESEARCH.md) describes the problem and outcomes.

## Contents

| Asset | Purpose |
|---|---|
| [`corpus.json`](corpus.json) | Stable case IDs, source windows, sample clocks, relative paths, SHA256/size checks and corpus counts |
| `audio/01-mental-health.wav`, `02-speed.wav`, `03-walking-oscar.wav`, `04-legacy-11yo.wav` | Four exact mono PCM16/16kHz benchmark excerpts; **125.5 seconds** total |
| `audio/legacy-phoneme-*.wav` | Two exact 9.2s native phone-pilot derivatives; separate from the main denominator |
| `audio/review-*.wav` | Seven short original/wider listening-review excerpts, retained to audit annotation decisions; not extra independent benchmark samples |
| [`references/conversations-v2.json`](references/conversations-v2.json) | Current reviewed three-excerpt reference: **280 words, 20 fillers**, with four uncertainty positions and review history |
| `references/conversations-v0.json`, `conversations-v1.json` | Earlier references retained, not silently overwritten; not the default gold |
| [`references/legacy.json`](references/legacy.json), `legacy-labels.tsv` | Legacy 30s reference: **84 words, five fillers**, only partial human boundary timing |
| [`references/presence-ledger.json`](references/presence-ledger.json) | All **364 original slots**: 343 eligible genuine occurrences including 25 fillers; 21 quarantined |
| [`references/presence-labels.json`](references/presence-labels.json) | Frozen 20 audio-reviewed negative cases, including repetition-surplus truth; evaluation-only |
| `references/adjudication-*.txt` | Human listening notes; audio locators rebound to the included review clips |
| [`scoring-policy.json`](scoring-policy.json), [`references/format-equivalence-v1.json`](references/format-equivalence-v1.json) | Literal normalization/alignment/metric definitions and separately labelled historical formatting diagnostic |
| [`rounds/2026-11.json`](rounds/2026-11.json) | Model/code/configuration pins, runtime versions, aggregate results, failures, limits and stop decisions; bound to the corpus manifest hash |
| [`provenance.json`](provenance.json) | Owner approval, original/exported reference hashes and exact metadata-only relocations |

## Verify after checkout

From the project root:

```sh
devenv -q shell -- python benchmarks/recognition/corpus_integrity.py
devenv -q shell -- python -m unittest discover -s benchmarks/recognition -p 'test_*.py' -v
```

The verifier uses only Python's standard library. It checks file bytes, WAV headers, native sample counts, reference/ledger token identity, complete case rosters, occurrence IDs and annotation counts. It performs **no model inference or accuracy grading**; a successful integrity check is not a qualification result. Git attributes preserve WAV bytes and LF text across checkouts.

## Reuse correctly

- Give an acoustic recognizer **audio only**. References, negative-case operations, uncertainty labels and human timing are evaluator inputs, never model hints or training data in this workflow.
- Keep the primary recognition representation and normalization fixed before execution. The two literal profiles differ: the legacy profile admits the historical `11`/`eleven` and `[UH]`/`[UM]` notation aliases; the conversations profile retains numeral/contraction differences in raw WER.
- Report canonical results **and all predeclared uncertainty variants**. Four binary positions yield 16 variants in the third conversation. Quarantined presence truth does not become definitive through model agreement.
- The historical format-equivalence diagnostic was defined after earlier baseline/Turbo predictions had been inspected, before the full-AMI screen. It is not independent validation and never changes raw transcripts.
- Preserve case boundaries and source offsets. Do not pad, stretch, denoise, retime or re-encode the WAVs. The historical presence catalogue hashed a full legacy decode; that is not the same file as a bounded 30s WAV. Original native pilot derivatives are explicitly separate assets—do not equate different container hashes or change their provenance.
- Keep future rounds append-only, with model/weight/code pins, corpus/reference hashes, settings, budget and all outcomes. Changing truth or audio requires a new version; do not update hashes merely to make failed verification disappear.
- The checked-in tool verifies **integrity**, not accuracy. Scoring rules are versioned here; this change does not introduce a new ASR runner, score historical predictions again, or claim an executable reproduction of every old diagnostic.

## Inclusion and reproduction limits

Full source videos, model weights, environments and credentials are not included or required to inspect and verify this corpus. Original recording fingerprints and windows are provenance metadata, not hidden file dependencies.

Raw third-party model predictions, logits and generated timing traces are **not redistributed in this change**, pending review of their output terms. Their recording-content privacy is cleared by the owner's consent, but that does not itself change model-output licensing. Historical aggregates/configurations are retained in Git; exact historical edit traces cannot be recomputed from aggregates alone. New candidate outputs can be scored against the included references; any fresh baseline inference requires an explicit budget.

No recording, reference token, human boundary or ambiguity decision was changed during export. WAVs and most reference files are byte-identical copies. The legacy label-file locator and two historical negative-case audio locators were rebound to tracked assets; review-note audio paths were similarly relocated and an unavailable model-comparison pointer removed. These metadata-only changes and both original/exported hashes are recorded in `provenance.json`.

Owner approval permits committing this corpus and its human annotations. Do not infer permission to add other recordings, publish credentials, redistribute restricted model outputs, change product defaults, or resume experiments. Research remains paused.
