# Contributing to eve.el

This repository has two main parts:

- `eve.el`: the Emacs major mode and editing UI
- `cli/`: the Python CLI for transcription, render planning, and media work

For user-facing behavior and commands, start with `docs/eve.org`.

## Prerequisites

- Emacs 29.1+
- Python 3.12+
- `devenv`
- `texinfo` for manual generation

The repo's local workflow is defined in `devenv.nix`.

## Local Commands

Run these from the repository root:

```bash
devenv shell -- format-elisp
devenv shell -- parse
devenv shell -- checkdoc
devenv shell -- load-check
devenv shell -- compile
devenv shell -- test-elisp

devenv shell -- sync-py
devenv shell -- format-py
devenv shell -- lint-py
devenv shell -- test-py
devenv shell -- build-py
devenv shell -- run-cli -- --help

devenv shell -- format
devenv shell -- lint
devenv shell -- test-all
devenv shell -- ci
devenv shell -- docs
```

Use `devenv shell -- test-all` for the combined test suite. The old `devenv shell -- test` command is stale and should not be used.

## Recommended Flow

1. Make the smallest change that solves the problem.
2. Run the narrowest relevant command first: `test-elisp`, `test-py`, `lint`, or `docs`.
3. Run `devenv shell -- test-all` before sending a cross-stack change.
4. Run `devenv shell -- ci` before proposing a larger change.

## CI Reference

GitHub Actions currently runs the following checks from `.github/workflows/test.yml`.

### Emacs Lisp job

```bash
chmod +x scripts/install-elpa-deps
./scripts/install-elpa-deps

export HOME=$GITHUB_WORKSPACE
chmod +x scripts/parse scripts/checkdoc scripts/load-check scripts/byte-compile
./scripts/parse eve.el test/eve-test.el
./scripts/checkdoc eve.el test/eve-test.el
./scripts/load-check
./scripts/byte-compile

export HOME=$GITHUB_WORKSPACE
chmod +x scripts/ert
./scripts/ert
```

### Documentation job

```bash
chmod +x scripts/install-elpa-deps
./scripts/install-elpa-deps

export HOME=$GITHUB_WORKSPACE
chmod +x scripts/gen-docs scripts/check-doc-drift
./scripts/gen-docs
./scripts/check-doc-drift
```

### Python job

```bash
chmod +x scripts/check.sh scripts/startup-health-check.sh scripts/sync-python.sh scripts/lint.sh scripts/build.sh scripts/run-cli.sh
./scripts/check.sh
```

If you want local parity with the repo's full checks, `devenv shell -- ci` is the closest single entry point.

## Documentation Changes

- User manual source: `docs/eve.org`
- Format specification: `docs/tjm-spec.md`
- Generate Info output with `devenv shell -- docs`
- Check symbol coverage with `devenv shell -- lint` or `devenv shell -- ci`

Keep the README high-level and value-first. Put contributor workflow details here instead of duplicating them in the manual.
