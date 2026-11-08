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

## Agent workspaces

Agent edits use the `agent-session` skill and managed worktrees. The devenv
module is pinned in `devenv.yaml` and `devenv.lock`; it supplies the launcher,
shell integration and completions. Pi itself and its skills remain native
client resources, not generated project configuration.

After integrating this setup, start from a clean primary checkout on `main`:

```bash
devenv shell
agent-session audio-render create --json
agent-session audio-render run test-all
```

`create` prepares a workspace without launching another agent. The default
launcher for human `start`/`launch` commands is `pi`. Use the unambiguous
`agent-session` executable in scripts: without shell integration, `as` may
resolve to the system assembler. For Bash/Zsh, load
`$AGENT_SESSION_SHELL_ROOT/init.sh` when needed; Fish uses `init.fish`.

Existing plain Git worktrees are not automatically managed or migrated. Do not
copy credentials or runtime state into sessions. Setup does not authorize any
worktree retirement; review and explicit approval are required separately.

## Optional context-panel integration tests

Context panels remain optional at runtime. For full integration coverage, use
an explicit checkout rather than a machine-specific load path. CI checks out
`behaghel/context-panels` at `a507d85e5add9ef72340a8087683fed437a922e2`.
Point the test runner at a checkout of that revision:

```bash
EVE_CONTEXT_PANELS_DIR=/path/to/context-panels \
EVE_REQUIRE_CONTEXT_PANELS=1 devenv -q shell -- test-elisp
```

The same variables apply to `devenv -q shell -- ci`. Required mode fails if
the dependency cannot load or any ERT test skips, including an incompatible
integration API. With no variables set, standalone developer runs may skip
optional integration tests. The runner never downloads dependencies or loads
your personal Emacs configuration.

## Recommended Flow

1. Make the smallest change that solves the problem.
2. Run the narrowest relevant command first: `test-elisp`, `test-py`, `lint`, or `docs`.
3. Run `devenv shell -- test-all` before sending a cross-stack change.
4. Run `devenv shell -- ci` before proposing a larger change.

## CI Reference

GitHub Actions currently runs the following checks from `.github/workflows/test.yml`.

### Emacs Lisp job

The job checks out the pinned context-panels test dependency under
`.test-deps/context-panels` and sets `EVE_CONTEXT_PANELS_DIR` to its absolute
path plus `EVE_REQUIRE_CONTEXT_PANELS=1` before running:

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
nix run --accept-flake-config github:cachix/devenv/v2.4.0 -- -q shell -- ./scripts/check.sh
```

If you want local parity with the repo's full checks, `devenv shell -- ci` is the closest single entry point.

## Documentation Changes

- User manual source: `docs/eve.org`
- Format specification: `docs/tjm-spec.md`
- Generate Info output with `devenv shell -- docs`
- Check symbol coverage with `devenv shell -- lint` or `devenv shell -- ci`

Keep the README high-level and value-first. Put contributor workflow details here instead of duplicating them in the manual.
