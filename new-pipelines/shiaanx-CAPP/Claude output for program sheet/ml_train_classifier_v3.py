"""
ml_train_classifier_v3.py - RF classifier trained on our pipeline's own feature extraction.

Root cause of v1/v2 inference gap:
  MFCAD++ H5 files store ~25 faces/part (simplified B-Rep);
  our OCC pipeline extracts ~37 faces/part (full STEP ADVANCED_FACE sequence).
  Features are therefore not comparable → model trained on H5 cannot infer correctly.

Fix: use OUR extract_features.py output + GT labels from STEP ADVANCED_FACE names.
  STEP ADVANCED_FACE count == our OCC face count → perfect alignment.

What this script does:
  1. Scans MFCAD++ test STEP files for those with a *_features.json already generated.
  2. Parses GT face labels from each STEP file's ADVANCED_FACE name field.
  3. Extracts 15 features per face using the same _extract_ml_features() used at inference.
  4. Trains a Random Forest (200 trees) on 80% of collected parts.
  5. Evaluates on the remaining 20%, saves model + metrics.

To generate missing *_features.json files first, run:
    conda run -n occ python "Claude output for program sheet/ml_batch_extract.py"

Usage:
    conda run -n occ python "Claude output for program sheet/ml_train_classifier_v3.py"
"""

import importlib.util
import json
import re
import sys
import time
import warnings
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

BASE       = Path(__file__).parent
STEP_TEST  = BASE / "Dataset/MFCAD_dataset/MFCAD++_dataset/step/test"
MODELS_DIR = BASE / "models"
MODELS_DIR.mkdir(exist_ok=True)

MODEL_PATH    = MODELS_DIR / "rf_classifier_v3.pkl"
ENCODER_PATH  = MODELS_DIR / "rf_label_encoder_v3.json"
METRICS_PATH  = BASE / "metrics_log.csv"
TAXONOMY_PATH = BASE / "rule_sheets/07_label_taxonomy.json"

TRAIN_SPLIT = 0.8  # fraction of available parts used for training

FEAT_NAMES = [
    "area", "cx", "cy", "cz", "surf_type",
    "neigh_degree", "neigh_type_mean", "neigh_type_std", "neigh_area_mean", "neigh_area_std",
    "comp_size", "comp_type_diversity", "comp_area_ratio", "two_hop_degree", "comp_aspect_ratio",
]


# ---------------------------------------------------------------------------
# GT label extraction from STEP file
# ---------------------------------------------------------------------------

_ADVANCED_FACE_RE = re.compile(r"ADVANCED_FACE\s*\(\s*'(\d+)'", re.IGNORECASE)


def get_gt_labels(step_path: Path) -> list:
    """Return list of int GT class IDs (one per ADVANCED_FACE in STEP order)."""
    labels = []
    with open(step_path, "r", errors="replace") as f:
        for line in f:
            m = _ADVANCED_FACE_RE.search(line)
            if m:
                labels.append(int(m.group(1)))
    return labels


# ---------------------------------------------------------------------------
# Find STEP + features.json pairs
# ---------------------------------------------------------------------------

def find_pairs():
    """
    Return list of (step_path, features_path) for all test parts that have
    an existing *_features.json.  Skips parts where the JSON is missing
    (run ml_batch_extract.py to generate them first).
    """
    pairs = []
    for step_path in sorted(STEP_TEST.rglob("*.step")):
        stem = step_path.stem
        # Features JSON may be alongside .step or inside a stem/ subdirectory
        candidates = [
            step_path.parent / f"{stem}_features.json",
            step_path.parent / stem / f"{stem}_features.json",
        ]
        for fp in candidates:
            if fp.exists():
                pairs.append((step_path, fp))
                break
    return pairs


# ---------------------------------------------------------------------------
# Dataset loader
# ---------------------------------------------------------------------------

def load_dataset(pairs: list) -> tuple:
    """
    For each (step, features) pair:
      - Parse GT labels from STEP file
      - Load features JSON
      - Check face counts match
      - Extract 15-feature matrix via classify_features._extract_ml_features()
    Returns (X, y) numpy arrays.
    """
    # Import our feature extractor (lazy — avoids OCC at import time)
    spec = importlib.util.spec_from_file_location(
        "classify_features", str(BASE / "3. classify_features.py"))
    clf_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clf_mod)

    X_parts, y_parts = [], []
    skipped_mismatch = 0
    skipped_empty = 0
    t0 = time.time()

    for i, (step_path, feat_path) in enumerate(pairs):
        if i % 50 == 0:
            elapsed = time.time() - t0
            print(f"  {i}/{len(pairs)} ({elapsed:.0f}s)  parts loaded={len(X_parts)}  "
                  f"skipped={skipped_mismatch+skipped_empty}", end="\r")

        gt_labels = get_gt_labels(step_path)
        if not gt_labels:
            skipped_empty += 1
            continue

        with open(feat_path) as f:
            feat_data = json.load(f)

        n_pipeline = len(feat_data["faces"]["faces"])
        if n_pipeline != len(gt_labels):
            skipped_mismatch += 1
            continue

        try:
            X = clf_mod._extract_ml_features(feat_data)
        except Exception:
            skipped_mismatch += 1
            continue

        X_parts.append(X)
        y_parts.append(np.array(gt_labels, dtype=np.int32))

    elapsed = time.time() - t0
    n_loaded = len(X_parts)
    print(f"  {len(pairs)}/{len(pairs)} done in {elapsed:.1f}s  "
          f"loaded={n_loaded}  skipped_mismatch={skipped_mismatch}  "
          f"skipped_empty={skipped_empty}          ")

    if not X_parts:
        raise RuntimeError(
            "No usable parts found.  Run ml_batch_extract.py first to generate "
            "*_features.json files for the MFCAD++ test set.")

    X = np.concatenate(X_parts, axis=0)
    y = np.concatenate(y_parts, axis=0)
    return X, y, n_loaded


# ---------------------------------------------------------------------------
# Train / evaluate
# ---------------------------------------------------------------------------

def train(X_train, y_train):
    from sklearn.ensemble import RandomForestClassifier
    print("\nTraining Random Forest v3...")
    t0 = time.time()
    clf = RandomForestClassifier(
        n_estimators=200, min_samples_leaf=2, n_jobs=-1, random_state=42)
    clf.fit(X_train, y_train)
    print(f"  Done in {time.time()-t0:.1f}s")
    return clf


def evaluate(clf, X_test, y_test, taxonomy: dict) -> dict:
    from sklearn.metrics import classification_report, accuracy_score
    print("\nEvaluating...")
    t0 = time.time()
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"  Done in {time.time()-t0:.1f}s")
    print(f"\n  Overall accuracy: {acc*100:.1f}%  ({int(acc*len(y_test)):,}/{len(y_test):,} faces)\n")

    labels_present = sorted(np.unique(np.concatenate([y_test, y_pred])))
    target_names   = [taxonomy.get(int(l), f"class_{l}") for l in labels_present]
    report = classification_report(
        y_test, y_pred, labels=labels_present,
        target_names=target_names, digits=3, zero_division=0)
    print(report)

    print("  Feature importances:")
    for name, imp in sorted(zip(FEAT_NAMES, clf.feature_importances_), key=lambda x: -x[1]):
        bar = "|" * int(imp * 50)
        print(f"    {name:26s} {imp:.4f}  {bar}")

    return {"accuracy": acc, "report": report}


def save_model(clf, taxonomy: dict):
    import joblib
    joblib.dump(clf, MODEL_PATH)
    with open(ENCODER_PATH, "w") as f:
        json.dump({str(k): v for k, v in taxonomy.items()}, f, indent=2)
    print(f"\n  Model saved:   {MODEL_PATH}")
    print(f"  Encoder saved: {ENCODER_PATH}")


def log_metrics(accuracy: float, n_train: int, n_test: int, n_parts: int):
    today = date.today().isoformat()
    header = not METRICS_PATH.exists()
    with open(METRICS_PATH, "a") as f:
        if header:
            f.write("date,model,n_train_faces,n_test_faces,overall_accuracy,notes\n")
        f.write(f"{today},rf_mfcad_v3,{n_train},{n_test},{accuracy:.4f},"
                f"RandomForest 15-feat pipeline-features {n_parts}-parts\n")
    print(f"  Metrics logged: {METRICS_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("ShiaanX — MFCAD++ Random Forest v3 (pipeline features)")
    print("=" * 60)

    with open(TAXONOMY_PATH) as f:
        taxonomy_data = json.load(f)
    taxonomy = {m["mfcad_id"]: m["mfcad_name"] for m in taxonomy_data["mappings"]}

    print("\nScanning for STEP + features.json pairs...")
    pairs = find_pairs()
    print(f"  Found {len(pairs)} parts with features.json")

    if len(pairs) < 10:
        print("\nToo few parts to train.  Run ml_batch_extract.py first:")
        print("  conda run -n occ python \"Claude output for program sheet/ml_batch_extract.py\"")
        sys.exit(1)

    # Reproducible train/test split at part level
    rng = np.random.default_rng(42)
    idx = rng.permutation(len(pairs))
    n_train_parts = int(len(pairs) * TRAIN_SPLIT)
    train_pairs = [pairs[i] for i in idx[:n_train_parts]]
    test_pairs  = [pairs[i] for i in idx[n_train_parts:]]

    print(f"\nLoading training data ({len(train_pairs)} parts)...")
    X_train, y_train, n_tr = load_dataset(train_pairs)
    print(f"  {X_train.shape[0]:,} faces, {X_train.shape[1]} features, "
          f"{len(np.unique(y_train))} classes")
    print(f"  Label distribution (top 5): "
          f"{Counter(y_train.tolist()).most_common(5)}")

    print(f"\nLoading test data ({len(test_pairs)} parts)...")
    X_test, y_test, n_te = load_dataset(test_pairs)
    print(f"  {X_test.shape[0]:,} faces")

    clf = train(X_train, y_train)
    results = evaluate(clf, X_test, y_test, taxonomy)
    save_model(clf, taxonomy)
    log_metrics(results["accuracy"], len(X_train), len(X_test), n_tr + n_te)

    print("\nDone.")


if __name__ == "__main__":
    main()
