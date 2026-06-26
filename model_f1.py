"""F1 + confusion matrix for the strength model's 3-way pick (Home / Draw / Away).

Reuses `backtest.build_rows()` so the predictions are the SAME leak-free walk-forward
probabilities the production model uses (each game predicted from prior games only).
Pick = argmax of `pred_probs` (production model = strength shrunk to prior, shrink=0.25).

This is the *classifier* view of accuracy — a complement to the proper-scoring metrics in
`backtest.py`. Note the model never picks "Draw" as its single most-likely outcome (a
favourite always edges it), so the Draw class is structurally F1=0 under argmax; that is an
argmax artifact, not a ranking failure (see docs/MODEL_PERFORMANCE.md §3a / §6).

Usage:
    python model_f1.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import build_rows, pred_probs

SHRINK = 0.25                 # production value (tuned by walk-forward CV)
LABELS = ["Home", "Draw", "Away"]
WC_START = "2026-06-11"


def confusion_and_f1(rows, title):
    cm = [[0, 0, 0] for _ in range(3)]            # cm[actual][pred]
    for r in rows:
        p = pred_probs(r, SHRINK)
        pred = max(range(3), key=lambda i: p[i])
        cm[r["actual"]][pred] += 1

    n = sum(sum(row) for row in cm)
    correct = sum(cm[i][i] for i in range(3))
    acc = correct / n if n else 0.0

    print(f"\n=== {title}  (n={n}) ===")
    print("Confusion matrix  (rows = ACTUAL, cols = PREDICTED):")
    print(f"{'':>10}" + "".join(f"{l:>8}" for l in LABELS) + f"{'tot':>8}")
    for i in range(3):
        print(f"{LABELS[i]:>10}" + "".join(f"{cm[i][j]:>8}" for j in range(3)) + f"{sum(cm[i]):>8}")
    print(f"{'pred tot':>10}" + "".join(f"{sum(cm[a][j] for a in range(3)):>8}" for j in range(3)))

    print(f"\nAccuracy: {correct}/{n} = {100*acc:.1f}%")
    print(f"{'class':>8}{'prec':>8}{'recall':>8}{'F1':>8}{'support':>9}")
    f1s, supports = [], []
    for c in range(3):
        tp = cm[c][c]
        fp = sum(cm[a][c] for a in range(3)) - tp
        fn = sum(cm[c]) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        f1s.append(f1); supports.append(sum(cm[c]))
        print(f"{LABELS[c]:>8}{prec:>8.2f}{rec:>8.2f}{f1:>8.2f}{sum(cm[c]):>9}")

    macro = sum(f1s) / 3
    weighted = sum(f1 * s for f1, s in zip(f1s, supports)) / (sum(supports) or 1)
    print(f"\nMacro-F1   : {macro:.3f}")
    print(f"Weighted-F1: {weighted:.3f}")


def main():
    rows = build_rows()
    wc = [r for r in rows if r["date"] >= WC_START]
    confusion_and_f1(wc, "2026 World Cup (live test set)")
    confusion_and_f1(rows, "Full dataset (all logged fixtures)")


if __name__ == "__main__":
    main()
