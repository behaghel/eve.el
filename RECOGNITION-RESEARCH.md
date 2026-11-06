# Verbatim recognition and timing research

**Status (2026-11-06): paused pending credible new released models. B2/B3 remain unqualified.** Editing/export and transcription integration are accepted; that does not establish automatic recognition or timing accuracy. No fine-tuning project is approved.

This is a research recap, not a replacement for the [TJM specification](docs/tjm-spec.md), [recognition-token authority](docs/internal/recognition-token-authority.md), or [alignment comparison policy](docs/internal/alignment-comparison-policy.md).

**Retained material:** the owner-approved [benchmark corpus](benchmarks/recognition/README.md) is checked in: exact audio excerpts, human references and annotation history, hashes, scoring definitions, and [historical configurations/results](benchmarks/recognition/rounds/2026-11.json). No private archive or local path is required. Raw third-party predictions are not redistributed pending output-terms review, so historical edit traces cannot be recomputed from aggregate results alone.

## Core problem

We want **fresh recording → faithful verbatim transcript → trustworthy word timing → safe editing and subtitles**.

Conventional ASR often removes genuine fillers, repetitions and false starts. Verbatim-oriented models recover more, but can also misclassify ordinary speech as fillers, substitute content or loop. Those errors can become destructive edits. Separately, forced aligners can assign plausible, finite intervals to words that were never spoken. Valid structure, successful inference and precise-looking timestamps are not evidence of correctness.

Recognition fidelity (B2), word presence, boundary accuracy and speech/pause coverage (B3) must be evaluated separately. A reject-only word gate cannot restore omitted speech.

## Benchmark method

1. **Freeze before execution:** recording windows and hashes, model/code revisions, weights, decoding/token policy, output representation, evaluation rules, resource limits, call budget and stop conditions.
2. **Keep truth independent:** human-reviewed references/timings are evaluation-only—not prompts, vocabulary mined from answers, training labels or routine production requirements. Revise references only through separate audio confirmation/versioning; retain earlier scores.
3. **Run locally and preserve everything:** seal predictions before evaluation. Retain raw text, native word/token streams, IDs, clocks and failures. No retries, sweeps, tail trimming, spelling/pronunciation repair or post-output label changes within a frozen experiment.
4. **Report separate dimensions:** literal edit counts/WER; filler precision and recall; genuine repetition counts; content loss/unsupported words; boundary error, coverage and completion. WER is `(substitutions + deletions + insertions) / reference words`. Filler recall is `correct fillers / reference fillers`; precision is `correct fillers / predicted fillers`. A content word substituted with a filler is not filler credit. SDK tokenization effects and predeclared format-equivalence diagnostics stay separate from raw scores. Do not choose whichever representation scores best afterward.
5. **Keep denominators honest:** abstention is a miss on a clear negative; wrong-neighbor accusations are not hits. Repetitions have surplus counts, not uniquely identifiable duplicate tokens. Missing/failed cases remain visible. Word mismatches alone are not audio-confirmed hallucinations.

Whisper's standard English normalizer removes `uh`/`um`: WER using it cannot establish filler preservation. Majority model agreement is not reference truth.

The checked-in development corpus spans four excerpts, **125.5 seconds**, with **343 eligible genuine occurrences including 25 fillers**; 21 other slots are quarantined. It is now inspected **development data**, not fresh validation. Studies below use different subsets and are not a common leaderboard.

## Systems and methods investigated

| System / method | Scoped outcome |
|---|---|
| Stock Whisper, including medium.en/large-v3; prompt/vocabulary controls | Established baselines, but filler omissions/content errors persisted. Larger models or changed hints did not establish qualification. |
| Nemotron streaming ASR; Switchboard-adapted CTC controls | Exploratory alternatives; no qualified verbatim/timing solution established. Do not infer a ranking across differently configured screens. |
| `JacobLinCool/whisper-large-v3-turbo-verbatim-1` | On three excerpts (95.5s), recovered **18/20 fillers vs baseline 12/20**. Format-equivalent diagnostic edits were **23 vs 22**. Useful recall gain, not an overall/default upgrade. |
| `JacobLinCool/whisper-large-v3-verbatim-1` | Same three-excerpt study: **17/20 fillers**, **102 diagnostic edits**, including a severe repetition loop. Tested configuration stopped; failure was not trimmed away. |
| `Revai/reverb-asr` Research v1 | Separate 30s/84-word screen: **7 literal edits, 4/5 fillers, 1 false filler**; saved Turbo raw text: **6 edits, 5/5, 1 false**. Same content-to-filler hazard persisted. Negative screen accepted; configuration stopped. Controls had different hints/decoding, so this is not causal proof of model inferiority. |
| CrisperWhisper 2.0 medium/large | Recovered fillers, but also produced a human-confirmed spurious filler split from a word-internal vowel. Native timing did not qualify. No further experiments/integration currently authorized; operational rights unresolved. |
| Native CTC, Montreal Forced Aligner; Qwen forced-alignment exploration | No engine qualified. In one matched **46-boundary** audit, CTC achieved **41/46 within 80ms**, MFA **40/46**; 95% requires 44. Qwen was not part of that matched denominator. |
| Forced-fit validity / CTC likelihood as word-presence evidence | Unspoken words received finite fits; likelihood could prefer deleting genuine speech. Likelihood-only discriminator rejected. |
| Flat minimum-phone-edit deletion counting (synthetic) | Equally optimal paths disagreed on whole-word deletion counts in repetitions. Criterion abandoned rather than selecting a convenient traceback. |
| Independent phoneme CTC + exact-context/empty-gap assessor | Recorded cached pilot detected **0/2 clear negatives**; **18 were unrun**. Frozen-version ceiling **18/20 < required 19**. Mostly abstaining on genuine speech with zero observed false accusations did not establish safety. Baseline closed for qualification. |

The three-excerpt **20 genuine fillers**, the assessor's **20 clear-negative cases**, and the legacy screen's **five fillers** are different denominators. The table uses corrected references rather than earlier provisional filler accusations. Historical raw model predictions are not distributed here; aggregate records are in Git. Synthetic scorer/adapter success is not acoustic validation.

Model/code revisions, weights, effective calls and runtime settings are retained in the [2026-11 round record](benchmarks/recognition/rounds/2026-11.json). These are complete-profile comparisons, not isolated effects of fine-tuning. The 22/23/102 formatting diagnostic was introduced after earlier baseline/Turbo outputs were inspected, before full-AMI inference; it is not independent validation or raw WER.

## Guidance for future rounds

- **Look for new evidence, not another settings sweep.** Prefer released English verbatim checkpoints with natural conversational/disfluent evaluation, held-out speakers/recordings, filler precision/recall and content-preservation results—not only fluent-speech WER or cleanup benchmarks.
- **Check feasibility early:** accessible weights, reproducible revisions, local runtime, disk/memory, safe loading and native output semantics. Code, weights and outputs may have different licenses. Permitted personal non-production research can precede operational viability review; user-managed installation does not override restrictions.
- **Request a fresh, bounded experiment budget.** Use baseline outputs from the same documented corpus. If those outputs are unavailable, budget new baseline runs explicitly; do not present comparisons with historical aggregate numbers as paired tests. Keep model selection separate from decoding/text policy. Do not replay failed experiments to tune away their outcomes; a genuinely new release requires a new candidate protocol.
- **Do not repair evidence:** preserve genuine fillers/repetitions, literal ownership and native clocks. No fabricated/interpolated/stretched intervals or reference-overlap veto. Preserve blank-bearing indices when decoding phoneme offsets; sparse/empty predicted phones are not proof of silence.
- **Require fresh validation before promotion.** Timing target: **95% of boundaries within 80ms**, with 40ms reported separately; speech coverage/pause rejection and **95% representative normal completion** also remain open. A successful API return with an output loop is not normal completion. The rejected word-assessor study separately required 19/20 clear negatives; confirmed false accusations hold automatic-blocking promotion.
- **Preserve consent and accepted behavior.** The included recordings and annotations have explicit owner approval for Git. Do not upload unapproved media, credentials or restricted model outputs. Keep research outside product defaults; integration and releases need separate approval.

For each future round, retain approved corpus material and a **git-tracked round record**: model/code/weight pins, runtime/settings, input/reference hashes, metric definitions, denominators, results, failures, limitations and acceptance/stop decision. Avoid hidden dependencies; explicitly identify anything that cannot be redistributed. Version reference changes rather than overwriting earlier truth.

After checkout, run the [corpus integrity check](benchmarks/recognition/README.md#verify-after-checkout). Resume research with a bounded primary-source review; model/data downloads, training, inference and grading require a separately approved protocol and budget.
