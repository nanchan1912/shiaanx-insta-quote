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
from collections import defaultdict
from pathlib import Path
from typing import Dict, Tuple


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

# Maximum radius (mm) of the narrowest bore step that can still be drilled
# with a standard twist drill.  If even the pilot step of a counterbore
# exceeds this, the whole feature requires a boring bar — classify as large_bore.
# (Distinct from LARGE_BORE_RADIUS_MM which guards single-step bores.)
DRILL_MAX_RADIUS_MM = 8.0


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
# Batch classification
# ---------------------------------------------------------------------------

def classify_clusters(clustered_data: Dict) -> Dict:
    """
    Classify all clusters in the clustered JSON.

    Takes the full parsed JSON dict from cluster_features.py output.
    Returns a new dict with 'feature_type' and 'confidence' added to
    every cluster. All original fields are preserved unchanged.

    Parameters
    ----------
    clustered_data : dict
        Parsed JSON from cluster_features.py. Must have a 'clusters' key.

    Returns
    -------
    dict
        Same structure with 'feature_type' and 'confidence' added to
        each cluster dict.
    """
    result = copy.deepcopy(clustered_data)

    for cluster in result['clusters']:
        feature_type, confidence = classify_cluster(cluster)
        cluster['feature_type'] = feature_type
        cluster['confidence']   = confidence

    return result


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
# Entry point for standalone use
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python classify_features.py <clustered_json> [output_json]")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = (sys.argv[2] if len(sys.argv) > 2
                   else str(Path(input_path).with_suffix('')) + '_classified.json')

    with open(input_path) as f:
        data = json.load(f)

    classified = classify_clusters(data)
    print_classification_summary(classified)
    save_classified(classified, output_path)
