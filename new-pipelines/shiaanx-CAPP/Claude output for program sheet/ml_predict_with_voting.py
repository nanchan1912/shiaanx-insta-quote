"""
ml_predict_with_voting.py — Inference with cluster-level majority voting.

Loads the baseline RF model (rf_classifier.pkl), runs face-level inference on the
MFCAD++ test set, then applies connected-component majority voting and compares
raw vs voted accuracy and per-class F1.

Usage:
    conda run -n occ python "Claude output for program sheet/ml_predict_with_voting.py"

Output files:
    models/perclass_f1_voted.json   — same schema as perclass_f1.json
"""

import json
import time
from collections import Counter
from pathlib import Path

import h5py
import joblib
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from sklearn.metrics import classification_report, accuracy_score

BASE          = Path(__file__).parent
H5_DIR        = BASE / "Dataset/MFCAD_dataset/MFCAD++_dataset/hierarchical_graphs"
MODELS_DIR    = BASE / "models"
MODEL_PATH    = MODELS_DIR / "rf_classifier.pkl"
TAXONOMY_PATH = BASE / "rule_sheets/07_label_taxonomy.json"
OUTPUT_PATH   = MODELS_DIR / "perclass_f1_voted.json"


def load_taxonomy() -> dict:
    with open(TAXONOMY_PATH) as f:
        data = json.load(f)
    return {m["mfcad_id"]: m["mfcad_name"] for m in data["mappings"]}


def extract_features_and_adj(grp):
    """Returns X (n_faces, 10), y (n_faces,), A (csr_matrix or None)."""
    V1      = grp["V_1"][:]
    labels  = grp["labels"][:]
    n_faces = V1.shape[0]

    adj_idx    = grp["A_1_idx"][:]
    adj_shape  = tuple(grp["A_1_shape"][:])
    adj_values = grp["A_1_values"][:]

    if adj_idx.shape[0] == 0 or adj_shape[0] != n_faces:
        neigh_features = np.zeros((n_faces, 5), dtype=np.float32)
        A = None
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
            degree, neigh_type_mean, neigh_type_std, neigh_area_mean, neigh_area_std,
        ], axis=1).astype(np.float32)

    X = np.concatenate([V1, neigh_features], axis=1)
    return X, labels.astype(np.int32), A


def apply_majority_voting(y_pred: np.ndarray, A) -> np.ndarray:
    """Replace each face's prediction with the majority label in its connected component."""
    if A is None:
        return y_pred.copy()
    n_comp, comp_ids = connected_components(A, directed=False)
    y_voted = y_pred.copy()
    for c in range(n_comp):
        mask = comp_ids == c
        labels_in_comp = y_pred[mask]
        majority = Counter(labels_in_comp.tolist()).most_common(1)[0][0]
        y_voted[mask] = majority
    return y_voted


def load_test_set():
    """Load test H5, return X, y_true, and per-PART adjacency matrices with global slices.

    Each H5 batch contains ~25 parts. The `idx` dataset maps each part within a batch to
    its local face range. We split the batch adjacency matrix into per-part sub-matrices
    so that majority voting operates within a single part, not across all parts in a batch.
    """
    X_parts, y_parts, slices = [], [], []
    t0 = time.time()
    global_offset = 0
    with h5py.File(H5_DIR / "test_MFCAD++.h5", "r") as f:
        batch_keys = sorted(f.keys(), key=lambda k: int(k))
        n = len(batch_keys)
        for i, key in enumerate(batch_keys):
            if i % 100 == 0:
                print(f"  Loading test: {i}/{n}", end="\r")
            grp = f[key]
            X_b, y_b, A_b = extract_features_and_adj(grp)
            X_parts.append(X_b)
            y_parts.append(y_b)
            n_faces_batch = X_b.shape[0]

            # Split batch into per-part face ranges using the idx dataset
            if "idx" in grp and A_b is not None:
                idx = grp["idx"][:]          # (n_parts, 2) — local face start indices
                base = int(idx[0, 0])        # first part's start (usually 0)
                local_starts = [int(idx[j, 0]) - base for j in range(len(idx))]
                local_starts.append(n_faces_batch)  # sentinel end

                for j in range(len(idx)):
                    lo, hi = local_starts[j], local_starts[j + 1]
                    if lo >= hi:
                        continue
                    # Extract the sub-matrix for this part
                    A_part = A_b[lo:hi, lo:hi]
                    slices.append((global_offset + lo, global_offset + hi, A_part))
            else:
                # Fallback: treat whole batch as one unit (no idx or no adjacency)
                slices.append((global_offset, global_offset + n_faces_batch, A_b))

            global_offset += n_faces_batch

    print(f"  Loaded {n} batches in {time.time()-t0:.1f}s          ")
    return np.concatenate(X_parts), np.concatenate(y_parts), slices


def parse_report_to_dict(report_str: str) -> dict:
    result = {}
    for line in report_str.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 5 and parts[0] not in ("accuracy", "macro", "weighted"):
            name = " ".join(parts[:-4])
            result[name] = {
                "precision": float(parts[-4]),
                "recall":    float(parts[-3]),
                "f1":        float(parts[-2]),
                "support":   int(parts[-1]),
            }
    return result


def main():
    print("Loading taxonomy...")
    taxonomy = load_taxonomy()

    print("Loading model...")
    clf = joblib.load(MODEL_PATH)

    print("Loading test set...")
    X_test, y_test, slices = load_test_set()
    print(f"  {X_test.shape[0]:,} faces total")

    # ── raw face-level inference ──────────────────────────────────────────────
    print("\nRunning inference...")
    t0 = time.time()
    y_pred_raw = clf.predict(X_test)
    print(f"  Inference done in {time.time()-t0:.1f}s")

    # ── per-batch majority voting ─────────────────────────────────────────────
    print("Applying majority voting...")
    y_pred_voted = y_pred_raw.copy()
    for start, end, A in slices:
        y_pred_voted[start:end] = apply_majority_voting(y_pred_raw[start:end], A)

    # ── evaluate both ─────────────────────────────────────────────────────────
    acc_raw   = accuracy_score(y_test, y_pred_raw)
    acc_voted = accuracy_score(y_test, y_pred_voted)

    labels_present = sorted(np.unique(np.concatenate([y_test, y_pred_raw, y_pred_voted])))
    target_names   = [taxonomy.get(int(l), f"class_{l}") for l in labels_present]

    report_raw   = classification_report(y_test, y_pred_raw,   labels=labels_present,
                                          target_names=target_names, digits=3, zero_division=0)
    report_voted = classification_report(y_test, y_pred_voted, labels=labels_present,
                                          target_names=target_names, digits=3, zero_division=0)

    raw_dict   = parse_report_to_dict(report_raw)
    voted_dict = parse_report_to_dict(report_voted)

    # ── comparison table ──────────────────────────────────────────────────────
    print(f"\nOverall accuracy — raw: {acc_raw*100:.1f}%   voted: {acc_voted*100:.1f}%  "
          f"(delta: {(acc_voted-acc_raw)*100:+.1f}%)\n")

    all_classes = sorted(voted_dict.keys(), key=lambda c: voted_dict[c]["f1"])
    print(f"  {'Class':<35} {'Raw F1':>7}  {'Voted F1':>8}  {'Delta':>6}")
    print("  " + "-" * 62)
    for cls in all_classes:
        raw_f1   = raw_dict.get(cls, {}).get("f1", 0.0)
        voted_f1 = voted_dict[cls]["f1"]
        delta    = voted_f1 - raw_f1
        flag     = " ▲" if delta > 0.01 else (" ▼" if delta < -0.01 else "")
        print(f"  {cls:<35} {raw_f1:>7.3f}  {voted_f1:>8.3f}  {delta:>+6.3f}{flag}")

    # ── save voted results ────────────────────────────────────────────────────
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "overall_accuracy_raw":   acc_raw,
            "overall_accuracy_voted": acc_voted,
            "per_class": voted_dict,
        }, f, indent=2)
    print(f"\nSaved voted results to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
