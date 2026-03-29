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

Recommended migration order:

1. Lift `video-transcribe` into Python modules and preserve its current CLI.
2. Lift `video-text-edit` with behavior-first parity tests.
3. Lift `video-trim-fillers` with fixture-based integration tests.
4. Consolidate `video-denoise` and `video-batch` into Python subcommands.
5. Relegate Nix to packaging and integration glue only.
