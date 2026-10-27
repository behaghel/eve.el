# Decision: unavailable auxiliary recognition timing

**Status:** Accepted

## Context

An independently aligned word has an authoritative acoustic interval and may
also have a recognizer interval used for comparison. These serve different
roles. A genuinely spoken filler can have a zero-duration recognizer interval
while independent alignment supplies a positive native interval. Requiring
force for that case obstructs an otherwise structurally valid automatic run.

A recognizer/alignment disagreement greater than 160 ms is already diagnostic,
not proof that the independent estimate is wrong. Neither an available nor an
unavailable comparison certifies word presence or boundary accuracy.

## Earlier baseline

Missing or invalid comparison timing was a blocking quality concern. An explicit
per-run force option could permit publication after independent structural
validation, without changing the estimates.

## Decision

After independent acoustic alignment passes structural validation, unavailable
recognizer comparison timing is a non-blocking diagnostic. Record the original
comparison boundaries in the alignment audit shared by the manifest and decision
report. Strings and null retain non-finite or missing values without invalid
JSON. Diagnostic-only runs do not record a quality override, even if force was
supplied.

This does not apply when recognizer timing is authoritative. Invalid authoritative
intervals, unavailable/failed models, changed token identity/order and source
integrity failures still reject publication. Existing historical audit records
are not reclassified or rewritten by consumers.

## Alternatives rejected

- **Keep the veto and force automatically:** disguises an override and violates
  the normal-run contract.
- **Keep a mandatory manual override:** makes auxiliary comparison availability
  a production intervention without establishing authoritative timing quality.
- **Repair the recognizer interval, drop the word or redistribute timing:** changes
  estimates or transcript identity merely to satisfy validation.
- **Remove structural validation:** confuses missing comparison evidence with
  valid authoritative timing.

## Consequences and revisiting

Automatic publication can proceed without an auxiliary comparison. It remains
experimental where qualification is incomplete; recognition fidelity, unspoken
word rejection, pause behavior and reference-based timing accuracy still need
independent evidence. The accuracy and operational completion targets do not
change.

Revisit this policy if representative validation establishes a reliable blocking
quality check from comparison availability. A future check needs measured
benefit and false-rejection behavior; model disagreement or a missing comparison
alone is not accuracy certification.

Normative behavior lives in [TJM §15.1](../tjm-spec.md#151-automatic-alignment-and-quality-overrides).
