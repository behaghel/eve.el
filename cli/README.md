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
