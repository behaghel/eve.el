{ pkgs, config, ... }:
{
  packages = [
    pkgs.git
    pkgs.python312
    pkgs.uv
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

Project
  devenv shell -- format
  devenv shell -- lint
  devenv shell -- test
  devenv shell -- ci
'';
  };

  enterShell = ''
    printf '%s\n' "$FLOW"
  '';

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

    format.exec = ''
      format-elisp
      format-py
    '';

    lint.exec = ''
      parse
      checkdoc
      load-check
      compile
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

  enterTest = ''
    ci
  '';
}
