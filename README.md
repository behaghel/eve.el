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
- phrase-level filler tagging, interactive add-at-point and add-region, and bulk delete
- right-margin timestamp ruler showing rendered timeline milestones
- live video playback tracking with mpv: segment highlighting, pause/resume, and seek-by-point
- optional orchestrated screen layout: video preview above, transcript below

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

## Compilation and Rendering

`C-c C-c` (`eve-compile`) compiles the current manifest — or the section under
point when on a marker — through `eve text-edit`.

Out of the box, compilation is tuned for a fast edit-preview loop:

- **Draft quality** by default — ultrafast encoding at lower fidelity so you
  see the result in seconds, not minutes.
- **Parallel encoding** — segments are rendered concurrently across all
  available CPU cores.
- **Segment cache** — unchanged segments are stored in `.eve-cache/` next to
  the manifest and reused across renders.  After a one-word edit only the
  affected segment is re-encoded; everything else is a cache hit.
- **Speculative pre-rendering** — on every save, changed segments are
  silently pre-rendered in the background so the cache is already warm when
  you hit `C-c C-c`.

For final delivery, press `C-u C-c C-c` to compile at full quality
(H.264 medium/CRF 18).

### Pre-flight checks

Before starting a render that would take more than
`eve-compile-confirm-threshold` seconds (default 30), `eve-compile` runs a
dry-run analysis and asks for confirmation.  It also validates the manifest
(missing source files, bad timecodes, absent b-roll) before committing to
an expensive encode.

### Compilation variables

| Variable | Default | Description |
|----------|---------|-------------|
| `eve-compile-quality` | `"draft"` | `"draft"` for fast preview, `"final"` for delivery |
| `eve-compile-jobs` | `0` | Parallel workers; 0 = auto-detect CPU count |
| `eve-compile-scale` | `1.0` | Resolution scale for draft (e.g. 0.5 = half res) |
| `eve-compile-cache` | `t` | Use the `.eve-cache/` segment cache |
| `eve-compile-confirm-threshold` | `30` | Seconds above which a confirmation is shown |
| `eve-auto-render` | `t` | Speculatively pre-render on save |
| `eve-auto-render-idle-seconds` | `2.0` | Idle delay before speculative render fires |

### CLI flags

`eve text-edit` exposes the full set of knobs for scripting or CI use.
Under normal Emacs usage you never need these — `--quality` drives the
defaults and everything else is resolved automatically.

```
--quality draft|final   Encoding profile (default: draft)
--codec h264|mjpeg      Override intermediate codec (draft→mjpeg, final→h264)
--scale FACTOR          Resolution scale (e.g. 0.5)
--jobs N                Parallel workers (0 = auto)
--cache-dir PATH        Segment cache location (default: .eve-cache/)
--no-cache              Disable segment cache
--cache-max-size GB     Cache eviction threshold (default: 10)
--validate              Check manifest without rendering
--dry-run               Report cache hits/misses and estimated time
--segments ID [ID …]    Pre-render only the listed segments into the cache
--no-resume             Ignore checkpoint from a prior interrupted render
--partial-every N       Write a partial .mp4 every N segments
```

## Filler Words

`eve-mode` supports non-destructive filler tagging. Words and phrases tagged as
fillers are highlighted in the buffer and excluded from the rendered video by
`eve text-edit`.

### Configuration

```elisp
;; Plain strings — single words or multi-word phrases
(setq eve-filler-phrases '("um" "uh" "you know" "to be honest with you"))
```

`eve-filler-phrases` is persisted via `customize-save-variable` when you use the
interactive add commands.

### Commands

| Command | Description |
|---------|-------------|
| `M-x eve-tag-fillers` | Tag all words/phrases matching `eve-filler-phrases` |
| `M-x eve-add-filler-at-point` | Add the word at point to `eve-filler-phrases` and retag |
| `M-x eve-add-filler-region` | Add the selected phrase to `eve-filler-phrases` and retag |
| `M-x eve-delete-fillers` | Mark all tagged filler words as deleted |

Fillers are tagged automatically on file open. The legacy
`eve-filler-regex` defcustom is still honoured for backwards compatibility.

## Timestamp Ruler

`eve-ruler-mode` (active by default) displays `[hh:mm:ss]` markers in the
right margin of the buffer. Each marker is aligned to the segment that reaches
that time milestone in the *rendered* video — deleted words and segments are
excluded from the running total.

The total rendered duration is also shown in the mode line next to the
filename.

```elisp
;; Change the interval between markers (seconds, default 30)
(setq eve-ruler-interval 60.0)
```

Toggle the ruler with `M-x eve-ruler-mode`.

## Video Playback and Tracking

`eve-mode` integrates with `mpv` via its JSON IPC socket to track playback
in real time. The currently-playing segment is highlighted with a vivid amber
overlay (`eve-playback-face`) that is distinct from both the focus highlight
and the region selection.

### Playing the source file

```
M-x eve-play-source
```

Plays the source media from the current segment to the end of file, tracking
all segments from the same source clip. The playback position maps directly to
the original recording timestamps.

### Playing the rendered output

```
M-x eve-play-rendered
```

Plays the compiled output file. If the output is missing or older than the
TJM manifest, `eve-mode` saves the buffer and runs `eve text-edit`
automatically before starting playback. Progress is tracked using the
post-edit rendered timeline, so deleted content is skipped.

### Playback controls

During playback, `SPC` is remapped to `eve-playback-pause-resume`.

| Key / Command | Action |
|---------------|--------|
| `SPC` | Pause / resume |
| Move point to a segment | Seek to that segment |
| `C-c k` / `M-x eve-stop-playback` | Stop and clean up |

Stopping playback restores all keybindings to their normal state.

## Video Layout

Set `eve-video-layout` to arrange the screen for editing: Emacs takes the
bottom portion of the display, and the mpv video window is positioned
directly above it — borderless, always-on-top, with automatic letterboxing.

```elisp
(setq eve-video-layout t)

;; Adjust the video/transcript split (default 0.3 = 30% video, 70% Emacs)
(setq eve-video-layout-ratio 0.4)
```

The layout activates on the first play command (`SPC`, `M-x eve-play-source`,
`M-x eve-play-rendered`) and the original frame geometry is restored when
playback stops. The layout is skipped when Emacs is in macOS native fullscreen.

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

The Emacs package locates the CLI automatically: it tries `executable-find`
on `eve-cli-program` first, then falls back to `scripts/run-cli.sh` relative
to the package source directory.  On a fresh clone `scripts/run-cli.sh`
bootstraps a venv from the pre-built wheel in `cli/dist/` using plain
`python3` — no `uv` required at runtime.

## Verification

From the repository root:

```bash
emacs --batch -Q -L . -l eve.el --eval '(princ "ok")'
emacs --batch -Q -L . -l ert -l test/eve-test.el -f ert-run-tests-batch-and-exit
PYTHONPATH=cli/src python3 -m eve_cli doctor --json
devenv shell -- ci
```
