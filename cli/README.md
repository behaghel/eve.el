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
