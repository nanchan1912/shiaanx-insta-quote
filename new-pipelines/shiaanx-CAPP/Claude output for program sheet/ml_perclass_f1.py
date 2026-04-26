"""
ml_perclass_f1.py — Load saved RF model and print per-class F1 on MFCAD++ test set.

Usage:
    conda run -n occ python "Claude output for program sheet/ml_perclass_f1.py"

Saves per-class F1 to models/perclass_f1.json and prints a ranked table.
"""

import json
import time
from pathlib import Path

import h5py
import joblib
import numpy as np
from scipy.sparse import csr_matrix
from sklearn.metrics import classification_report, accuracy_score

BASE        = Path(__file__).parent
H5_DIR      = BASE / "Dataset/MFCAD_dataset/MFCAD++_dataset/hierarchical_graphs"
MODELS_DIR  = BASE / "models"
MODEL_PATH  = MODELS_DIR / "rf_classifier.pkl"
ENCODER_PATH = MODELS_DIR / "rf_label_encoder.json"
TAXONOMY_PATH = BASE / "rule_sheets/07_label_taxonomy.json"
OUTPUT_PATH = MODELS_DIR / "perclass_f1.json"


def load_taxonomy() -> dict:
    with open(TAXONOMY_PATH) as f:
        data = json.load(f)
    return {m["mfcad_id"]: m["mfcad_name"] for m in data["mappings"]}


def extract_features_from_batch(grp):
    V1     = grp["V_1"][:]
    labels = grp["labels"][:]
    n_faces = V1.shape[0]

    adj_idx    = grp["A_1_idx"][:]
    adj_shape  = tuple(grp["A_1_shape"][:])
    adj_values = grp["A_1_values"][:]

    if adj_idx.shape[0] == 0 or adj_shape[0] != n_faces:
        neigh_features = np.zeros((n_faces, 5), dtype=np.float32)
    else:
        A = csr_matrix(
            (adj_values, (adj_idx[:, 0], adj_idx[:, 1])),
            shape=adj_shape, dtype=np.float32,
        )
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
            degree, neigh_type_mean, neigh_type_std, neigh_area_mean, neigh_area_std
        ], axis=1).astype(np.float32)

    return np.concatenate([V1, neigh_features], axis=1), labels.astype(np.int32)


def load_test_set():
    X_parts, y_parts = [], []
    t0 = time.time()
    h5_path = H5_DIR / "test_MFCAD++.h5"
    with h5py.File(h5_path, "r") as f:
        batch_keys = sorted(f.keys(), key=lambda k: int(k))
        n = len(batch_keys)
        for i, key in enumerate(batch_keys):
            if i % 100 == 0:
                print(f"  Loading test: {i}/{n}", end="\r")
            X_b, y_b = extract_features_from_batch(f[key])
            X_parts.append(X_b)
            y_parts.append(y_b)
    print(f"  Loaded {n} batches in {time.time()-t0:.1f}s          ")
    return np.concatenate(X_parts), np.concatenate(y_parts)


def main():
    print("Loading taxonomy...")
    taxonomy = load_taxonomy()

    print("Loading model...")
    clf = joblib.load(MODEL_PATH)

    print("Loading test set...")
    X_test, y_test = load_test_set()

    print("Running inference...")
    t0 = time.time()
    y_pred = clf.predict(X_test)
    print(f"  Inference done in {time.time()-t0:.1f}s")

    acc = accuracy_score(y_test, y_pred)
    print(f"\nOverall accuracy: {acc*100:.1f}%  ({int(acc*len(y_test)):,}/{len(y_test):,} faces)\n")

    labels_present = sorted(np.unique(np.concatenate([y_test, y_pred])))
    target_names   = [taxonomy.get(int(l), f"class_{l}") for l in labels_present]

    report_str = classification_report(
        y_test, y_pred,
        labels=labels_present,
        target_names=target_names,
        digits=3,
        zero_division=0,
    )
    print(report_str)

    # Parse per-class F1 into dict and save
    report_dict = {}
    for line in report_str.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 5 and parts[0] not in ("accuracy", "macro", "weighted"):
            # class name may be multi-word — join all but last 4 tokens
            name = " ".join(parts[:-4])
            precision, recall, f1, support = parts[-4], parts[-3], parts[-2], parts[-1]
            report_dict[name] = {
                "precision": float(precision),
                "recall":    float(recall),
                "f1":        float(f1),
                "support":   int(support),
            }

    # Sort by F1 ascending so weak classes are at top
    sorted_rows = sorted(report_dict.items(), key=lambda x: x[1]["f1"])

    print("\nPer-class F1 (weakest first):")
    print(f"  {'Class':<30} {'F1':>6}  {'Precision':>9}  {'Recall':>6}  {'Support':>8}")
    print("  " + "-" * 68)
    for name, m in sorted_rows:
        bar = "█" * int(m["f1"] * 20)
        print(f"  {name:<30} {m['f1']:>6.3f}  {m['precision']:>9.3f}  {m['recall']:>6.3f}  {m['support']:>8,}  {bar}")

    with open(OUTPUT_PATH, "w") as f:
        json.dump({"overall_accuracy": acc, "per_class": report_dict}, f, indent=2)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
