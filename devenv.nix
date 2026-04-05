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

    dev-help-all.exec = ''
      if [ -t 1 ]; then
        B="\033[1m"; R="\033[0m"
      else
        B=""; R=""
      fi
      printf "\n''${B}eve.el — full command reference''${R}\n\n"

      printf "📝 Emacs\n"
      printf "  format-elisp           Format .el files\n"
      printf "  parse                  Check Emacs Lisp syntax\n"
      printf "  checkdoc               Lint docstrings\n"
      printf "  load-check             Verify the package loads\n"
      printf "  compile                Byte-compile\n"
      printf "  test-elisp             Run ERT tests\n\n"

      printf "🐍 Python CLI\n"
      printf "  sync-py                Install Python deps\n"
      printf "  format-py              Format Python code\n"
      printf "  lint-py                Run ruff + mypy\n"
      printf "  test-py                Run pytest\n"
      printf "  build-py               Build the wheel\n"
      printf "  run-cli -- --help      Run the eve CLI\n\n"

      printf "📖 Documentation\n"
      printf "  docs                   Generate eve.texi and eve.info\n"
      printf "  check-doc-drift        Verify all symbols are documented\n\n"

      printf "🚀 Project\n"
      printf "  format                 Format all\n"
      printf "  lint                   Lint all\n"
      printf "  test                   Test all\n"
      printf "  ci                     Full CI pipeline\n\n"

      printf "🔧 Tooling\n"
      printf "  emacs %s\n" "$(emacs --version 2>/dev/null | head -1 | sed 's/GNU Emacs //')"
      printf "  python %s\n" "$(python3 --version 2>/dev/null | sed 's/Python //')"
      printf "  uv %s\n" "$(uv --version 2>/dev/null | sed 's/uv //')"
      printf "  makeinfo %s\n" "$(makeinfo --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9.]+')"
    '';
  };

  enterShell = ''
    state_dir="''${XDG_STATE_HOME:-$HOME/.local/state}/eve-el"
    stamp="$state_dir/last-greeting"
    mkdir -p "$state_dir"

    show=false
    if [ ! -f "$stamp" ]; then
      show=true
    else
      last=$(cat "$stamp" 2>/dev/null || echo 0)
      now=$(date +%s)
      if [ $((now - last)) -ge 86400 ]; then
        show=true
      fi
    fi

    if [ "$show" = true ] && [ -t 1 ]; then
      B="\033[1m"; R="\033[0m"
      printf "\n''${B}🎬 eve.el''${R} — text-driven video editing in Emacs\n\n"
      printf "📝  ''${B}format''${R}    Format all          🔍  ''${B}lint''${R}   Lint all\n"
      printf "🧪  ''${B}test''${R}      Test all            🚀  ''${B}ci''${R}     Full CI pipeline\n"
      printf "📖  ''${B}docs''${R}      Generate manual     ▶️   ''${B}run-cli''${R} Run eve CLI\n"
      printf "\n💡 Run ''${B}dev-help-all''${R} for the full command reference.\n\n"
      date +%s > "$stamp"
    fi

    hook="$DEVENV_ROOT/.git/hooks/pre-push"
    if [ ! -f "$hook" ] || ! grep -q "eve-ci-pre-push" "$hook" 2>/dev/null; then
      mkdir -p "$(dirname "$hook")"
      cat > "$hook" << 'HOOK'
#!/usr/bin/env bash
# eve-ci-pre-push — installed by devenv enterShell
echo "[pre-push] Running CI checks..."
cd "$(git rev-parse --show-toplevel)"
devenv shell -- ci </dev/null
exit_code=$?
if [ "$exit_code" -ne 0 ]; then
  echo "[pre-push] CI failed (exit $exit_code)" >&2
fi
exit "$exit_code"
HOOK
      chmod +x "$hook"
    fi
  '';

  enterTest = ''
    ci
  '';
}
