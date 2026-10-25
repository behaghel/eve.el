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
