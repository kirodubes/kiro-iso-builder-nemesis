#!/bin/bash
set -euo pipefail
# Run every KIB test: unit tests, the build-the-iso.sh contract test, ruff, and the GUI
# smoke test when a desktop session is available. Pass a folder to keep GUI screenshots.
#   bash tests/run-tests.sh [screenshot-dir]

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "${TESTS_DIR}")"
FAILED=()

run() {
    local name="$1"
    shift
    echo
    echo "######## ${name}"
    if "$@"; then
        echo "######## ${name}: passed"
    else
        echo "######## ${name}: FAILED"
        FAILED+=("${name}")
    fi
}

cd "${APP_DIR}"
run "unit tests" python3 -B -m unittest discover -s tests -v
run "build script contract" bash tests/test_build_script.sh
if command -v ruff >/dev/null; then
    run "ruff" ruff check .
fi
if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    run "GUI smoke test" python3 -B tests/gui_smoke.py "$@"
else
    echo
    echo "######## GUI smoke test: skipped (no desktop session)"
fi

echo
if (( ${#FAILED[@]} )); then
    echo "FAILED: ${FAILED[*]}"
    exit 1
fi
echo "All KIB tests passed"
