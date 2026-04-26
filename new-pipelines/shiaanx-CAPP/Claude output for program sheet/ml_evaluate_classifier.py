"""
evaluate_classifier.py — Evaluate classify_features.py against MFCAD++ ground truth.

Usage:
    python "evaluate_classifier.py" <classified_json> [--step <step_file>]

If --step is omitted, the script looks for a .step file in the same directory as the
classified JSON (same stem, e.g. 25.step alongside 25_features_clustered_classified.json).

Output:
    - Per-cluster predictions vs ground truth (printed)
    - Per-class precision / recall / F1
    - Confusion matrix
    - Overall accuracy

Ground truth extraction:
    MFCAD++ embeds the label ID (0–24) as the first argument of each ADVANCED_FACE
    entity in the STEP file, e.g.  #17 = ADVANCED_FACE('24', ...) → label 24 (Stock).
    OCC's TopExp_Explorer iterates faces in the same order as ADVANCED_FACE entities
    appear in the file, so face_index i in the pipeline JSON corresponds to the i-th
    ADVANCED_FACE (0-indexed) in the STEP file.

Label taxonomy:
    Uses rule_sheets/07_label_taxonomy.json to bridge MFCAD++ IDs → internal
    feature_type names used by the ShiaanX pipeline.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. Load label taxonomy (07_label_taxonomy.json)
# ---------------------------------------------------------------------------

def load_taxonomy(rule_sheets_dir: Path) -> dict:
    """Return {mfcad_id: {internal_feature_type, mfcad_name, ...}} mapping."""
    path = rule_sheets_dir / "07_label_taxonomy.json"
    if not path.exists():
        raise FileNotFoundError(f"Taxonomy not found: {path}")
    with open(path) as f:
        data = json.load(f)
    return {m["mfcad_id"]: m for m in data["mappings"]}


# ---------------------------------------------------------------------------
# 2. Extract GT labels from STEP file
# ---------------------------------------------------------------------------

ADVANCED_FACE_RE = re.compile(r"ADVANCED_FACE\('(\d+)'", re.IGNORECASE)


def extract_step_labels(step_path: Path) -> list[int]:
    """Return list of int label IDs, one per ADVANCED_FACE, in file order."""
    labels = []
    with open(step_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = ADVANCED_FACE_RE.search(line)
            if m:
                labels.append(int(m.group(1)))
    return labels


# ---------------------------------------------------------------------------
# 3. Evaluate
# ---------------------------------------------------------------------------

def majority_vote(indices: list[int], face_labels: list[int]) -> int:
    """Return the most common label among the given face indices."""
    counts = Counter(face_labels[i] for i in indices if i < len(face_labels))
    if not counts:
        return -1
    return counts.most_common(1)[0][0]


def evaluate(classified_json: Path, step_path: Path, taxonomy: dict) -> dict:
    """
    Core evaluation.  Returns a results dict with:
        clusters       — per-cluster detail
        by_class       — precision / recall / F1 per GT internal_feature_type
        overall_acc    — float
        confusion      — dict {gt_type: {pred_type: count}}
    """
    with open(classified_json) as f:
        data = json.load(f)

    face_labels = extract_step_labels(step_path)
    clusters = data["clusters"]

    results = []
    for cluster in clusters:
        face_indices = cluster["face_indices"]
        predicted_type = cluster["feature_type"]
        gt_mfcad_id = majority_vote(face_indices, face_labels)

        tax = taxonomy.get(gt_mfcad_id)
        gt_internal = tax["internal_feature_type"] if tax else f"unknown_{gt_mfcad_id}"
        gt_mfcad_name = tax["mfcad_name"] if tax else f"id_{gt_mfcad_id}"

        # Strip _angled suffix for comparison (setup planning adds it, GT doesn't)
        pred_bare = predicted_type.replace("_angled", "") if predicted_type else predicted_type

        correct = pred_bare == gt_internal

        results.append({
            "cluster_id": cluster["cluster_id"],
            "face_indices": face_indices,
            "gt_mfcad_id": gt_mfcad_id,
            "gt_mfcad_name": gt_mfcad_name,
            "gt_internal": gt_internal,
            "predicted": predicted_type,
            "predicted_bare": pred_bare,
            "correct": correct,
        })

    # Per-class stats
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for r in results:
        gt = r["gt_internal"]
        pred = r["predicted_bare"]
        confusion[gt][pred] += 1
        if pred == gt:
            tp[gt] += 1
        else:
            fp[pred] += 1
            fn[gt] += 1

    all_classes = sorted(set(list(tp) + list(fp) + list(fn)))
    by_class = {}
    for cls in all_classes:
        p_denom = tp[cls] + fp[cls]
        r_denom = tp[cls] + fn[cls]
        precision = tp[cls] / p_denom if p_denom else 0.0
        recall = tp[cls] / r_denom if r_denom else 0.0
        f1_denom = precision + recall
        f1 = 2 * precision * recall / f1_denom if f1_denom else 0.0
        by_class[cls] = {
            "tp": tp[cls], "fp": fp[cls], "fn": fn[cls],
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }

    correct_total = sum(1 for r in results if r["correct"])
    overall_acc = correct_total / len(results) if results else 0.0

    return {
        "clusters": results,
        "by_class": by_class,
        "overall_acc": round(overall_acc, 3),
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "n_clusters": len(results),
        "n_correct": correct_total,
    }


# ---------------------------------------------------------------------------
# 4. Pretty print
# ---------------------------------------------------------------------------

def print_results(ev: dict, part_name: str = "") -> None:
    print(f"\n{'='*60}")
    print(f"  Classifier Evaluation  {f'— {part_name}' if part_name else ''}")
    print(f"{'='*60}")

    print(f"\nClusters ({ev['n_clusters']} total):")
    print(f"  {'ID':>3}  {'GT (internal)':25}  {'MFCAD name':28}  {'Predicted':25}  OK")
    print(f"  {'-'*3}  {'-'*25}  {'-'*28}  {'-'*25}  --")
    for r in ev["clusters"]:
        tick = "Y" if r["correct"] else "X"
        print(f"  {r['cluster_id']:>3}  {r['gt_internal']:25}  {r['gt_mfcad_name']:28}  {r['predicted']:25}  {tick}")

    print(f"\nPer-class metrics:")
    print(f"  {'Class':30}  {'P':>6}  {'R':>6}  {'F1':>6}  TP  FP  FN")
    print(f"  {'-'*30}  {'---':>6}  {'---':>6}  {'---':>6}  --  --  --")
    for cls, m in sorted(ev["by_class"].items()):
        print(f"  {cls:30}  {m['precision']:>6.3f}  {m['recall']:>6.3f}  {m['f1']:>6.3f}"
              f"  {m['tp']:>2}  {m['fp']:>2}  {m['fn']:>2}")

    print(f"\nOverall accuracy: {ev['overall_acc']:.1%}  ({ev['n_correct']}/{ev['n_clusters']})")

    print(f"\nConfusion matrix (rows=GT, cols=predicted):")
    all_types = sorted(set(
        list(ev["confusion"].keys()) +
        [p for row in ev["confusion"].values() for p in row]
    ))
    col_w = max(len(t) for t in all_types) if all_types else 10
    row_w = col_w
    gt_pred_label = "GT \\ Pred"
    header = f"  {gt_pred_label:>{row_w}}  " + "  ".join(f"{t:>{col_w}}" for t in all_types)
    print(header)
    for gt in all_types:
        row_data = ev["confusion"].get(gt, {})
        row = f"  {gt:>{row_w}}  " + "  ".join(
            f"{row_data.get(pred, 0):>{col_w}}" for pred in all_types
        )
        print(row)
    print()


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

def find_step_for_classified(classified_json: Path) -> Path | None:
    """
    Guess the STEP file path from the classified JSON path.
    e.g. .../25/25_features_clustered_classified.json → .../25/25.step
    """
    stem = classified_json.stem  # e.g. "25_features_clustered_classified"
    part_id = stem.split("_")[0]  # e.g. "25"
    candidate = classified_json.parent / f"{part_id}.step"
    return candidate if candidate.exists() else None


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate classify_features.py against MFCAD++ ground truth"
    )
    parser.add_argument("classified_json", help="Path to *_classified.json")
    parser.add_argument("--step", help="Path to .step file (auto-detected if omitted)")
    parser.add_argument("--json-out", help="Write results as JSON to this path")
    args = parser.parse_args()

    classified_path = Path(args.classified_json)
    if not classified_path.exists():
        print(f"ERROR: classified JSON not found: {classified_path}", file=sys.stderr)
        sys.exit(1)

    if args.step:
        step_path = Path(args.step)
    else:
        step_path = find_step_for_classified(classified_path)
        if step_path is None:
            print("ERROR: could not auto-detect STEP file. Use --step.", file=sys.stderr)
            sys.exit(1)

    if not step_path.exists():
        print(f"ERROR: STEP file not found: {step_path}", file=sys.stderr)
        sys.exit(1)

    # Locate rule_sheets relative to this script
    script_dir = Path(__file__).parent
    taxonomy = load_taxonomy(script_dir / "rule_sheets")

    ev = evaluate(classified_path, step_path, taxonomy)
    print_results(ev, part_name=step_path.stem)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(ev, f, indent=2)
        print(f"Results written to {args.json_out}")


if __name__ == "__main__":
    main()
