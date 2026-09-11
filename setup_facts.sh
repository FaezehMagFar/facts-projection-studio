#!/usr/bin/env bash
# Reproducible FACTS setup for Windows/WSL2 and native Linux.
# This script intentionally does not support macOS.

set -euo pipefail

WITH_DATA=false
REBUILD=false
for arg in "$@"; do
  case "$arg" in
    --with-data) WITH_DATA=true ;;
    --rebuild) REBUILD=true ;;
    *) printf 'ERROR: unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but was not found."; }

case "$(uname -s)" in
  Darwin*) die "This automated setup supports Windows/WSL2 and Linux, not macOS." ;;
  Linux*) ;;
  *) die "Unsupported operating system: $(uname -s)" ;;
esac

need git
need docker
docker info >/dev/null 2>&1 || die "Docker is installed but its engine is not running."

INSTALL_ROOT="${FACTS_INSTALL_ROOT:-$HOME/facts_ssisls}"
FACTS_REPO="${FACTS_REPO:-$INSTALL_ROOT/facts}"
DASHBOARD_REPO="${DASHBOARD_REPO:-$INSTALL_ROOT/facts.plotting.dashboard}"
UI_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$INSTALL_ROOT"

say "FACTS source"
if [[ ! -d "$FACTS_REPO/.git" ]]; then
  git clone --branch demo/ssisls --single-branch \
    https://github.com/pkjr002/facts.git "$FACTS_REPO"
else
  printf 'Using existing repository: %s\n' "$FACTS_REPO"
  git -C "$FACTS_REPO" remote get-url origin
  git -C "$FACTS_REPO" branch --show-current
fi

[[ -d "$FACTS_REPO/modules" && -f "$FACTS_REPO/docker/Dockerfile" ]] \
  || die "$FACTS_REPO is not a valid FACTS checkout."

if $WITH_DATA; then
  say "Full module data (resumable; this is the large download)"
  DATA_DIR="$FACTS_REPO/modules-data"
  URL_LIST="$DATA_DIR/modules-data.urls.txt"
  [[ -f "$URL_LIST" ]] || die "Missing official URL list: $URL_LIST"
  cd "$DATA_DIR"
  if command -v wget >/dev/null 2>&1; then
    wget --continue --retry-connrefused --waitretry=5 --timeout=60 \
      --input-file="$URL_LIST"
  elif command -v curl >/dev/null 2>&1; then
    while IFS= read -r url; do
      [[ -n "$url" && "$url" != \#* ]] || continue
      curl --fail --location --retry 5 --continue-at - \
        --output "${url##*/}" "$url"
    done < "$URL_LIST"
  else
    die "Install wget or curl to download FACTS module data."
  fi

  missing=0
  while IFS= read -r url; do
    [[ -n "$url" && "$url" != \#* ]] || continue
    file="${url##*/}"
    if [[ ! -s "$DATA_DIR/$file" ]]; then
      printf 'MISSING: %s\n' "$file" >&2
      missing=$((missing + 1))
    fi
  done < "$URL_LIST"
  (( missing == 0 )) || die "$missing module-data archive(s) are incomplete."
else
  say "Module-data download skipped"
  printf 'Enable “Full local module data” in the Setup tab when local runs are needed.\n'
fi

say "FACTS Docker image"
if $REBUILD || ! docker image inspect ssisls >/dev/null 2>&1; then
  docker build --target facts-core --build-arg MODULES_DATA=none \
    --tag ssisls --file "$FACTS_REPO/docker/Dockerfile" "$FACTS_REPO"
else
  printf 'Image ssisls already exists; leaving it unchanged.\n'
fi

say "Interactive results-dashboard source"
if [[ ! -d "$DASHBOARD_REPO/.git" ]]; then
  git clone https://github.com/Ttheegela/facts.plotting.dashboard.git \
    "$DASHBOARD_REPO"
else
  printf 'Using existing repository: %s\n' "$DASHBOARD_REPO"
fi

if $REBUILD || ! docker image inspect facts-viz >/dev/null 2>&1; then
  (cd "$DASHBOARD_REPO" && bash docker/build.sh)
else
  printf 'Image facts-viz already exists; leaving it unchanged.\n'
fi

chmod +x "$UI_DIR/run_facts_slr.sh" "$UI_DIR/facts-dashboard" 2>/dev/null || true

say "Setup complete"
printf 'FACTS repository:     %s\n' "$FACTS_REPO"
printf 'FACTS image:          ssisls\n'
printf 'Dashboard repository: %s\n' "$DASHBOARD_REPO"
printf 'Dashboard image:      facts-viz\n'
