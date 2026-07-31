# Project Agent Instructions

## TJM specification is authoritative

- Treat `docs/tjm-spec.md` as the normative contract for TJM behavior.
- Before changing manifest semantics, rendering behavior, validation, Emacs editing behavior, or CLI output, read the relevant section of `docs/tjm-spec.md` first.
- If implementation behavior changes or an ambiguity is discovered, update `docs/tjm-spec.md` in the same change. The spec must stay current, authoritative, and test-backed.
- Add or update tests that prove the spec requirement is implemented. Prefer focused regression tests for the exact behavior being clarified.
- User-facing documentation in `docs/eve.org` should explain workflows, but it must not contradict `docs/tjm-spec.md`.
