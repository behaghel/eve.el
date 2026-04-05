{ pkgs, config, ... }:
{
  packages = [
    pkgs.git
    pkgs.python312
    pkgs.uv
    pkgs.texinfo
  ];

  env = {
    EDITOR = "emacs";
    PYTHONUTF8 = "1";
    FLOW = ''
eve.el development shell

Emacs
  devenv shell -- format-elisp
  devenv shell -- parse
  devenv shell -- checkdoc
  devenv shell -- load-check
  devenv shell -- compile
  devenv shell -- test-elisp

Python
  devenv shell -- sync-py
  devenv shell -- format-py
  devenv shell -- lint-py
  devenv shell -- test-py
  devenv shell -- build-py
  devenv shell -- run-cli -- --help

Docs
  devenv shell -- docs

Project
  devenv shell -- format
  devenv shell -- lint
  devenv shell -- test
  devenv shell -- ci
'';
  };

  scripts = {
    format-elisp.exec = ''
      cd "$DEVENV_ROOT"
      mapfile -t el_files < <(git ls-files "*.el")
      if [ ''${#el_files[@]} -gt 0 ]; then
        ./scripts/format "''${el_files[@]}"
      fi
    '';

    parse.exec = ''
      cd "$DEVENV_ROOT"
      mapfile -t el_files < <(git ls-files "*.el")
      if [ ''${#el_files[@]} -gt 0 ]; then
        ./scripts/parse "''${el_files[@]}"
      fi
    '';

    checkdoc.exec = ''
      cd "$DEVENV_ROOT"
      mapfile -t el_files < <(git ls-files "*.el")
      if [ ''${#el_files[@]} -gt 0 ]; then
        ./scripts/checkdoc "''${el_files[@]}"
      fi
    '';

    load-check.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/load-check
    '';

    compile.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/byte-compile
    '';

    test-elisp.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/ert
    '';

    sync-py.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/sync-python.sh
    '';

    format-py.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/format.sh
    '';

    lint-py.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/lint.sh
    '';

    test-py.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/check.sh
    '';

    build-py.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/build.sh
    '';

    run-cli.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/run-cli.sh "$@"
    '';

    docs.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/gen-docs
      emacs --batch -l ox-texinfo --visit docs/eve.org \
        --funcall org-texinfo-export-to-texinfo
      makeinfo --no-split docs/eve.texi -o docs/eve.info
      install-info --dir=docs/dir docs/eve.info
    '';

    format.exec = ''
      format-elisp
      format-py
    '';

    check-doc-drift.exec = ''
      cd "$DEVENV_ROOT"
      ./scripts/gen-docs
      ./scripts/check-doc-drift
    '';

    lint.exec = ''
      parse
      checkdoc
      load-check
      compile
      check-doc-drift
      lint-py
    '';

    test.exec = ''
      test-elisp
      test-py
    '';

    ci.exec = ''
      lint
      test
    '';
  };

  enterShell = ''
    printf '%s\n' "$FLOW"

    # Install a pre-push hook that runs the CI pipeline locally.
    hook="$DEVENV_ROOT/.git/hooks/pre-push"
    if [ ! -f "$hook" ] || ! grep -q "eve-ci-pre-push" "$hook" 2>/dev/null; then
      mkdir -p "$(dirname "$hook")"
      cat > "$hook" << 'HOOK'
#!/usr/bin/env bash
# eve-ci-pre-push — installed by devenv enterShell
set -euo pipefail
echo "[pre-push] Running CI checks..."
cd "$(git rev-parse --show-toplevel)"
devenv shell -- ci
HOOK
      chmod +x "$hook"
      echo "Installed pre-push hook at $hook"
    fi
  '';

  enterTest = ''
    ci
  '';
}
