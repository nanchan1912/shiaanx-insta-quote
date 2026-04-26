"""
ml_train_classifier_v2.py — Train RF classifier with 15 face-level features (v2).

Adds 5 cluster-proxy features derived from connected components:
  - comp_size, comp_type_diversity, comp_area_ratio, two_hop_degree, comp_aspect_ratio

Usage:
    conda run -n occ python "Claude output for program sheet/ml_train_classifier_v2.py"

Output files:
    models/rf_classifier_v2.pkl
    models/rf_label_encoder_v2.json
    metrics_log.csv  (appended)
"""

import json
import time
import warnings
from datetime import date
from pathlib import Path

import h5py
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

warnings.filterwarnings("ignore")

BASE       = Path(__file__).parent
H5_DIR     = BASE / "Dataset/MFCAD_dataset/MFCAD++_dataset/hierarchical_graphs"
MODELS_DIR = BASE / "models"
MODELS_DIR.mkdir(exist_ok=True)

MODEL_PATH    = MODELS_DIR / "rf_classifier_v2.pkl"
ENCODER_PATH  = MODELS_DIR / "rf_label_encoder_v2.json"
METRICS_PATH  = BASE / "metrics_log.csv"
TAXONOMY_PATH = BASE / "rule_sheets/07_label_taxonomy.json"

# All 15 features, indices 0–14
FEAT_NAMES = [
    # 0–4: raw face features from V_1
    "area", "cx", "cy", "cz", "surf_type",
    # 5–9: 1-hop neighbourhood aggregates
    "neigh_degree", "neigh_type_mean", "neigh_type_std", "neigh_area_mean", "neigh_area_std",
    # 10–14: connected-component (cluster proxy) features
    "comp_size", "comp_type_diversity", "comp_area_ratio", "two_hop_degree", "comp_aspect_ratio",
]


def load_taxonomy() -> dict:
    with open(TAXONOMY_PATH) as f:
        data = json.load(f)
    return {m["mfcad_id"]: m["mfcad_name"] for m in data["mappings"]}


def extract_features_from_batch(grp) -> tuple[np.ndarray, np.ndarray]:
    V1      = grp["V_1"][:]       # (n_faces, 5)
    labels  = grp["labels"][:]
    n_faces = V1.shape[0]

    adj_idx    = grp["A_1_idx"][:]
    adj_shape  = tuple(grp["A_1_shape"][:])
    adj_values = grp["A_1_values"][:]

    no_edges = adj_idx.shape[0] == 0 or adj_shape[0] != n_faces

    if no_edges:
        neigh_features = np.zeros((n_faces, 5), dtype=np.float32)
        comp_features  = np.zeros((n_faces, 5), dtype=np.float32)
        comp_features[:, 0] = 1.0  # comp_size = 1 (each face is its own component)
        comp_features[:, 2] = 1.0  # comp_area_ratio = 1.0
        comp_features[:, 4] = 1.0  # comp_aspect_ratio = 1.0
    else:
        A = csr_matrix(
            (adj_values, (adj_idx[:, 0], adj_idx[:, 1])),
            shape=adj_shape, dtype=np.float32,
        )

        # ── 1-hop neighbourhood features ──────────────────────────────────────
        degree         = np.array(A.sum(axis=1)).flatten()
        neigh_area_sum = A.dot(V1[:, 0])
        neigh_type_sum = A.dot(V1[:, 4])
        neigh_area_sq  = A.dot(V1[:, 0] ** 2)
        neigh_type_sq  = A.dot(V1[:, 4] ** 2)
        safe_deg       = np.where(degree > 0, degree, 1.0)
        neigh_area_mean = neigh_area_sum / safe_deg
        neigh_type_mean = neigh_type_sum / safe_deg
        neigh_area_std  = np.sqrt(np.maximum(neigh_area_sq / safe_deg - neigh_area_mean**2, 0))
        neigh_type_std  = np.sqrt(np.maximum(neigh_type_sq / safe_deg - neigh_type_mean**2, 0))
        neigh_features  = np.stack([
            degree, neigh_type_mean, neigh_type_std, neigh_area_mean, neigh_area_std,
        ], axis=1).astype(np.float32)

        # ── connected-component features ──────────────────────────────────────
        n_comp, comp_ids = connected_components(A, directed=False)

        comp_size         = np.zeros(n_faces, dtype=np.float32)
        comp_type_div     = np.zeros(n_faces, dtype=np.float32)
        comp_area_ratio   = np.ones(n_faces,  dtype=np.float32)
        comp_aspect_ratio = np.ones(n_faces,  dtype=np.float32)

        for c in range(n_comp):
            mask = comp_ids == c
            idx  = np.where(mask)[0]
            size = idx.shape[0]
            comp_size[mask] = float(size)

            types = V1[idx, 4]
            comp_type_div[mask] = float(types.std()) if size > 1 else 0.0

            areas = V1[idx, 0]
            mean_area = areas.mean()
            comp_area_ratio[mask] = (areas / mean_area if mean_area > 0 else np.ones(size))

            if size > 1:
                coords = V1[idx, 1:4]  # cx, cy, cz
                ranges = coords.max(axis=0) - coords.min(axis=0)
                ranges_sorted = np.sort(ranges)
                min_r = ranges_sorted[0]
                max_r = ranges_sorted[-1]
                aspect = max_r / min_r if min_r > 1e-9 else 1.0
                comp_aspect_ratio[mask] = float(aspect)

        # two_hop_degree: A^2 row sum (degree of 2-hop neighbourhood)
        two_hop = np.array((A @ A).sum(axis=1)).flatten().astype(np.float32)

        comp_features = np.stack([
            comp_size, comp_type_div, comp_area_ratio, two_hop, comp_aspect_ratio,
        ], axis=1).astype(np.float32)

    X = np.concatenate([V1, neigh_features, comp_features], axis=1)  # (n_faces, 15)
    return X, labels.astype(np.int32)


def load_split(h5_path: Path, desc: str) -> tuple[np.ndarray, np.ndarray]:
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
    print(f"  {desc}: {X.shape[0]:,} faces, {X.shape[1]} features, {len(np.unique(y))} classes")
    return X, y


def train(X_train, y_train):
    from sklearn.ensemble import RandomForestClassifier
    print("\nTraining Random Forest (v2, 15 features)...")
    t0 = time.time()
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=None,
        min_samples_leaf=2,
        n_jobs=-1,
        random_state=42,
        verbose=0,
    )
    clf.fit(X_train, y_train)
    print(f"  Training done in {time.time()-t0:.1f}s")
    return clf


def evaluate(clf, X_test, y_test, taxonomy: dict) -> dict:
    from sklearn.metrics import classification_report, accuracy_score
    print("\nEvaluating on test set...")
    t0 = time.time()
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"  Inference done in {time.time()-t0:.1f}s")
    print(f"\n  Overall accuracy: {acc*100:.1f}%  ({int(acc*len(y_test)):,}/{len(y_test):,} faces)\n")

    labels_present = sorted(np.unique(np.concatenate([y_test, y_pred])))
    target_names   = [taxonomy.get(int(l), f"class_{l}") for l in labels_present]
    report = classification_report(
        y_test, y_pred, labels=labels_present,
        target_names=target_names, digits=3, zero_division=0,
    )
    print(report)

    importances = clf.feature_importances_
    print("  Feature importances:")
    for name, imp in sorted(zip(FEAT_NAMES, importances), key=lambda x: -x[1]):
        bar = "█" * int(imp * 50)
        print(f"    {name:26s} {imp:.4f}  {bar}")

    return {"accuracy": acc, "report": report}


def save_model(clf, taxonomy: dict):
    import joblib
    joblib.dump(clf, MODEL_PATH)
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
        f.write(f"{today},rf_mfcad_v2,{n_train},{n_test},{accuracy:.4f},"
                f"RandomForest n_estimators=200 15-feat comp+2hop\n")
    print(f"  Metrics logged: {METRICS_PATH}")


def main():
    print("=" * 60)
    print("ShiaanX — MFCAD++ Random Forest v2 (15 features)")
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
