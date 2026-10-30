#!/usr/bin/env bash
# Train and evaluate the notes classifier (category + theme).
#
#   ./train.sh            full run: baseline + SetFit, category and theme
#   ./train.sh quick      baseline only (seconds), to check the data
#   ./train.sh category   only the category model
#   ./train.sh theme      only the theme model
#   ./train.sh full --iterations 40   extra args go to train.py
#
# Results: output/run_<date>_<target>/report.md
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-full}"
[[ $# -gt 0 ]] && shift

# Good defaults for ~500-2000 labels on CPU.
# - mpnet-base: better than MiniLM for short multilingual text; ~3x slower
#   to train, still minutes on CPU. Use MiniLM for fast tests.
# - 20 pairs per example and 1 epoch: the usual SetFit setting; more
#   rarely helps and takes longer.
# - min 8 labels per class: fewer cannot be split into train and test.
# - 30 rule-labeled notes: the model sees template-only notes without
#   flooding the training set.
BASE_MODEL="${TRAIN_BASE_MODEL:-sentence-transformers/paraphrase-multilingual-mpnet-base-v2}"
COMMON=(
  --base-model "$BASE_MODEL"
  --train-langs "${TRAIN_LANGS:-es,en,und}"
  --test-size 0.2
  --min-per-class 8
  --iterations 20
  --epochs 1
  --batch-size 16
  --seed 42
)

run() {
  local target="$1"; shift
  echo "==> Training ${target} (${BASE_MODEL})"
  docker compose run --rm trainer --target "$target" "${COMMON[@]}" "$@"
}

case "$MODE" in
  full|quick|category|theme) ;;
  *) echo "Usage: $0 [full|quick|category|theme] [extra train.py args]" >&2; exit 1 ;;
esac

docker compose build trainer

case "$MODE" in
  quick)
    run category --skip-setfit "$@"
    run theme --skip-setfit --rule-samples 0 "$@"
    ;;
  category)
    run category "$@"
    ;;
  theme)
    run theme --rule-samples 0 "$@"
    ;;
  full)
    run category "$@"
    run theme --rule-samples 0 "$@"
    ;;
esac

echo
echo "Reports:"
ls -dt output/run_* 2>/dev/null | head -2 | sed 's|$|/report.md|'
