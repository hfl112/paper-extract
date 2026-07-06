#!/usr/bin/env bash
# Run the full offline test suite (unit + smoke). Manual tests are in tests/README.md.
#
# Uses the Python environment where paper-extract is installed:
#   source .venv/bin/activate && bash tests/run_all.sh
# or point PYTHON at any interpreter explicitly:
#   PYTHON=.venv/bin/python bash tests/run_all.sh
set -e
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

echo "== unit tests =="
"$PYTHON" -m pytest tests/ -q

for s in smoke_step1 smoke_step2 smoke_step3 smoke_step4 smoke_step5; do
  echo "== $s =="
  "$PYTHON" "tests/$s.py"
done

echo
echo "All offline tests passed."
