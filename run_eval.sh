#!/usr/bin/env bash
# Promo Finder: full evaluation (macOS / Linux).  ./run_eval.sh [test]
set -euo pipefail
cd "$(dirname "$0")"
TITLE="I Married a Broken Soldier. He was a Duke"
python3 -m pip install -q -r requirements.txt
python3 -m promo_finder ping
python3 -m promo_finder run --input data/raw --out runs/baseline --mode baseline --title "$TITLE" --reference eval/reference.json
if [ "${1:-}" = "test" ]; then
  python3 -m promo_finder run --input data/raw --out runs/ai_test --episodes 1-3 --title "$TITLE" --reference eval/reference.json
else
  python3 -m promo_finder run --input data/raw --out runs/ai --title "$TITLE" --baseline runs/baseline --reference eval/reference.json
fi
