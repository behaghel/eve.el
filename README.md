# eve.el

`eve` is an Emacs major mode for editing Textual Join Manifest (`.tjm.json`)
files. A TJM manifest describes spoken-word video edits as ordered text
segments with per-word timing, optional markers, and b-roll metadata.

This repository is Emacs-package-first:

- `eve.el` is the main library entrypoint
- `test/` contains the ERT suite
- `docs/` captures the editing model and migration notes
- `cli/` contains Python executables that support the editing workflow

The mode is designed to work alongside the `eve` CLI, especially
`eve text-edit` and `eve transcribe`:

- edit words, segments, markers, and b-roll metadata in Emacs
- preview source segments with `mpv`
- compile the current manifest or a marker-delimited section through
  `eve text-edit`

## Features

- structured editing for per-word timed transcript segments
- segment split, merge, reorder, and deletion commands
- marker sections and section-scoped compilation
- b-roll metadata editing, placeholder editing, and continuation support
- validation and auto-correction for common timing issues
- raw JSON companion buffer for low-level edits

## Installation

Install `hydra`, then add the package to your `load-path` and require `eve`, or
install it with your preferred package manager.

### straight.el

```elisp
(use-package eve
  :straight (:type git :host github :repo "behaghel/eve.el"))
```

```elisp
(require 'eve)
```

Opening a `*.tjm.json` file enables `eve-mode` automatically.

## Project Layout

- main library: `eve.el`
- tests: `test/eve-test.el`
- workflow notes: `docs/video-text-editing.md`
- CLI package: `cli/`

## Development

The project uses `devenv` at the repository root.

```bash
devenv shell -- format
devenv shell -- parse
devenv shell -- checkdoc
devenv shell -- load-check
devenv shell -- compile
devenv shell -- lint
devenv shell -- test
devenv shell -- ci
devenv shell -- sync-py
devenv shell -- run-cli -- --help
```

`lint` runs the Emacs lint pipeline plus the Python static checks. `test` runs
ERT plus the Python CLI health suite. `ci` runs the full project verification.

The package has one hard Emacs dependency:

- `hydra`

The full workflow also expects these external tools at runtime:

- `mpv` for segment playback
- `eve text-edit` for manifest compilation
- `eve transcribe` or another transcript generator to create TJM
  manifests

Repo-specific `evil`, `general`, or leader-key integrations belong in the
consuming Emacs configuration rather than in the package itself.

## CLI

`cli/` is an `uv`-managed Python project. It is the home of the media-processing
commands that support the Emacs workflow.

Today the Python package provides the canonical `eve` command with migrated
subcommands for `transcribe`, `text-edit`, `trim-fillers`, `denoise`, and
`batch`. The Emacs package calls this CLI rather than embedding or depending on
Nix-hosted business logic.

## Verification

From the repository root:

```bash
emacs --batch -Q -L . -l eve.el --eval '(princ "ok")'
emacs --batch -Q -L . -l ert -l test/eve-test.el -f ert-run-tests-batch-and-exit
PYTHONPATH=cli/src python3 -m eve_cli doctor --json
devenv shell -- ci
```
