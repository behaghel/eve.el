# TJM v1.1 Specification

Status: Draft repository specification

## Abstract

This document specifies `TJM` (Textual Join Manifest) v1.1 for text-driven
spoken-word video editing.

TJM v1.1 is a JSON manifest format stored in `.tjm.json` files. It describes an
ordered timeline over one or more source media files, separates spoken content
from display-oriented text, carries exact timing through per-source tick values,
and defines a deterministic render contract for consumers that produce media
output.

This specification is normative for implementations that produce or consume TJM
compatible with the v1.1 contract adopted by this repository.

## 1. Scope

This specification defines:

- the on-disk JSON representation of TJM v1.1 manifests;
- the semantics of sources, segments, marker segments, word objects, and b-roll
  metadata;
- the fidelity layer for spoken words, display text, and filler annotations;
- the exact timing model based on source-local ticks;
- the deterministic render contract used to derive output media and subtitles;
- producer, consumer, validation, and conformance requirements.

This specification does not define:

- CLI argument syntax;
- Emacs user interface behavior;
- FFmpeg filter-graph internals;
- media codecs or container formats beyond manifest semantics.

## 2. Conventions and Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT",
"SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and
"OPTIONAL" in this document are to be interpreted as described in RFC 2119 and
RFC 8174 when, and only when, they appear in all capitals.

This document uses the following terms:

- `manifest`: a complete TJM JSON document.
- `producer`: software that creates or rewrites a TJM manifest.
- `consumer`: software that reads TJM for editing, validation, subtitle
  generation, or media rendering.
- `renderer`: a consumer that emits a media timeline from TJM.
- `source`: a media input declared in the top-level `sources` array.
- `timebase`: a rational clock for a source, expressed as `numerator` and
  `denominator`, where one tick equals `numerator / denominator` seconds.
- `tick`: an integer position measured in the timebase of a source.
- `segment`: an entry in the top-level `segments` array.
- `media segment`: a segment representing playable source media.
- `marker segment`: a segment whose `kind` is `"marker"`.
- `word object`: an entry in a segment's `words` array describing spoken
  content, display intent, semantic kind, and exact timing.
- `edit namespace`: an optional object on a segment or word that carries
  mutable editorial intent without changing source-local transcript facts.
- `spoken text`: the faithful spoken form represented by words.
- `display text`: the text intended for subtitles, review UIs, or other display
  consumers.
- `timeline order`: the manifest order of entries in `segments`; consumers build
  output in this order, not in source timestamp order.

## 3. File Identification and Encoding

### 3.1 File extension

Conforming TJM files SHOULD use the file name suffix `.tjm.json`.

### 3.2 Encoding

TJM is a JSON text format. Conforming producers MUST write a top-level JSON
object. UTF-8 encoding is RECOMMENDED.

Producers MUST NOT rely on comments, trailing commas, duplicate object keys, or
other JSON extensions.

Consumers MAY reject invalid JSON. If a consumer's JSON parser does not expose
duplicate-key information, behavior for duplicate keys is implementation-defined
and therefore non-conformant.

### 3.3 Character repertoire

String values MAY contain any valid JSON string content. Repository code writes
Unicode directly rather than ASCII-escaping it.

## 4. Top-Level Structure

A TJM v1.1 manifest MUST be a JSON object with the following top-level members:

- `version`: REQUIRED string format version;
- `sources`: REQUIRED array of source descriptors;
- `segments`: REQUIRED array of timeline entries;
- `render`: REQUIRED deterministic render configuration object.

Additional top-level members are allowed for forward compatibility. Editors
SHOULD preserve unknown members when practical.

Example skeleton:

```json
{
  "version": "1.1",
  "sources": [],
  "segments": [],
  "render": {
    "filler_policy": "keep",
    "preserve_short_gaps": 0.0
  }
}
```

## 5. Versioning

The only version defined by this specification is `"1.1"`.

Conforming producers MUST emit `"version": "1.1"`.

Conforming consumers MUST accept `"1.1"` and MUST treat any other value as
unsupported.

TJM v1.1 is not an additive alias for TJM v1. The v1 and v1.1 contracts differ
in field names, timing authority, and render semantics.

## 6. Sources

### 6.1 Structure

Each entry in `sources` MUST be an object with these members:

- `id`: REQUIRED string identifier, unique within the manifest;
- `file`: REQUIRED string path naming the media asset;
- `timebase`: REQUIRED object describing the source clock.

Example:

```json
{
  "id": "clip01",
  "file": "raw/interview.mp4",
  "timebase": {
    "numerator": 1,
    "denominator": 48000
  }
}
```

### 6.2 Path semantics

The `id` is the symbolic reference used by segments. Segment `source` values
refer to `sources[*].id`, not directly to a file path.

If `file` is relative, consumers MUST resolve it relative to the directory of
the manifest file. If `file` is absolute, consumers MUST use it as-is.

Producers targeting portability SHOULD prefer manifest-relative paths.

### 6.3 Timebase semantics

`timebase` MUST be an object with:

- `numerator`: REQUIRED positive integer;
- `denominator`: REQUIRED positive integer.

One source tick equals `numerator / denominator` seconds.

Every exact timing field tied to a source MUST be interpreted in that source's
timebase.

Producers MUST NOT emit duplicate source identifiers.

## 7. Segment Array and Timeline Semantics

### 7.1 Ordering

The `segments` array defines timeline order. Consumers MUST process segments in
array order.

Reordering a manifest is achieved by rearranging entries in `segments`. This
array order is the canonical way to reorder rendered output because each
segment's timing fields remain factual, source-local metadata attached to that
segment regardless of where it appears in the timeline.

Removing a segment from `segments` deletes it from the manifest. Producers that
need a non-destructive edit history MAY instead preserve the segment and mark
it for omission from rendered output with `segment.edit.deleted`.

### 7.2 Segment identity

Each segment MUST have an `id` string unique within the manifest.

### 7.3 Segment kinds

This specification defines two segment kinds:

- media segments;
- marker segments, identified by `"kind": "marker"`.

If `kind` is absent, the segment is a media segment.

Renderers MUST reject unknown segment `kind` values.

## 8. Media Segments

### 8.1 Required members

A media segment MUST contain:

- `id`: segment identifier;
- `source`: source identifier referring to a declared source;
- `start_tick`: exact source-local start tick;
- `end_tick`: exact source-local end tick.

`end_tick` MUST be strictly greater than `start_tick`.

### 8.2 Optional members

A media segment MAY additionally contain:

- `start`: numeric seconds convenience value;
- `end`: numeric seconds convenience value;
- `spoken_text`: convenience spoken-form string;
- `display_text`: display-form string;
- `words`: array of word objects;
- `speaker`: string or `null`;
- `edit`: segment edit metadata object.

Unknown segment members are allowed. Editors SHOULD preserve unknown members
when round-tripping.

### 8.3 Segment edit namespace

When present, `segment.edit` MUST be an object. It carries mutable editorial
instructions while `source`, `start_tick`, `end_tick`, `spoken_text`, and
`words` remain the factual transcript and timing record.

The following interoperable `segment.edit` members are defined in TJM v1.1:

- `deleted`: OPTIONAL boolean;
- `tags`: OPTIONAL array of strings;
- `notes`: OPTIONAL string, empty string, or `null`;
- `broll`: OPTIONAL b-roll object or `null`.

When `segment.edit.deleted` is `true`, the segment's content is marked for
removal from rendered output without erasing the segment's source-local timing
metadata from the manifest.

The same `segment.edit` namespace is available on both media segments and
marker segments.

Unknown members inside `segment.edit` are allowed for forward compatibility.
Editors SHOULD preserve them when round-tripping.

Future extensions: later revisions may define additional `segment.edit`
controls such as `fade_in`, `fade_out`, `volume`, and `speed`. These names are
reserved as plausible future video-edit controls and are non-normative in TJM
v1.1.

### 8.4 Spoken and display semantics

`spoken_text` and `display_text` serve different roles:

- `spoken_text`, when present, represents faithful spoken content;
- `display_text`, when present, represents the text intended for subtitles or
  display consumers.

If `words` is present and non-empty, `spoken_text` SHOULD equal the `spoken`
values of `words`, joined with single spaces in array order.

If `display_text` is absent and `words` is present, consumers SHOULD derive it
by joining each word's display token in array order, omitting words whose
`kind` is `"filler"` when `render.filler_policy` is `"drop"`, and omitting
words whose `word.edit.deleted` flag is `true`.

The display token for a word is:

1. `word.display` when present and non-empty;
2. otherwise `word.spoken`.

### 8.5 Segment-level timing and words

If a media segment supplies `words`, the segment's `start_tick` and `end_tick`
SHOULD match the first and last remaining word timing after any editing step.

Consumers that implement word-level editing SHOULD preserve word order exactly.

## 9. Word Objects

Each entry in `words` MUST be an object.

Conforming word objects use:

- `start_tick`: REQUIRED integer;
- `end_tick`: REQUIRED integer;
- `spoken`: REQUIRED string;
- `kind`: OPTIONAL string;
- `display`: OPTIONAL string;
- `edit`: OPTIONAL word edit metadata object;
- `start`: OPTIONAL numeric seconds convenience value;
- `end`: OPTIONAL numeric seconds convenience value.

`end_tick` MUST be strictly greater than `start_tick`.

If `kind` is absent, consumers MUST treat the word as if `kind` were
`"lexical"`.

The interoperable `kind` values in TJM v1.1 are:

- `lexical`: an ordinary spoken word;
- `filler`: a spoken filler or disfluency such as `um` or `uh`.

Editors SHOULD preserve recognized and unrecognized `kind` values when
round-tripping. Renderers MUST reject unknown `kind` values.

`kind` is descriptive metadata. It does not itself delete or rewrite the word.

When present, `word.edit` MUST be an object. The following interoperable
`word.edit` member is defined in TJM v1.1:

- `deleted`: OPTIONAL boolean.

When `word.edit.deleted` is `true`, that word's content is marked for removal
from rendered output without rewriting the underlying transcription facts.

Unknown members inside `word.edit` are allowed for forward compatibility.
Editors SHOULD preserve them when round-tripping.

Producers SHOULD trim surrounding whitespace from `spoken` and SHOULD omit
zero-content words.

## 10. Exact Timing Model

### 10.1 Timing authority

Exact timing in TJM v1.1 is carried by tick fields, not float seconds.

The authoritative timing fields are:

- `segments[*].start_tick`
- `segments[*].end_tick`
- `segments[*].words[*].start_tick`
- `segments[*].words[*].end_tick`

Renderers MUST use tick fields when constructing media output.

### 10.2 Convenience seconds

The numeric `start` and `end` fields on segments and words are convenience
values for display, review, and human editing.

Convenience seconds MUST NOT override tick fields.

When a producer emits both ticks and seconds, the seconds SHOULD be derived from
the associated source timebase.

### 10.3 Word bounds inside segments

If `words` is present:

- each word MUST use the same source timebase as its parent segment;
- each word MUST satisfy `segment.start_tick <= word.start_tick < word.end_tick
  <= segment.end_tick`.

## 11. Marker Segments

### 11.1 Structure

A marker segment MUST contain `"kind": "marker"`.

A marker segment SHOULD also contain:

- `id`: marker identifier;
- `title`: human-readable heading.

A marker segment MAY also contain:

- `source`: source hint;
- `start_tick`: source-relative time hint;
- `start`: seconds convenience hint;
- `display_text`: fallback display text;
- `edit`: segment edit metadata object;
- `duration`: renderer-facing duration value.

### 11.2 Semantics

Markers are logical headings in the segment stream. A marker without renderable
`segment.edit.broll` does not itself contribute playable media and MUST be
ignored for normal segment rendering.

Markers MAY still contribute structure to downstream consumers, such as section
labels or subtitle annotations.

If a marker includes renderable `segment.edit.broll` metadata, a renderer MAY
treat it as a timeline unit only when enough information exists to derive
duration.

## 12. B-roll Object

### 12.1 Core structure

The `segment.edit.broll` member, when present, MUST be an object with at least:

- `file`: REQUIRED string path.

The following interoperable members are defined:

- `mode`: OPTIONAL string, default `"replace"`;
- `audio`: OPTIONAL string, default `"source"`;
- `start_offset`: OPTIONAL time value;
- `duration`: OPTIONAL time value;
- `still`: OPTIONAL boolean;
- `continue`: OPTIONAL boolean;
- `position`: OPTIONAL object used by picture-in-picture mode;
- `overlays`: OPTIONAL renderer extension array;
- `placeholders`: OPTIONAL renderer extension object.

If `file` is relative, consumers MUST resolve it relative to the manifest
directory.

### 12.2 Supported modes and audio policies

The interoperable `mode` values are:

- `"replace"`
- `"pip"`

The interoperable `audio` values are:

- `"source"`
- `"broll"`

Producers MUST NOT emit `mode` or `audio` values outside these sets.

### 12.3 Still-image rules

If `still` is `true`, the consumer SHOULD treat the referenced asset as a still
image to be converted into a synthetic video stream of the requested duration.

If `still` is `true`, `audio` MUST NOT be `"broll"`.

### 12.4 Picture-in-picture positioning

When `mode` is `"pip"`, `position` MAY contain:

- `x`: normalized horizontal origin from `0` to `1` inclusive;
- `y`: normalized vertical origin from `0` to `1` inclusive;
- `width`: normalized width fraction, greater than `0` and less than `1`.

Consumers SHOULD default `x` and `y` to `0.05`, and `width` to `0.3` when
values are absent.

### 12.5 Time values

`segment.edit.broll.start_offset`, `segment.edit.broll.duration`, and marker
`duration` MAY be expressed as either:

- a JSON number of seconds; or
- a string in one of these forms:
  - `SS[.fff]`
  - `MM:SS[.fff]`
  - `HH:MM:SS[.fff]`

Consumers MUST interpret these forms as wall-clock durations.

### 12.6 Continuous b-roll chains

If adjacent segments carry equivalent b-roll definitions and the later segment's
`continue` flag is `true`, a rendering consumer MAY treat the sequence as a
continuous chain rather than restarting the asset at each segment.

Equivalence SHOULD be determined by the effective b-roll source, `mode`,
`audio`, `still`, `position`, and whether the referenced `file` is itself a
template JSON descriptor.

### 12.7 Template JSON extension

If `segment.edit.broll.file` names a `.json` file, a renderer MAY interpret it
as a template descriptor rather than as media directly.

The current repository renderer expects such a template file to decode to a JSON
object with:

- `template`: REQUIRED media path;
- `overlays`: OPTIONAL array of drawtext-like overlay descriptors;
- `placeholders`: OPTIONAL object mapping placeholder names to values.

If both the template file and the segment's `segment.edit.broll` object provide
`placeholders`, the segment-local `placeholders` override template defaults.

If the segment's `segment.edit.broll` object provides `overlays`, those entries
replace the template's `overlays` for that segment.

## 13. Deterministic Render Contract

### 13.1 Render object

The top-level `render` object MUST contain:

- `filler_policy`: REQUIRED string;
- `preserve_short_gaps`: REQUIRED non-negative number of seconds.

The interoperable `filler_policy` values are:

- `"keep"`
- `"drop"`

### 13.2 Rendering order

A renderer MUST walk `segments` in file order.

For a normal media segment, the renderer renders the interval from
`start_tick` to `end_tick` from the referenced source, optionally modified by
`segment.edit.broll` and `render.filler_policy`.

If `segment.edit.deleted` is `true`, the renderer MUST omit that segment from
rendered output while leaving the manifest entry itself intact.

If `word.edit.deleted` is `true`, consumers MUST omit that word from rendered
output and from display-text derivation in the same way they would omit any
other word intentionally removed by an edit workflow.

### 13.3 Filler policy behavior

When `render.filler_policy` is `"keep"`, filler words do not alter source-media
selection by themselves.

When `render.filler_policy` is `"drop"` and a segment has `words`, the renderer
MUST remove the source-media intervals covered by words whose `kind` is
`"filler"`. The remaining non-filler, non-deleted intervals from that segment
MUST be concatenated in word order.

### 13.4 Missing source and zero-duration behavior

A renderable media segment MUST have a resolvable `source`.

A renderer MUST treat the following as errors:

- missing referenced source;
- `end_tick <= start_tick`;
- any retained media interval with zero or negative duration;
- unknown segment `kind`;
- unknown word `kind`;
- unsupported `segment.edit.broll.mode` or `segment.edit.broll.audio` values.

Unlike TJM v1, source-less b-roll fallback is not part of the v1.1 deterministic
render contract.

### 13.5 Short-gap preservation

Short-gap preservation is part of the manifest contract in TJM v1.1.

The following semantics apply:

- only positive gaps between consecutive retained intervals from the same source
  are eligible;
- a gap is preserved only when its duration is less than or equal to
  `render.preserve_short_gaps`;
- preserved gap media occupies real time in the output timeline.

If a continued b-roll chain spans such a boundary, a renderer MAY choose not to
insert the source gap only when that choice is documented as part of the
renderer's supported b-roll behavior. Repository consumers SHOULD keep this
behavior stable across runs.

## 14. Subtitle and Display Cue Generation

For consumers that derive subtitles from TJM:

- subtitle time starts at `0` in rendered timeline space, not source-media time;
- marker segments do not emit normal subtitle cues;
- preserved short gaps advance subtitle time;
- segment cue text MUST be determined as follows:
  1. use non-empty `display_text` when present;
  2. otherwise derive text from `words` using the display-token rules in
     Section 8.4;
  3. otherwise fall back to the segment `id`.

If `speaker` is present and cue text is non-empty, consumers SHOULD prefix the
cue text with `"<speaker>: "`.

Consumers that separately expose marker annotations MAY map markers into
timeline-space labels using the accumulated rendered duration at the point where
the marker appears. When deriving a marker label, consumers SHOULD prefer
`title`, then `display_text`, then `id`.

## 15. Producer Requirements

A conforming TJM v1.1 producer:

- MUST emit a valid JSON object with `version`, `sources`, `segments`, and
  `render`;
- MUST emit `"version": "1.1"`;
- MUST emit a `timebase` for every source;
- MUST emit `start_tick` and `end_tick` for every media segment;
- MUST ensure every referenced `source` exists in `sources`;
- MUST ensure segment order reflects intended output order;
- MUST emit supported `segment.edit.broll.mode` and
  `segment.edit.broll.audio` values only;
- MUST emit supported `render.filler_policy` values only;
- SHOULD emit stable, unique `id` values for sources and segments;
- SHOULD emit `display_text` explicitly on spoken media segments;
- SHOULD preserve unknown members when editing an existing manifest unless the
  producer intentionally normalizes them away;
- SHOULD write manifest-relative paths for portable manifests;
- SHOULD write UTF-8 JSON.

Plain transcription producers MAY omit `kind` on all words. In that case,
consumers treat the words as lexical by default.

## 16. Consumer Requirements

A conforming TJM v1.1 consumer:

- MUST accept TJM v1.1 manifests that satisfy this specification;
- MUST reject unsupported `version` values;
- MUST process `segments` in array order;
- MUST resolve `source` values through the top-level `sources` array;
- MUST resolve relative paths against the manifest directory;
- MUST use tick fields as the authoritative timing source;
- MUST support media segments;
- MUST support marker segments as non-playable structural entries unless they
  carry renderable `segment.edit.broll` with usable duration data;
- SHOULD preserve unknown members for forward compatibility when operating as an
  editor or round-tripper.

## 17. Validation and Error Handling

For interoperable implementations, producers and validators MUST report at least
the following as invalid for media segments:

- missing `source`, `start_tick`, or `end_tick`;
- `end_tick <= start_tick`;
- a `words` entry with `end_tick <= start_tick`;
- a word timing that falls outside the enclosing media segment;
- a source without a valid `timebase`;
- a non-boolean `segment.edit.deleted` or `word.edit.deleted` value;
- `still: true` combined with `audio: "broll"`;
- unsupported `segment.edit.broll.mode` or `segment.edit.broll.audio` values;
- unsupported `render.filler_policy` values.

The following checks are RECOMMENDED for robust implementations:

- duplicate source or segment identifiers;
- empty or whitespace-only `spoken` values;
- disagreement between convenience seconds and exact tick-derived timing;
- missing `display_text` when a consumer requires explicit display strings.

Marker segments are validated differently. A marker without timing remains valid
as a structural entry.

## 18. Forward Compatibility

Unknown object members are reserved for future extension.

To preserve forward compatibility:

- producers SHOULD avoid deleting unknown members when round-tripping existing
  manifests;
- non-rendering consumers SHOULD ignore unknown members unless those members are
  required for the consumer's specific operation;
- renderers MAY reject unknown members only when they affect required rendering
  behavior.

This specification does not standardize canonical key ordering.

## 19. Conformance

An implementation is a conforming TJM v1.1 producer if it satisfies Section 15
and emits manifests matching the syntax and semantics in Sections 3 through 13.

An implementation is a conforming TJM v1.1 consumer if it satisfies Section 16
and correctly interprets manifests according to Sections 4 through 14.

An implementation MAY conform as a producer only, a consumer only, or both.

### 19.1 Conformance matrix

At minimum, a v1.1 implementation SHOULD be tested against these cases:

- valid minimal v1.1 manifest;
- valid filler-aware manifest with `render.filler_policy = "drop"`;
- valid marker and b-roll manifest;
- invalid version value;
- invalid or missing source timebase;
- invalid filler word timing outside its segment;
- invalid unsupported render policy or b-roll mode.

## Appendix A. Valid Minimal Manifest

```json
{
  "version": "1.1",
  "sources": [
    {
      "id": "clip01",
      "file": "raw/interview.mp4",
      "timebase": {
        "numerator": 1,
        "denominator": 48000
      }
    }
  ],
  "render": {
    "filler_policy": "keep",
    "preserve_short_gaps": 0.0
  },
  "segments": [
    {
      "id": "clip01-s0001",
      "source": "clip01",
      "start_tick": 0,
      "end_tick": 43200,
      "display_text": "hello world",
      "spoken_text": "hello world",
      "words": [
        {
          "start_tick": 0,
          "end_tick": 19200,
          "spoken": "hello"
        },
        {
          "start_tick": 19200,
          "end_tick": 43200,
          "spoken": "world"
        }
      ],
      "edit": {
        "tags": [],
        "notes": "",
        "broll": null
      }
    }
  ]
}
```

## Appendix B. Valid Filler-Aware Manifest

```json
{
  "version": "1.1",
  "sources": [
    {
      "id": "clip01",
      "file": "raw/interview.mp4",
      "timebase": {
        "numerator": 1,
        "denominator": 48000
      }
    }
  ],
  "render": {
    "filler_policy": "drop",
    "preserve_short_gaps": 0.2
  },
  "segments": [
    {
      "id": "clip01-s0001",
      "source": "clip01",
      "start_tick": 0,
      "end_tick": 49920,
      "display_text": "we launched it",
      "words": [
        {
          "start_tick": 0,
          "end_tick": 9600,
          "spoken": "um",
          "kind": "filler"
        },
        {
          "start_tick": 9600,
          "end_tick": 15600,
          "spoken": "we"
        },
        {
          "start_tick": 15600,
          "end_tick": 38000,
          "spoken": "launched"
        },
        {
          "start_tick": 38000,
          "end_tick": 49920,
          "spoken": "it"
        }
      ]
    }
  ]
}
```

## Appendix C. Valid Marker and B-roll Manifest

```json
{
  "version": "1.1",
  "sources": [
    {
      "id": "clip01",
      "file": "raw/interview.mp4",
      "timebase": {
        "numerator": 1,
        "denominator": 48000
      }
    }
  ],
  "render": {
    "filler_policy": "keep",
    "preserve_short_gaps": 0.0
  },
  "segments": [
    {
      "id": "marker-001",
      "kind": "marker",
      "title": "Recap"
    },
    {
      "id": "clip01-s0001",
      "source": "clip01",
      "start_tick": 0,
      "end_tick": 48000,
      "display_text": "shipping update",
      "edit": {
        "broll": {
          "file": "broll/card.mp4",
          "mode": "replace",
          "audio": "source",
          "duration": "00:01.000"
        }
      }
    }
  ]
}
```

## Appendix D. Invalid Example Discussion

The following fragments are invalid or non-conformant under this specification:

1. Unsupported version:

```json
{"version": 1}
```

Reason: TJM v1.1 requires the exact version string `"1.1"`.

2. Missing source timebase:

```json
{
  "id": "clip01",
  "file": "raw/interview.mp4"
}
```

Reason: every source requires a `timebase`.

3. Invalid media timing:

```json
{
  "id": "seg-1",
  "source": "clip01",
  "start_tick": 500,
  "end_tick": 400
}
```

Reason: `end_tick` MUST be strictly greater than `start_tick`.

4. Word outside segment bounds:

```json
{
  "id": "seg-1",
  "source": "clip01",
  "start_tick": 0,
  "end_tick": 100,
  "words": [
    {
      "start_tick": 90,
      "end_tick": 120,
      "spoken": "late"
    }
  ]
}
```

Reason: word timing must stay within its parent segment.

5. Unsupported filler policy:

```json
{
  "render": {
    "filler_policy": "highlight",
    "preserve_short_gaps": 0.0
  }
}
```

Reason: `render.filler_policy` only allows `"keep"` and `"drop"`.
