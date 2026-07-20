#!/usr/bin/env sh
set -eu

FIX=false

usage() {
  cat <<'EOF'
Usage:
  ./verify.sh [options]

Options:
  --fix       Run auto-fixable checks where supported.
              Currently applies to:
                - ruff format
                - ruff check --fix

  -h, --help  Show this help message.

Examples:
  ./verify.sh
  ./verify.sh --fix
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --fix)
      FIX=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

run_step() {
  name=$1
  shift

  printf '\n'
  printf '============================================================\n'
  printf '==> %s\n' "$name"
  printf '============================================================\n'
  printf '+'
  printf ' %s' "$@"
  printf '\n\n'

  "$@"
}

printf '\nethos verification\n'

if [ "$FIX" = true ]; then
  printf 'mode: fix\n'
else
  printf 'mode: check\n'
fi

if [ "$FIX" = true ]; then
  run_step "Format with Ruff" uv run ruff format
  run_step "Lint with Ruff and apply fixes" uv run ruff check --fix
else
  run_step "Check formatting with Ruff" uv run ruff format --check
  run_step "Lint with Ruff" uv run ruff check
fi

run_step "Type check with mypy" uv run mypy
run_step "Type check with pyright" uv run pyright
run_step "Run tests" uv run pytest -v

printf '\n'
printf 'All verification steps passed.\n'