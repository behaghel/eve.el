# Decision: literal-text word identity before independent alignment

**Status:** Accepted

## Context

A timed editing token must remain an atomic unit during display editing, cuts
and highlighting. A recognizer's segment text and its SDK word-timestamp output
can represent the same characters with different word boundaries. In the pinned
faster-whisper tokenizer, `split_tokens_on_spaces` starts a new item at an
apostrophe punctuation token; subsequent letters can attach to that item.
`merge_punctuations` does not reattach a non-punctuation item such as `'M`.
Consequently, literal `I'M` can appear as SDK words `I` and `'M`, even without
neural inference. These are not two separately recognized spoken words.

The literal text is recognition output, not a human reference. It can contain
wrong words, spelling, repetitions or fillers; lexical grouping must not quietly
correct any of those errors or certify that the predicted words were spoken.

## Earlier baseline

The faster-whisper producer uses SDK word strings as original token identity
and SDK boundaries as estimates or independent-alignment comparisons. This path
remains the default. Its use with already-word-tokenized models does not justify
silently changing existing timelines or all backend adapters.

## Decision

The explicit trained-verbatim profile establishes original word identity from
whitespace-delimited literal segment text before independent native CTC alignment.
After removing SDK whitespace, fragments must tile each text word exactly in
order, with no fragment crossing a text-word boundary. Reject ambiguity, changed
characters or missing text/fragments rather than infer corrections. Preserve
literal text, original SDK fragments/boundaries and the exact mapping in the
alignment audit. Existing TJM tokens are never retokenized by this policy.

Each whole word obtains its authoritative interval independently from native
acoustic evidence. A multi-fragment word has no combined SDK comparison interval;
its fragment estimates remain audit-only. A one-fragment word retains its
available comparison estimate. The existing non-blocking comparison policy and
all authoritative timing/model/source validation remain in force.

Caption splitting follows alignment and preserves word intervals, source order,
selected media coverage and timeline gaps. A valid recognizer-selected media
envelope does not become invalid merely because a lexical word lacks one
independent SDK estimate. This does not change the separate render gap policy
or claim coverage of every spoken event in the original recording.

## Alternatives rejected

- **Ship independently editable SDK contraction fragments:** exposes a tokenizer
  representation artifact as two spoken-word identities.
- **Join or redistribute fragment timestamps:** produces a synthetic interval
  rather than an independent estimate of the whole word.
- **Change punctuation-merging settings to absorb spoken suffix letters:** the
  SDK punctuation merge is not evidence for the absorbed letters' acoustic end.
- **Turn off word timestamps and assume equivalent recognition output:** changes
  the evaluated decoder/seek behavior without establishing equivalence.
- **Repair capitalization, spelling, names, numbers or grammar while grouping:**
  changes recognition content; no human transcript or model consensus supplies
  automatic production truth.
- **Retokenize published TJM or globally replace backend token policy:** changes
  existing source identities and unrelated/default behavior.

## Consequences and revisiting

Model selection remains separate from decoding/token policy. Frontends retain
existing backend/model settings and select the generic recognition profile
independently; compatible local checkpoints need configuration changes, not
model-specific modes or registry entries. Unsupported combinations reject
instead of silently substituting a model/policy. A new backend or materially
different decoding requirement still needs explicit adapter support and tests,
not an assumption that a generic model string implies compatibility or quality.

This profile requires independent native CTC and its supported transcript
alphabet/forms. Exact grouping can reject SDK/text disagreements instead of
publishing guessed token bindings. Runtime audit/model fingerprints identify
what ran; they do not verify upstream provenance or qualify word presence,
recognition fidelity, acoustic boundaries, pause handling or normal completion.
No qualification target or force rule is weakened.

Revisit the adapter if the pinned SDK supplies correct whole-word identities and
independently evidenced whole-word boundaries, or if a validated upstream fix
makes this policy unnecessary. Compare actual pinned behavior before removing
it; do not weaken identity or timing guarantees to accommodate a new backend.

Normative behavior lives in [TJM §15.2](../tjm-spec.md#152-literal-text-recognition-and-trained-verbatim-profile).
