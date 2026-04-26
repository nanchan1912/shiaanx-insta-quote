"""
train_classifier.py — Train a Random Forest face-level feature classifier
                       on the MFCAD++ dataset.

Usage:
    conda run -n occ python "train_classifier.py"

What it does:
    1. Reads all batches from training_MFCAD++.h5
       - V_1 features per face: [surface_area, cx, cy, cz, surface_type]
       - Adjacency matrix A_1 to compute neighbourhood features
    2. Adds neighbourhood features per face:
       - degree (number of adjacent faces)
       - mean/std of neighbour surface_types
       - mean/std of neighbour surface_areas
    3. Trains a Random Forest (scikit-learn) on all ~1M training faces
    4. Evaluates on test_MFCAD++.h5 → per-class F1, overall accuracy
    5. Saves model to models/rf_classifier.pkl
    6. Appends results to metrics_log.csv

Feature vector per face (10 dimensions):
    [surface_area, cx, cy, cz, surface_type,
     neighbour_degree, neigh_surface_type_mean, neigh_surface_type_std,
     neigh_area_mean, neigh_area_std]

Output files:
    models/rf_classifier.pkl     — trained model (joblib)
    models/rf_label_encoder.pkl  — label array (int → class name)
    metrics_log.csv              — accuracy log appended each run
"""

import json
import time
import warnings
from collections import defaultdict
from datetime import date
from pathlib import Path

import h5py
import numpy as np
from scipy.sparse import csr_matrix

warnings.filterwarnings("ignore")

# ── paths ─────────────────────────────────────────────────────────────────────

BASE      = Path(__file__).parent
H5_DIR    = BASE / "Dataset/MFCAD_dataset/MFCAD++_dataset/hierarchical_graphs"
MODELS_DIR = BASE / "models"
MODELS_DIR.mkdir(exist_ok=True)

MODEL_PATH   = MODELS_DIR / "rf_classifier.pkl"
ENCODER_PATH = MODELS_DIR / "rf_label_encoder.json"
METRICS_PATH = BASE / "metrics_log.csv"

TAXONOMY_PATH = BASE / "rule_sheets/07_label_taxonomy.json"

# ── taxonomy ──────────────────────────────────────────────────────────────────

def load_taxonomy() -> dict:
    with open(TAXONOMY_PATH) as f:
        data = json.load(f)
    return {m["mfcad_id"]: m["mfcad_name"] for m in data["mappings"]}

# ── feature extraction ────────────────────────────────────────────────────────

def extract_features_from_batch(grp) -> tuple[np.ndarray, np.ndarray]:
    """
    Extract face-level feature matrix X and label vector y from one H5 group.

    Returns
    -------
    X : np.ndarray, shape (n_faces, 10)
    y : np.ndarray, shape (n_faces,)  int labels 0-24
    """
    V1      = grp["V_1"][:]          # (n_faces, 5)  [area, cx, cy, cz, surf_type]
    labels  = grp["labels"][:]       # (n_faces,)
    n_faces = V1.shape[0]

    # Build sparse adjacency matrix from COO data
    adj_idx    = grp["A_1_idx"][:]   # (n_edges, 2)
    adj_shape  = tuple(grp["A_1_shape"][:])  # (n_faces, n_faces)
    adj_values = grp["A_1_values"][:]

    # Guard: some batches may have no edges
    if adj_idx.shape[0] == 0 or adj_shape[0] != n_faces:
        neigh_features = np.zeros((n_faces, 5), dtype=np.float32)
    else:
        A = csr_matrix(
            (adj_values, (adj_idx[:, 0], adj_idx[:, 1])),
            shape=adj_shape,
            dtype=np.float32,
        )

        # Neighbourhood features per face
        degree          = np.array(A.sum(axis=1)).flatten()          # (n,)
        neigh_area_sum  = A.dot(V1[:, 0])                            # sum of neighbour areas
        neigh_type_sum  = A.dot(V1[:, 4])                            # sum of neighbour surf types
        neigh_area_sq   = A.dot(V1[:, 0] ** 2)
        neigh_type_sq   = A.dot(V1[:, 4] ** 2)

        # Mean and std (safe: degree=0 stays 0)
        safe_deg = np.where(degree > 0, degree, 1.0)
        neigh_area_mean = neigh_area_sum / safe_deg
        neigh_type_mean = neigh_type_sum / safe_deg
        neigh_area_var  = np.maximum(neigh_area_sq / safe_deg - neigh_area_mean ** 2, 0)
        neigh_type_var  = np.maximum(neigh_type_sq / safe_deg - neigh_type_mean ** 2, 0)
        neigh_area_std  = np.sqrt(neigh_area_var)
        neigh_type_std  = np.sqrt(neigh_type_var)

        neigh_features = np.stack([
            degree, neigh_type_mean, neigh_type_std,
            neigh_area_mean, neigh_area_std,
        ], axis=1).astype(np.float32)

    X = np.concatenate([V1, neigh_features], axis=1)   # (n_faces, 10)
    y = labels.astype(np.int32)
    return X, y


def load_split(h5_path: Path, desc: str) -> tuple[np.ndarray, np.ndarray]:
    """Load all batches from an H5 file into (X, y) arrays."""
    X_parts, y_parts = [], []
    t0 = time.time()
    with h5py.File(h5_path, "r") as f:
        batch_keys = sorted(f.keys(), key=lambda k: int(k))
        n = len(batch_keys)
        for i, key in enumerate(batch_keys):
            if i % 200 == 0:
                elapsed = time.time() - t0
                print(f"  {desc}: batch {i}/{n}  ({elapsed:.0f}s)", end="\r")
            X_b, y_b = extract_features_from_batch(f[key])
            X_parts.append(X_b)
            y_parts.append(y_b)
    print(f"  {desc}: loaded {n} batches in {time.time()-t0:.1f}s          ")
    X = np.concatenate(X_parts, axis=0)
    y = np.concatenate(y_parts, axis=0)
    print(f"  {desc}: {X.shape[0]:,} faces, {X.shape[1]} features, "
          f"{len(np.unique(y))} classes")
    return X, y

# ── training ──────────────────────────────────────────────────────────────────

def train(X_train, y_train):
    from sklearn.ensemble import RandomForestClassifier
    print("\nTraining Random Forest...")
    print(f"  Training faces: {X_train.shape[0]:,}")
    t0 = time.time()
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=None,
        min_samples_leaf=2,
        n_jobs=-1,           # use all CPU cores
        random_state=42,
        verbose=0,
    )
    clf.fit(X_train, y_train)
    print(f"  Training done in {time.time()-t0:.1f}s")
    return clf

# ── evaluation ────────────────────────────────────────────────────────────────

def evaluate(clf, X_test, y_test, taxonomy: dict) -> dict:
    from sklearn.metrics import classification_report, accuracy_score
    print("\nEvaluating on test set...")
    t0 = time.time()
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"  Inference done in {time.time()-t0:.1f}s")
    print(f"\n  Overall accuracy: {acc*100:.1f}%  ({int(acc*len(y_test)):,}/{len(y_test):,} faces)\n")

    # Per-class report
    labels_present = sorted(np.unique(np.concatenate([y_test, y_pred])))
    target_names   = [taxonomy.get(int(l), f"class_{l}") for l in labels_present]
    report = classification_report(
        y_test, y_pred,
        labels=labels_present,
        target_names=target_names,
        digits=3,
        zero_division=0,
    )
    print(report)

    # Feature importance
    feat_names = ["area", "cx", "cy", "cz", "surf_type",
                  "neigh_degree", "neigh_type_mean", "neigh_type_std",
                  "neigh_area_mean", "neigh_area_std"]
    importances = clf.feature_importances_
    print("  Feature importances:")
    for name, imp in sorted(zip(feat_names, importances), key=lambda x: -x[1]):
        bar = "█" * int(imp * 50)
        print(f"    {name:22s} {imp:.4f}  {bar}")

    return {"accuracy": acc, "report": report}

# ── persistence ───────────────────────────────────────────────────────────────

def save_model(clf, taxonomy: dict):
    import joblib
    joblib.dump(clf, MODEL_PATH)
    # Save label map so predict script can decode class IDs
    with open(ENCODER_PATH, "w") as f:
        json.dump({str(k): v for k, v in taxonomy.items()}, f, indent=2)
    print(f"\n  Model saved:   {MODEL_PATH}")
    print(f"  Encoder saved: {ENCODER_PATH}")


def log_metrics(accuracy: float, n_train: int, n_test: int):
    today = date.today().isoformat()
    header = not METRICS_PATH.exists()
    with open(METRICS_PATH, "a") as f:
        if header:
            f.write("date,model,n_train_faces,n_test_faces,overall_accuracy,notes\n")
        f.write(f"{today},rf_mfcad_baseline,{n_train},{n_test},{accuracy:.4f},"
                f"RandomForest n_estimators=200 face-level 10-feat\n")
    print(f"  Metrics logged: {METRICS_PATH}")

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("ShiaanX — MFCAD++ Random Forest Baseline Trainer")
    print("=" * 60)

    taxonomy = load_taxonomy()

    print("\nLoading training data...")
    X_train, y_train = load_split(H5_DIR / "training_MFCAD++.h5", "train")

    print("\nLoading test data...")
    X_test, y_test = load_split(H5_DIR / "test_MFCAD++.h5", "test")

    clf = train(X_train, y_train)

    results = evaluate(clf, X_test, y_test, taxonomy)

    save_model(clf, taxonomy)
    log_metrics(results["accuracy"], len(X_train), len(X_test))

    print("\nDone.")


if __name__ == "__main__":
    main()
