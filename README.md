# eve.el

`eve` is an Emacs major mode for editing Textual Join Manifest (`.tjm.json`)
files. A TJM manifest describes spoken-word video edits as ordered text
segments with per-word timing, optional markers, and b-roll metadata.

The normative TJM v1.1 format specification lives in `docs/tjm-spec.md`. The
workflow-oriented background and editing model live in
`docs/video-text-editing.md`.

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

## Emacs Transcription Commands

The package also ships two interactive entry points for starting `eve transcribe`
from Emacs:

- `M-x eve-transcribe` prompts for a directory, scans its immediate regular files
  non-recursively, keeps only supported media files, infers the output manifest
  path, and starts `eve transcribe` asynchronously.
- `M-x eve-dired-transcribe` runs from Dired, using the marked files or the
  current file when nothing is marked, then applies the same media filtering,
  manifest inference, and async launch.
- On success, the generated `.tjm.json` manifest is opened immediately, which
  enables `eve-mode` automatically.
- On failure, Emacs shows the `*eve transcribe*` buffer so you can inspect the
  command output.
- Emacs passes transcription settings through the customizable variables
  `eve-transcribe-backend`, `eve-transcribe-model`,
  `eve-transcribe-verbatim`, and `eve-transcribe-tag-fillers`.

If you want a Dired key for the command, add one in your own Emacs config:

```elisp
(with-eval-after-load 'dired
  (define-key dired-mode-map (kbd "C-c C-t") #'eve-dired-transcribe))
```

If you want to change the backend or model that Emacs launches, customize the
same variables it forwards to `eve transcribe`:

```elisp
(setq eve-transcribe-backend "faster-whisper"
      eve-transcribe-model "base.en"
      eve-transcribe-verbatim t
      eve-transcribe-tag-fillers t)
```

`eve transcribe` currently supports `faster-whisper`, `transformers`, and
`nemo` backends. `--verbatim` biases decoding toward spoken disfluencies on the
`faster-whisper` path; for the optional backends, pick a verbatim-friendly
model with `--model` or `eve-transcribe-model` instead. Practical starting
points are `nyrahealth/CrisperWhisper` with `--backend transformers` and
`nvidia/parakeet-ctc-1.1b` with `--backend nemo`.

The optional `transformers` and `nemo` backends need extra Python dependencies.
From the repo root, install them with `uv sync --project cli --extra
transformers` or `uv sync --project cli --extra nemo`; from `cli/`, use
`uv pip install .[transformers]` or `uv pip install .[nemo]`.

`--tag-fillers` and `eve-transcribe-tag-fillers` apply non-destructive filler
review during transcription by marking matching manifest words with
`kind: "filler"`. The standalone `eve tag-fillers` command applies the same
tagging to an existing TJM manifest without trimming media. `eve trim-fillers`
still exists today, but it is deprecated in favor of this manifest-tagging
workflow.

## Project Layout

- main library: `eve.el`
- tests: `test/eve-test.el`
- normative TJM specification: `docs/tjm-spec.md`
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
subcommands for `transcribe`, `text-edit`, `tag-fillers`, `trim-fillers`,
`denoise`, and `batch`. The Emacs package calls this CLI rather than embedding
or depending on Nix-hosted business logic. `trim-fillers` remains available for
now but prints a deprecation warning that points users to `eve tag-fillers`.

## Verification

From the repository root:

```bash
emacs --batch -Q -L . -l eve.el --eval '(princ "ok")'
emacs --batch -Q -L . -l ert -l test/eve-test.el -f ert-run-tests-batch-and-exit
PYTHONPATH=cli/src python3 -m eve_cli doctor --json
devenv shell -- ci
```
