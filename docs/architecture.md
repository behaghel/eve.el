# Architecture

This project follows a strict two-layer boundary.

## Emacs package root

The Emacs package owns:

- major or minor modes
- interactive editing commands
- playback integration
- asynchronous process launching
- parsing machine-readable CLI output
- user customization and validation display

The Emacs layer must not become a second implementation of media-processing
rules.

## `cli/`

The Python CLI owns:

- manifest generation
- denoise orchestration
- filler classification and render policy logic
- text-based render planning and deterministic timeline construction
- ffmpeg and ffprobe invocation
- machine-readable diagnostics for Emacs

The Python CLI is where the current business logic from
`modules/home/video-editing/` should move.

## Shared contract

The shared contract between `eve.el` and `cli/` is:

- TJM v1.1 files on disk as specified in `docs/tjm-spec.md`
- structured JSON command output where appropriate
- stable exit codes

This keeps the Emacs package thin and prevents duplicated media semantics.
