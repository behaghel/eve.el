# Migration Notes

Current source of truth in this repository:

- `modules/home/video-editing/transcribe.nix`
- `modules/home/video-editing/text-edit.nix`
- `modules/home/video-editing/trim-fillers.nix`
- `modules/home/video-editing/denoise.nix`
- `modules/home/video-editing/batch.nix`
- `tests/video-editing.nix`

Planned migration target inside this subtree:

- `cli/src/eve_cli/commands/transcribe.py`
- `cli/src/eve_cli/commands/text_edit.py`
- `cli/src/eve_cli/commands/trim_fillers.py`
- `cli/src/eve_cli/commands/denoise.py`
- `cli/src/eve_cli/commands/batch.py`
- `cli/tests/`

TJM v1.1 migration order in this repository:

1. Replace the draft TJM direction with a normative v1.1 specification in
   `docs/tjm-spec.md`.
2. Build a conformance-oriented v1.1 test suite and JSON fixtures under
   `cli/tests/`.
3. Migrate producers to emit and annotate TJM v1.1 data.
4. Migrate `text-edit` to consume the exact timing and deterministic render
   contract.
5. Migrate `eve.el` to preserve and expose the new v1.1 structure while keeping
   media semantics in the CLI.
