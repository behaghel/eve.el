# eve.el

Eve is an Emacs major mode for editing spoken-word videos by manipulating text
instead of cutting timelines.  A recording is transcribed into a Textual Join
Manifest (`.tjm.json`) — an ordered list of timed segments with per-word
timestamps.  You delete words, reorder paragraphs, attach b-roll, and mark
sections in Emacs; the `eve text-edit` CLI turns the edited manifest into an
`.mp4`.

The edit-render loop is fast by design: draft renders use parallel MJPEG
encoding and a content-addressable segment cache, so pressing `C-c C-c` after
a small edit re-encodes only the changed segments (often under two seconds).
Speculative pre-rendering warms the cache on every save.  `C-u C-c C-c`
exports at full H.264 quality for delivery.

**Full user manual**: `docs/eve.org` — or `C-h i m eve RET` in Emacs after
installation.  
**TJM v1.1 format specification**: `docs/tjm-spec.md`.

## Installation

Install the `eve` CLI (required for transcription and rendering):

```bash
pip install cli/dist/eve_cli-*.whl
eve doctor --json   # verify
```

Install the Emacs package with straight.el:

```elisp
(use-package eve
  :straight (:type git :host github :repo "behaghel/eve.el"))
```

Or manually: clone, add to `load-path`, `(require 'eve)`.

Opening any `*.tjm.json` file activates `eve-mode` automatically.

**Optional**: install `hydra` for the `?` keybinding cheatsheet popup.
## Development

The project uses [devenv](https://devenv.sh) at the repository root.

```bash
devenv shell -- format    # format all
devenv shell -- lint      # lint all (Emacs + Python)
devenv shell -- test      # ERT + pytest
devenv shell -- ci        # full pipeline
devenv shell -- docs      # generate eve.texi and eve.info
```

**Project layout**

```
eve.el          Main Emacs library
test/           ERT test suite
docs/           Documentation
  eve.org         User manual source (Org → Info)
  tjm-spec.md     TJM v1.1 format specification
  internal/       Working documents
cli/            Python package (transcription, rendering, CLI)
scripts/        Build helpers
```

**Dependencies**

- Emacs 29.1+, `mpv` (playback)
- Python 3.12+, `uv` (CLI development)
- `texinfo` (Info generation via `devenv shell -- docs`)
- `hydra` (optional — keybinding cheatsheet)

**Verification**

```bash
emacs --batch -Q -L . -l eve.el --eval '(princ "ok")'
devenv shell -- test-elisp
devenv shell -- test-py
PYTHONPATH=cli/src python3 -m eve_cli doctor --json
```
