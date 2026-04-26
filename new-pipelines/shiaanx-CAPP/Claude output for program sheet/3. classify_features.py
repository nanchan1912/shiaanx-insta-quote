"""
classify_features.py
--------------------
Feature classification step. Takes the clustered JSON output from
cluster_features.py and assigns a 'feature_type' label to every cluster.

This is the step between clustering and process selection.
Clustering said: "these faces belong together."
Classification says: "and this is what kind of feature it is."

Every downstream step — process selection, tool selection, parameter
calculation — uses the feature_type label as its primary input.

Usage
-----
    python classify_features.py <clustered_json> [output_json]

    python classify_features.py Hub_clustered_features.json Hub_classified.json

Or from Python:
    from classify_features import classify_clusters
    result = classify_clusters(clustered_data)

Feature Types Produced
----------------------
Bore family:
    through_hole        Drilled all the way through. No floor face.
    blind_hole          Drilled to a depth and stopped. Has a flat floor.
    counterbore         Stepped hole -- wide entry, narrower bore below.
                        Requires two tools (drill + counterbore cutter).
    large_bore          Diameter too large for a drill. Requires turning
                        or boring bar operation.
    tapped_hole         Threaded bore. Emitted when cluster has is_tapped=true.
                        Process: spot_drill → twist_drill → tap_rh (G84).
                        Set is_tapped on the cluster to activate this path;
                        automatic STEP thread geometry detection is future work.

Slot family:
    slot                Channel cut into the part with a slot mill.
                        Detected as a pair of bore clusters (the two semi-
                        circular ends) with matching radius, parallel axes,
                        and matching depth.  internal_corner_radius = arc radius.

Pocket family:
    pocket              Flat-floored enclosed recess.  Classified when a plane
                        cluster has >= 2 adjacent perpendicular wall faces AND
                        a total face area below POCKET_MAX_AREA_MM2 (to avoid
                        misclassifying large datum faces).
                        internal_corner_radius = smallest adjacent cylinder radius.

Boss family:
    boss                Cylinder protruding outward from the part surface.
                        Requires turning or milling.

Planar family:
    planar_face         Flat face identified as a datum, flange, or step.

Non-feature:
    background          Stock faces, structural geometry -- not directly
                        machined as individual features.

Angled modifier:
    Any type above can have '_angled' appended if is_principal_axis is False.
    Example: 'counterbore_angled', 'through_hole_angled', 'slot_angled'
    This signals the setup planner that the part must be rotated for this feature.

Confidence Levels
-----------------
Each classified cluster also gets a 'confidence' field:

    high    All signals agree. Classification is unambiguous.
            Safe to use directly in process selection.

    medium  Classification is likely correct but based on inference
            (e.g., a single-face bore classified as through_hole because
            its depth-to-diameter ratio is very small).
            Worth a quick human sanity check on new part types.

    low     Ambiguous case. No cap face was captured (face_count == 1)
            and geometry alone cannot distinguish blind from through.
            Treat as a flag for manual review.

Classification Logic Summary
----------------------------
The full decision tree is documented inside classify_cluster() below.
The key signals used, in order of priority:

    1. seed_type       ('bore', 'boss', 'plane', 'background')
    2. len(radii)      Multiple radii → stepped/counterbore feature
    3. max(radii)      Very large radius → large_bore (turning operation)
    4. face_count      2+ faces → through_hole (both ends of hole captured)
    5. depth/diameter  For single-face bores: shallow = through arc segment,
                       deep = likely blind
    6. is_principal_axis  False → append '_angled'
"""

import copy
import json
import sys
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import numpy as np
    from scipy.sparse import csr_matrix
    _ML_DEPS_AVAILABLE = True
except ImportError:
    _ML_DEPS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Thresholds — adjust here if working with a different part family
# ---------------------------------------------------------------------------

# Radius (mm) above which a bore is classified as large_bore rather than
# through_hole / blind_hole / counterbore. At this size, a standard drill
# cannot be used — you need a boring bar or facing operation.
LARGE_BORE_RADIUS_MM = 10.0

# Depth-to-diameter ratio threshold for single-face bore disambiguation.
# A bore seen as only ONE cylinder face with a very small depth/diameter
# ratio is almost certainly an arc segment of a through-hole (the bore
# passes through the part, but only a thin arc of the cylinder wall was
# captured as a distinct face). A genuine blind hole is always deeper
# relative to its diameter.
#
# How to read it: if depth / (2 * radius) <= this value, call it through_hole.
# 0.5 means: if the depth is less than half the diameter, it's a thin arc.
SINGLE_FACE_THROUGH_HOLE_DDR_MAX = 0.5

# Maximum total face area (mm^2) for a plane cluster to be classified as
# a pocket rather than a planar_face/datum.  A large flat face (e.g. the
# top stock surface) may have perpendicular neighbour walls but is NOT a
# pocket -- its area exceeds this threshold.
POCKET_MAX_AREA_MM2 = 500.0

# Maximum number of perpendicular walls a plane cluster can have and still
# be classified as a pocket.  Real machined pockets have 2–6 walls at most.
# A stock face or complex datum face may have many more adjacent walls
# (e.g. 15) — capping here prevents those from being called pockets.
POCKET_MAX_PERP_WALLS = 8

# Maximum radius (mm) of the narrowest bore step that can still be drilled
# with a standard twist drill.  If even the pilot step of a counterbore
# exceeds this, the whole feature requires a boring bar — classify as large_bore.
# (Distinct from LARGE_BORE_RADIUS_MM which guards single-step bores.)
DRILL_MAX_RADIUS_MM = 8.0


# ---------------------------------------------------------------------------
# Rule sheet loader (Sheet 1: 01_feature_classification.json)
# ---------------------------------------------------------------------------
_RULE_SHEET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rule_sheets')
_CLASSIFY_RULE_SHEET_PATH = os.path.join(_RULE_SHEET_DIR, '01_feature_classification.json')


def load_feature_classification_rule_sheet(path: str = None) -> Dict:
    """
    Load Sheet 1 classification rules (JSON).

    Safe-by-default: if missing/invalid, returns {} and hardcoded constants stay.
    """
    p = path or _CLASSIFY_RULE_SHEET_PATH
    if not os.path.exists(p):
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _apply_feature_classification_rules(rules: Dict) -> None:
    global LARGE_BORE_RADIUS_MM
    global SINGLE_FACE_THROUGH_HOLE_DDR_MAX
    global POCKET_MAX_AREA_MM2
    global DRILL_MAX_RADIUS_MM
    global POCKET_MAX_PERP_WALLS

    thresholds = (rules.get('thresholds_mm') or {}) if isinstance(rules, dict) else {}

    def _get_value(key: str):
        v = thresholds.get(key)
        if isinstance(v, dict) and 'value' in v:
            return v['value']
        return None

    v = _get_value('large_bore_radius_mm')
    if v is not None:
        LARGE_BORE_RADIUS_MM = float(v)

    v = _get_value('single_face_through_hole_ddr_max')
    if v is not None:
        SINGLE_FACE_THROUGH_HOLE_DDR_MAX = float(v)

    v = _get_value('pocket_max_area_mm2')
    if v is not None:
        POCKET_MAX_AREA_MM2 = float(v)

    v = _get_value('drill_max_radius_mm')
    if v is not None:
        DRILL_MAX_RADIUS_MM = float(v)

    v = _get_value('pocket_max_perp_walls')
    if v is not None:
        POCKET_MAX_PERP_WALLS = int(v)


# Apply rule sheet at import time (best-effort).
_classify_rules = load_feature_classification_rule_sheet()
if _classify_rules:
    _apply_feature_classification_rules(_classify_rules)


# ---------------------------------------------------------------------------
# Core classification function
# ---------------------------------------------------------------------------

def classify_cluster(cluster: Dict) -> Tuple[str, str]:
    """
    Classify a single cluster dict and return (feature_type, confidence).

    Parameters
    ----------
    cluster : dict
        One cluster from the clusters list in the clustered JSON.
        Expected keys: seed_type, radii, depth, face_count, is_principal_axis.

    Returns
    -------
    feature_type : str
        The classification label. See module docstring for full list.
    confidence : str
        'high', 'medium', or 'low'. See module docstring for meaning.
    """
    seed_type       = cluster.get('seed_type', 'unknown')
    radii           = cluster.get('radii', [])
    depth           = cluster.get('depth')
    face_count      = cluster.get('face_count', 0)
    is_principal    = cluster.get('is_principal_axis')

    # ------------------------------------------------------------------
    # Non-feature clusters — no further analysis needed
    # ------------------------------------------------------------------
    if seed_type == 'background':
        return 'background', 'high'

    if seed_type in ('plane', 'plane_feature'):
        has_perp   = cluster.get('has_perpendicular_walls', False)
        perp_count = cluster.get('perp_wall_count', 0)
        face_area  = cluster.get('face_area')

        if (has_perp
                and perp_count >= 2
                and perp_count <= POCKET_MAX_PERP_WALLS
                and face_area is not None
                and face_area < POCKET_MAX_AREA_MM2):
            if is_principal is False:
                # Angled pockets need a rotated setup — more risk regardless of
                # wall count, so cap confidence at medium.
                return 'pocket_angled', 'medium'
            confidence = 'high' if perp_count >= 3 else 'medium'
            return 'pocket', confidence

        return 'planar_face', 'high'

    # ------------------------------------------------------------------
    # Slot clusters (merged bore pairs from detect_slots)
    # ------------------------------------------------------------------
    if seed_type == 'slot':
        feature_type = 'slot_angled' if is_principal is False else 'slot'
        return feature_type, 'high'

    # ------------------------------------------------------------------
    # Boss clusters
    # ------------------------------------------------------------------
    if seed_type == 'boss':
        feature_type = 'boss'
        confidence   = 'high'
        # Angled boss (e.g. a peg on an inclined face)
        if is_principal is False:
            feature_type = 'boss_angled'
        return feature_type, confidence

    # ------------------------------------------------------------------
    # Bore clusters — main classification tree
    # ------------------------------------------------------------------
    if seed_type == 'bore':

        # Tapped hole check — highest priority within bore family.
        # Set is_tapped=true on the cluster to activate this path.
        # (Automatic detection requires STEP thread geometry parsing — future work.)
        if cluster.get('is_tapped'):
            feature_type = 'tapped_hole_angled' if is_principal is False else 'tapped_hole'
            return feature_type, 'high'


        feature_type = None
        confidence   = 'high'

        # --- Signal 1: Multiple distinct radii → stepped/counterbore ---
        # This is the strongest signal. If a bore cluster has 2+ different
        # radii, it is definitely a stepped hole regardless of face count.
        if len(radii) >= 2:
            max_radius = max(radii)
            min_radius = min(radii)
            if max_radius >= LARGE_BORE_RADIUS_MM:
                # Largest step requires a boring bar — whole feature is large_bore
                feature_type = 'large_bore'
            elif min_radius >= DRILL_MAX_RADIUS_MM:
                # Pilot (narrowest) step is already too large for a twist drill;
                # boring bar or interpolated milling required throughout.
                feature_type = 'large_bore'
            else:
                # Standard counterbore: e.g. [0.63, 1.4, 1.8]mm radii
                feature_type = 'counterbore'
            confidence = 'high'

        # --- Signal 2: Single radius ---
        elif len(radii) == 1:
            radius = radii[0]

            # Very large radius → turning/boring, regardless of face count
            if radius >= LARGE_BORE_RADIUS_MM:
                feature_type = 'large_bore'
                confidence   = 'high'

            # Two or more faces → both ends of the hole were captured.
            # This is the clearest through_hole signal: the BFS found
            # cylinder faces on both the entry side and exit side.
            elif face_count >= 2:
                feature_type = 'through_hole'
                confidence   = 'high'

            # Single face → more ambiguous. Use depth/diameter ratio.
            else:
                if depth is not None and radius > 0:
                    ddr = depth / (2.0 * radius)
                else:
                    ddr = None

                if ddr is not None and ddr <= SINGLE_FACE_THROUGH_HOLE_DDR_MAX:
                    # Very shallow relative to diameter = arc segment of
                    # a through-hole. The bore passes through the part but
                    # the arc is thin because it intersects an angled face.
                    feature_type = 'through_hole'
                    confidence   = 'medium'   # inferred, not confirmed by 2 faces
                else:
                    # Deeper relative to diameter = likely a true blind hole.
                    # No cap face was captured, so we cannot confirm, but the
                    # geometry is consistent with a blind hole.
                    # Also reached when depth is None or radius == 0 (ddr = None)
                    # — geometry is insufficient to classify; treat as blind with
                    # low confidence and flag for manual review.
                    feature_type = 'blind_hole'
                    confidence   = 'low'      # no cap face — flag for review

        # --- No radii at all (should not happen for a bore, but handle it) ---
        else:
            feature_type = 'unknown_bore'
            confidence   = 'low'

        # --- Angled modifier ---
        # Applied after the main type is decided.
        # Does NOT apply to unknown_bore (axis undefined — no setup signal).
        # large_bore on an angled face → large_bore_angled (needs rotary setup).
        if is_principal is False and feature_type != 'unknown_bore':
            feature_type = feature_type + '_angled'

        return feature_type, confidence

    # ------------------------------------------------------------------
    # Fallback — unknown seed_type (future-proofing)
    # ------------------------------------------------------------------
    return 'unknown', 'low'




# ---------------------------------------------------------------------------
# Summary printer (for validation and debugging)
# ---------------------------------------------------------------------------

def print_classification_summary(classified_data: Dict):
    """
    Print a human-readable summary of classification results.
    Useful for validating the output before passing to process selection.
    """
    clusters = classified_data['clusters']

    # Group by feature_type
    by_type = defaultdict(list)
    for c in clusters:
        by_type[c['feature_type']].append(c)

    print(f"Classification summary — {len(clusters)} clusters total")
    print()

    type_order = [
        'through_hole', 'through_hole_angled',
        'blind_hole',   'blind_hole_angled',
        'counterbore',  'counterbore_angled',
        'large_bore',   'large_bore_angled',
        'boss',         'boss_angled',
        'slot',         'slot_angled',
        'pocket',       'pocket_angled',
        'planar_face',
        'background',
        'unknown', 'unknown_bore',
    ]
    # Put any unlisted types at the end
    all_types = type_order + [t for t in by_type if t not in type_order]

    for ft in all_types:
        if ft not in by_type:
            continue
        group = by_type[ft]
        print(f"  {ft} ({len(group)})")
        for c in group:
            cid    = c.get('cluster_id', '?')
            radii  = c.get('radii', [])
            depth  = c.get('depth')
            conf   = c.get('confidence', '?')
            faces  = c.get('face_count', 0)
            # diameter/depth string
            if radii and depth is not None:
                dims = f"r={radii[0]}mm d={depth}mm"
            elif radii:
                dims = f"r={radii[0]}mm"
            else:
                dims = "—"
            print(f"    C{cid:2d} | {dims:28s} | faces={faces} | conf={conf}")
    print()

    # Flag low-confidence clusters
    low_conf = [c for c in clusters if c.get('confidence') == 'low']
    if low_conf:
        print(f"  [!] {len(low_conf)} low-confidence cluster(s) - recommend manual review:")
        for c in low_conf:
            print(f"    C{c['cluster_id']:2d} | {c['feature_type']} | "
                  f"r={c['radii']} depth={c['depth']} faces={c['face_count']}")
    else:
        print("  [ok] No low-confidence clusters.")
    print()


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def save_classified(classified_data: Dict, output_path: str):
    """Save classified results to a JSON file."""
    with open(output_path, 'w') as f:
        json.dump(classified_data, f, indent=2)
    print(f"Classified features saved to: {output_path}")


# ---------------------------------------------------------------------------
# ML classification (--mode ml)
# ---------------------------------------------------------------------------

# OCC GeomAbs_SurfaceType integer encoding, normalised by 11 (same as H5 training data)
_SURF_TYPE_INT = {'Plane': 0, 'Cylinder': 1, 'Cone': 2, 'Sphere': 3,
                  'Torus': 4, 'BSpline': 6}
_SURF_TYPE_NORM_FACTOR = 11.0


def _get_face_centroid(face: Dict) -> Tuple[float, float, float]:
    st = face.get('surface_type', '')
    if st == 'Cylinder' and 'cylinder' in face:
        loc = face['cylinder']['axis_location']
        return loc['x'], loc['y'], loc['z']
    if st == 'Plane' and 'plane' in face:
        orig = face['plane']['origin']
        return orig['x'], orig['y'], orig['z']
    if st == 'Cone' and 'cone' in face:
        d = face['cone']
        pt = d.get('apex', d.get('axis_location', {}))
        return pt.get('x', 0.0), pt.get('y', 0.0), pt.get('z', 0.0)
    if st == 'Torus' and 'torus' in face:
        c = face['torus'].get('center', {})
        return c.get('x', 0.0), c.get('y', 0.0), c.get('z', 0.0)
    return 0.0, 0.0, 0.0


def _extract_ml_features(features_data: Dict) -> 'np.ndarray':
    """Build (n_faces, 15) feature matrix matching the v2 RF training schema."""
    faces = features_data['faces']['faces']
    n = len(faces)
    bb = features_data.get('bounding_box', {})
    bb_min = np.array([bb.get('xmin', 0), bb.get('ymin', 0), bb.get('zmin', 0)], dtype=np.float32)
    bb_size = np.array([
        bb.get('length_x', 1) or 1,
        bb.get('length_y', 1) or 1,
        bb.get('length_z', 1) or 1,
    ], dtype=np.float32)

    # --- V_1: area (normalised), cx/cy/cz (normalised), surf_type (normalised) ---
    areas_raw = np.array([f.get('area', 0.0) for f in faces], dtype=np.float32)
    max_area = areas_raw.max() if areas_raw.max() > 0 else 1.0
    areas = areas_raw / max_area

    centroids = np.zeros((n, 3), dtype=np.float32)
    surf_types = np.zeros(n, dtype=np.float32)
    for i, face in enumerate(faces):
        cx, cy, cz = _get_face_centroid(face)
        centroids[i] = [
            (cx - bb_min[0]) / bb_size[0],
            (cy - bb_min[1]) / bb_size[1],
            (cz - bb_min[2]) / bb_size[2],
        ]
        st_int = _SURF_TYPE_INT.get(face.get('surface_type', ''), 5)
        surf_types[i] = st_int / _SURF_TYPE_NORM_FACTOR

    # --- Adjacency matrix from features JSON ---
    adj_raw = features_data.get('face_adjacency', {})
    rows, cols, vals = [], [], []
    for k, neighbours in adj_raw.items():
        i = int(k)
        for j in neighbours:
            if 0 <= i < n and 0 <= j < n:
                rows.append(i); cols.append(j); vals.append(1.0)
    if rows:
        A = csr_matrix((vals, (rows, cols)), shape=(n, n), dtype=np.float32)
    else:
        A = csr_matrix((n, n), dtype=np.float32)

    # --- 1-hop neighbourhood features ---
    degree = np.array(A.sum(axis=1)).flatten()
    safe_deg = np.where(degree > 0, degree, 1.0)
    neigh_area_mean = A.dot(areas) / safe_deg
    neigh_type_mean = A.dot(surf_types) / safe_deg
    neigh_area_std  = np.sqrt(np.maximum(A.dot(areas**2)      / safe_deg - neigh_area_mean**2, 0))
    neigh_type_std  = np.sqrt(np.maximum(A.dot(surf_types**2) / safe_deg - neigh_type_mean**2, 0))

    # --- two_hop_degree ---
    two_hop = np.array((A @ A).sum(axis=1)).flatten().astype(np.float32)

    # --- Component features using B-Rep connected components (matches training) ---
    from scipy.sparse.csgraph import connected_components as _connected_components
    n_comp, comp_ids = _connected_components(A, directed=False)

    comp_size         = np.zeros(n, dtype=np.float32)
    comp_type_div     = np.zeros(n, dtype=np.float32)
    comp_area_ratio   = np.ones(n,  dtype=np.float32)
    comp_aspect_ratio = np.ones(n,  dtype=np.float32)

    for c in range(n_comp):
        mask = comp_ids == c
        idx  = np.where(mask)[0]
        size = idx.shape[0]
        comp_size[mask] = float(size)
        t = surf_types[idx]
        comp_type_div[mask] = float(t.std()) if size > 1 else 0.0
        a = areas[idx]
        mean_a = a.mean()
        comp_area_ratio[mask] = a / mean_a if mean_a > 0 else np.ones(size)
        if size > 1:
            rng = centroids[idx].max(axis=0) - centroids[idx].min(axis=0)
            rng_sorted = np.sort(rng)
            mn, mx = rng_sorted[0], rng_sorted[-1]
            comp_aspect_ratio[mask] = float(mx / mn) if mn > 1e-9 else 1.0

    V1    = np.stack([areas, centroids[:, 0], centroids[:, 1], centroids[:, 2], surf_types], axis=1)
    neigh = np.stack([degree, neigh_type_mean, neigh_type_std, neigh_area_mean, neigh_area_std], axis=1)
    comp  = np.stack([comp_size, comp_type_div, comp_area_ratio, two_hop, comp_aspect_ratio], axis=1)
    return np.concatenate([V1, neigh, comp], axis=1).astype(np.float32)


def _load_ml_resources():
    """Load the best available RF model + MFCAD-id → internal_feature_type map.

    Prefers rf_classifier_v3.pkl (pipeline-native features, no train/inference gap)
    over rf_classifier_v2.pkl (H5-based features).
    """
    import joblib
    base = Path(__file__).parent
    for model_name in ('rf_classifier_v3.pkl', 'rf_classifier_v2.pkl'):
        model_path = base / 'models' / model_name
        if model_path.exists():
            model = joblib.load(model_path)
            break
    else:
        raise FileNotFoundError(
            "No RF model found in models/.  Run ml_train_classifier_v3.py first.")
    with open(base / 'rule_sheets' / '07_label_taxonomy.json') as f:
        taxonomy_data = json.load(f)
    mfcad_to_internal = {m['mfcad_id']: m['internal_feature_type']
                         for m in taxonomy_data['mappings']}
    return model, mfcad_to_internal


def classify_clusters_ml(clustered_data: Dict, features_data: Dict) -> Dict:
    """
    ML-based cluster classification using the v2 Random Forest model.

    Each face is scored independently; the majority MFCAD++ class across a
    cluster's face_indices becomes that cluster's prediction.  The MFCAD++
    class is then translated to an internal feature_type via the taxonomy.
    The _angled modifier is still applied from is_principal_axis.
    """
    if not _ML_DEPS_AVAILABLE:
        raise RuntimeError("numpy/scipy not available — cannot use --mode ml")

    model, mfcad_to_internal = _load_ml_resources()
    X = _extract_ml_features(features_data)
    y_pred = model.predict(X)

    result = copy.deepcopy(clustered_data)
    n_faces = X.shape[0]

    for cluster in result['clusters']:
        face_indices = [i for i in cluster.get('face_indices', []) if i < n_faces]
        if face_indices:
            preds = [int(y_pred[i]) for i in face_indices]
            majority_id = Counter(preds).most_common(1)[0][0]
            vote_counts = dict(Counter(preds))
        else:
            majority_id = 24  # Stock → background
            vote_counts = {}

        internal_type = mfcad_to_internal.get(majority_id, 'unknown')

        # Apply _angled modifier (ML model has no setup-axis knowledge)
        is_principal = cluster.get('is_principal_axis')
        if (is_principal is False
                and internal_type not in ('background', 'unknown')
                and not internal_type.endswith('_angled')):
            internal_type = internal_type + '_angled'

        cluster['feature_type']    = internal_type
        cluster['confidence']      = 'medium'
        cluster['ml_mfcad_id']     = majority_id
        cluster['ml_vote_counts']  = vote_counts

    return result


# ---------------------------------------------------------------------------
# Batch classification (rule or ml)
# ---------------------------------------------------------------------------

def classify_clusters(clustered_data: Dict,
                      mode: str = 'rule',
                      features_data: Optional[Dict] = None) -> Dict:
    """
    Classify all clusters in the clustered JSON.

    Parameters
    ----------
    clustered_data : dict
        Parsed JSON from cluster_features.py.
    mode : str
        'rule' (default) — rule-based heuristics.
        'ml'             — Random Forest v2 model (requires features_data).
    features_data : dict, optional
        Parsed features JSON from extract_features.py.  Required when mode='ml'.

    Returns
    -------
    dict  Same structure with 'feature_type' and 'confidence' added to each cluster.
    """
    if mode == 'ml':
        if features_data is None:
            raise ValueError("features_data must be provided when mode='ml'")
        return classify_clusters_ml(clustered_data, features_data)

    # --- rule mode (original behaviour) ---
    result = copy.deepcopy(clustered_data)
    for cluster in result['clusters']:
        feature_type, confidence = classify_cluster(cluster)
        cluster['feature_type'] = feature_type
        cluster['confidence']   = confidence
    return result


# ---------------------------------------------------------------------------
# Entry point for standalone use
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Classify clustered features.')
    parser.add_argument('input',  help='*_clustered.json from cluster_features.py')
    parser.add_argument('output', nargs='?',
                        help='Output JSON path (default: input + _classified.json)')
    parser.add_argument('--mode', choices=['rule', 'ml'], default='rule',
                        help='rule (default) or ml (RF v2 model)')
    parser.add_argument('--features',
                        help='*_features.json required for --mode ml; '
                             'inferred from input path if omitted')
    args = parser.parse_args()

    input_path  = args.input
    output_path = args.output or (str(Path(input_path).with_suffix('')) + '_classified.json')

    with open(input_path) as f:
        data = json.load(f)

    features_data = None
    if args.mode == 'ml':
        features_path = args.features
        if not features_path:
            stem = Path(input_path).stem
            features_path = str(Path(input_path).parent /
                                 (stem.replace('_clustered', '') + '_features.json'))
        if not os.path.exists(features_path):
            print(f"ERROR: features file not found: {features_path}")
            print("Pass --features <path> to specify it explicitly.")
            sys.exit(1)
        with open(features_path) as f:
            features_data = json.load(f)
        print(f"ML mode — features: {features_path}")

    classified = classify_clusters(data, mode=args.mode, features_data=features_data)
    print_classification_summary(classified)
    save_classified(classified, output_path)
