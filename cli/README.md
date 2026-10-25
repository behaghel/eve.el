# CLI

This is the Python command layer for `eve.el`.

It is intentionally structured around subcommands that mirror the current
workflow living in `modules/home/video-editing/`:

- `transcribe`
- `text-edit`
- `trim-fillers`
- `denoise`
- `batch`

These subcommands now hold the migrated media-processing logic that used to live
in the Nix wrappers. They are tested for behavior parity and are the canonical
command surface for the project.

The CLI is also the primary TJM v1.1 producer and renderer implementation in
this repository. It owns exact-timing interpretation, filler-policy behavior,
and deterministic render planning.

## Transcription timing and v1.1 output

`eve transcribe input.mp4 --output edit.tjm.json` writes a validated v1.1
manifest. Each source declares its original audio sample grid as its timebase
(`1 / sample_rate`); backend seconds are converted once to nearest source-sample
ticks, with ties rounded to even. No intermediate millisecond rounding is used.
ASR's 16 kHz working audio retains leading media-time offsets; it does not change
the declared original-source clock. Source paths are relative to the manifest.

The output records explicit render settings: keep fillers and preserve no gaps
unless the manifest is subsequently edited. Available backend timing precision
is preserved, but this does not turn ASR estimates into sample-accurate acoustic
alignment. Missing, reversed, zero-duration or sub-tick-collapsed word intervals
produce a diagnostic rather than fabricated durations. Failed validation leaves
an existing output untouched; successful manifests are published atomically
after validation.

### Opt-in automatic native CTC alignment

```bash
devenv -q shell -- bash -c 'cd cli && uv run --no-sync eve transcribe \
  /path/input.mp4 --output /path/edit.tjm.json --word-aligner ctc'
```

This uses the declared `alignment` extra, English faster-whisper recognition and
unchanged pinned Wav2Vec2 character-span estimates. It never reads human timing
annotations, refines quiet edges or interpolates word intervals. English unsigned
integers (including grouped thousands and sentence punctuation) are verbalized
with pinned `num2words` for acoustic matching only: `11,` stays `11,` in TJM while
CTC matches `ELEVEN`. Multiword readings remain one timed token. Four-digit
numbers compare cardinal/year readings and English conjunction variants against
the same acoustic emissions; the best native path supplies all word timings.
The selected readings and library version are recorded per source in
`alignment.sources[].normalization`. Reading scores are uncalibrated diagnostics.
Search is bounded to 32 pronunciation paths per source; ties or excess paths
fail rather than guess. Decimals, signed numbers, currencies, percentages,
leading-zero numbers and numeric compounds/ordinals remain unsupported.
Existing transcription without `--word-aligner ctc` is unchanged. Emacs users
can opt in with `eve-transcribe-word-aligner` set to `"ctc"`; a prefix argument
requests a per-run quality override. Normal runs never force or retry
automatically. Manual region retranscription remains unchanged.

Experimental status is a non-blocking notice. A recognizer/CTC start or end
boundary disagreement **greater than 160 ms** is recorded in
`alignment.diagnostics`, but does not block publication or require force:
disagreement alone does not establish that CTC is wrong. Unavailable recognizer
timing for comparison remains a blocking quality concern. `--force` may accept
such concerns after structural validation; it cannot override missing/invalid
native estimates,
overlap, source bounds, collapsed sample ticks, changed tokens or model errors.
Word alignment retains existing selected media rather than shrinking cuts to
character spans. Envelopes expand to contain aligned words, then intersecting
envelopes are partitioned jointly at midpoints between adjacent word intervals.
Their source-interval union is unchanged: no footage is duplicated or lost.
Non-overlapping captions and intended gaps stay unchanged. Only caption start/end
boundaries move; word estimates, token grouping and order never change.
Genuinely overlapping word estimates still fail, even with `--force`.

Every structurally valid alignment writes `OUTPUT.alignment-report.json` (or
`--alignment-report PATH`) with `publication_permitted` and the producer's
`alignment` record. Successful TJM includes the identical record: model/revision,
experimental qualification, diagnostics, concrete concerns and whether they were
overridden. Informational diagnostics alone never set `forced`, even if the
operator supplied `--force`.
The report is a validation decision, not a claim that manifest publication
succeeded. Hard failures do not replace previous manifests or decision reports.
A rerender with `eve text-edit ... --report render.json` preserves that audit.
Recorded overrides never disable render validation. Subtitle styling remains
owned by the manifest, not these flags.

Silero-VAD pause observations use gaps at least 250 ms long and an 80 ms margin
at each edge. They are explicitly **provisional and non-blocking**, pending
representative validation; they do not certify pause accuracy. No runtime check
certifies the 95%/80 ms reference-based target. Ordinary completion and quality
rejection rates must also be measured before claiming routine usability.

## Alignment benchmark (experimental)

The benchmark evaluates reference accuracy independently of the opt-in CTC
transcription path. Other candidate engines remain benchmark-only.
The opt-in benchmark uses a single-source v1.1 reference with reviewed word
intervals, plus the original audio. It reports every start/end error, nearest-rank
p95, worst error, and highlighted frames inside reference-labelled silent gaps.
The release distance target is at least 95% of boundaries within 80 ms;
40 ms is reported separately as a stretch target. Reference comparisons ignore
case and sentence punctuation, but never drop, reorder or substitute words.
The current silence diagnostic conservatively counts every annotated gap,
including small boundary-edge gaps; a zero count is stricter than the product
requirement to leave clear pauses unhighlighted. Full benchmark `pass` retains
that stricter check and is not an automatic per-clip accuracy certificate.
The benchmark also appends an unspoken token as a negative probe; positive
forced timestamps or model scores do not establish that a token was spoken.

From the project root, for example:

```bash
devenv -q shell -- bash -c 'cd cli &&
  uv run --extra alignment --extra alignment-benchmark python \
    ../scripts/benchmark-alignment.py /path/reference.tjm.json \
    --audio /path/source.wav --engine qwen \
    --frame-times /path/video-frame-times.json \
    --output-dir /tmp/eve-alignment --render'
```

For video evaluation, `--frame-times` supplies JSON with a rational `time_base`
string and an array of native integer `pts`. Silence checks use those actual
presentation timestamps, including variable-rate video, instead of the default
60 fps grid used for controlled fixtures. Rejected predictions produce an
unscored failure report, not fabricated intervals or a made-up accuracy score.

Use `--engine ctc` for the English Wav2Vec2 baseline. Public model revisions are
pinned and downloaded to the local cache; audio inference stays local. With a
populated cache, `--offline` forbids model downloads. Native CTC is also available
through the explicit CLI opt-in described below; the other engines remain
benchmark-only. Qwen predictions are read before the SDK's interpolation
and monotonicity repair; invalid predictions fail instead of being repaired.

CTC acoustic inference uses nominal 10-second windows with 2-second overlapping
context on each side, following the [overlap/stride strategy](https://huggingface.co/blog/asr-chunking#chunking-with-stride).
Clips up to 10 seconds retain the original single-window inference path. Longer
inputs are sliced on the model's native convolution grid; receptive-field
coverage and the real source tail are included without padding. Each global
frame is retained exactly once, with no averaging, interpolation or time stretch.
The complete unchanged transcript is then forced-aligned against those emissions.
Unexpected window output lengths fail without repair. Per-source CLI audits and
CTC benchmark reports record the policy, actual sample windows, retained frame
ranges and native grid geometry. Windowing does not certify boundary accuracy or
prove that a forced token was spoken; experimental status and quality gates are
unchanged.

`--engine ctc-boundary` is a separate experimental CTC refinement, leaving the
character-span baseline unchanged. It uses acoustically aligned word-delimiter
ends, then adjusts only signal edges supported by at least 80 ms of adjacent
quiet. The signal detector uses 5 ms RMS windows with a floor of one PCM16 step
or 0.001 times the 95th-percentile RMS, whichever is larger. Boundary searches
are limited to 80 ms for starts and 200 ms for ends; these are eligibility
limits, never durations added to tokens. Unsupported interior boundaries are
not inferred by splitting signal activity. No fixed padding or interpolation
is applied. Signal activity and delimiter emissions are not proof of a word's
phonetic extent or that it was spoken; all original evaluation gates still apply.

The Apple Silicon phoneme baseline uses `--engine mfa`. Its native Kaldi/MFA
dependencies are isolated from the CLI in `scripts/alignment-mfa/pixi.toml` and
`pixi.lock`, with Pixi provided by devenv. Install that environment first:

```bash
devenv -q shell -- pixi install --manifest-path scripts/alignment-mfa/pixi.toml --locked
```

Then run the benchmark with `--engine mfa`; no Torch extras are needed for this
engine. The pinned [US English model by Michael McAuliffe](https://huggingface.co/MontrealCorpusTools/english_us_arpa)
(CC BY 4.0) supplies both the pronunciation dictionary and matching G2P model.
The historical single-utterance baseline disables speaker adaptation. Add
`--speaker-adaptation` with `--engine mfa` to test MFA's normal two-pass recipe.
That mode extracts the final adapted archive, not first-pass estimates. Timings
come from native integer lattice frames before TextGrid cleanup or end-of-file
adjustments.
Unknown-word substitution, token changes and invalid frame intervals fail rather
than being repaired. Engine/runtime failures are not counted as evidence that a
negative-probe token was unspoken.

`--engine ctc-phoneme` tests native phoneme alignment in overlapping contexts
computed from acoustic CTC anchors, not human reference times. Each context
includes one neighboring transcript word on either side; only the central word
is retained. Contexts extend up to 200 ms beyond the neighboring anchors and
are limited by the centers of outside anchors. These cuts define search windows,
never output word boundaries. Native frame indices are restored with the exact
PCM sample offset. Results less than one phoneme frame from a window edge,
failing to cover the CTC core with one 20 ms CTC frame of slack, or overlapping
another retained word are rejected, not moved or stretched. Rejection reports retain unaccepted
native diagnostics without manufacturing accuracy scores or dropping bad words.
The method requires both the CTC extras and the separate locked MFA environment.

For MFA-based experiments, `--pronunciations /path/confirmed.dict` supplies explicit
single-token pronunciation overrides: one word followed by whitespace-separated
ARPAbet phones per line. For example, `lt EH1 L T IY1` expresses the confirmed
letter names “ell tee” while keeping `LT` one spoken/display token. The benchmark
derives a temporary dictionary, replacing that word's existing pronunciations
without editing the cached model dictionary or override file. Phones must exist
in the pinned acoustic model; malformed, duplicate or unknown-phone entries fail
as configuration errors, never as evidence that a word was unspoken. The report
records the override file and its SHA-256. Other words retain dictionary/G2P
handling, and no timing values or evaluation tolerances change.

Reports and explicitly requested candidate videos are research artifacts, not
production-approved alignments. Rendering uses the reference's subtitle style.
The command returns nonzero when a gate fails. Passing one small fixture is not
a certification of natural-speech accuracy or general hallucination detection.

## Burned-in captions

Burning requires a supported v1.1-or-later manifest; currently only `"1.1"` is
supported. Legacy v1 burn-in requests are rejected with a re-transcription
message. Merely changing a legacy file's version field is not an upgrade.

The optional top-level `subtitles` object in a v1.1 TJM file controls burning; no
subtitle styling flags are needed. For example:

```json
{
  "subtitles": {
    "mode": "burn",
    "style": "plain",
    "font": { "family": "Arial", "size": 48, "bold": true },
    "placement": { "anchor": "bottom-center", "bottom_margin": 60 },
    "color": "#FFFFFF",
    "outline": { "color": "#000000", "width": 3 }
  }
}
```

Add that member to an otherwise complete TJM manifest, then run
`eve text-edit edit.tjm.json --output final.mp4 --quality final`. Each segment
appears as one caption, wrapping as needed, and captions disappear between
segments. Dimensions scale from the default 1920×1080 reference canvas. Burning
re-encodes the final video; audio is copied from the assembled cut.

The requested font family and weight must be installed: missing fonts cause an
error rather than silent substitution. Runtime prerequisites are FFmpeg with
libass and fontconfig (`fc-match`); the devenv environment provides the tools.
Choose an installed font in the manifest if Arial is unavailable.

Without this section, rendering does not burn captions. The existing
`--subtitles` option still generates WebVTT and an optional embedded track.
To highlight the currently spoken token, use `"style": "karaoke"` and add
`"highlight_color": "#FFD700"`. Each token's complete display fragment highlights
together, even when a display rewrite contains several written words. Tokens
return to the base color after speech, and pauses remain unhighlighted. This
requires word timings and segment `display_text` consistent with the token
display strings; timings are never inferred from rewritten text.

Rendering uses lossless intermediate audio and planned cut durations, encoding
AAC only for the assembled output. Subtitle burning copies that audio and
preserves video presentation timestamps. Highlight boundaries are evaluated
against actual frames, including variable-rate video. A timing boundary the
subtitle backend cannot represent causes an explicit error instead of an
approximate highlight.
