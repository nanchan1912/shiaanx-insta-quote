"""
cluster_features.py
-------------------
Feature clustering pipeline: groups raw BRep faces into manufacturing features.

Phases:
    1. Build face adjacency graph          (via feature_graph.py)
    2. Identify feature seed faces         (faces that anchor a feature)
    3. Grow each seed into a face cluster  (BFS with geometric rules)
    4. Resolve overlaps, assign all faces  (every face gets exactly one cluster)

Output: list of cluster dicts, each representing one candidate manufacturing feature,
        ready to be passed to the classification step.

Design principle: generic — no assumptions about part type, orientation,
or number of features. Tolerances are configurable via ClusteringConfig.
"""

import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import networkx as nx

from feature_graph import build_face_graph, graph_summary
from geometry_utils import (
    axes_are_collinear,
    cone_axis_collinear_with_cylinder,
    torus_is_bridge,
    plane_closes_cylinder,
    planes_are_parallel,
    get_feature_axis,
    get_feature_depth,
    get_radii_sorted,
    is_principal_axis,
    to_vec,
)



# ---------------------------------------------------------------------------
# Local vector math helpers
# ---------------------------------------------------------------------------

def _dot(a, b):
    """Dot product of two iterables."""
    return sum(x * y for x, y in zip(a, b))


def _norm(v):
    """Euclidean length of a vector."""
    return _dot(v, v) ** 0.5


def _vec(v):
    """
    Normalise a vector to a plain Python list of floats.
    Accepts either a dict {'x':..,'y':..,'z':..} (as stored in the face graph
    JSON) or any iterable of numbers.
    """
    if isinstance(v, dict):
        return [float(v.get('x', 0)), float(v.get('y', 0)), float(v.get('z', 0))]
    return [float(x) for x in v]


def _axes_parallel(axis1, axis2, tol=1e-4):
    """
    True if two axis direction vectors are parallel (same or opposite direction).
    tol is the max deviation of |cos(angle)| from 1.0.
    """
    a = _vec(axis1)
    b = _vec(axis2)
    na = _norm(a)
    nb = _norm(b)
    if na < 1e-10 or nb < 1e-10:
        return False
    cos_a = abs(_dot(a, b)) / (na * nb)
    return abs(cos_a - 1.0) < tol


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ClusteringConfig:
    """
    All tolerance and threshold parameters in one place.
    Adjust these if working with very small parts (tighten) or
    rough castings (loosen).
    """
    # Collinearity check: max perpendicular distance between cylinder axes (mm)
    axis_collinear_distance_tol: float = 0.01

    # Collinearity check: max sine of angle between axis directions
    axis_direction_tol: float = 1e-4

    # Plane-closes-cylinder: max deviation from parallel between normal and axis
    plane_axis_direction_tol: float = 1e-3

    # Plane-closes-cylinder: how far outside cylinder radius the plane origin
    # can be and still be considered the cap (mm)
    plane_position_tol: float = 1.0

    # Parallel planes: max sine of angle between normals
    plane_parallel_tol: float = 1e-4

    # Minimum area for a face to be considered a primary stock/datum face (mm^2)
    # Faces larger than this are unlikely to be part of a hole feature
    large_face_area_threshold: float = 1000.0

    # Minimum area for a plane face to be seeded as a planar feature (mm^2)
    # Tiny planes (fillets, chamfer flats) are skipped — they will be picked
    # up by adjacent cylinder clusters or left in background.
    min_plane_seed_area: float = 5.0


# ---------------------------------------------------------------------------
# Phase 2 — Seed identification
# ---------------------------------------------------------------------------

def find_seeds(G: nx.Graph, config: ClusteringConfig) -> List[Dict]:
    """
    Identify seed faces — the primary face that anchors each feature.

    Seeding rules (in priority order):

    Rule 1 — Reversed cylinder:
        A cylinder with Reversed orientation = bore surface (hole, counterbore).
        Multiple Reversed cylinders on the SAME axis = one through-hole seen
        from both sides. Deduplicated by axis collinearity check — only the
        first one found per axis is kept as a seed.

    Rule 2 — Forward cylinder not collinear with any bore seed:
        A Forward cylinder (normal outward) that stands alone on its axis
        is a boss or peg. Deduplicated the same way — one seed per unique axis.

    Returns list of seed dicts:
        {
            'seed_face_index' : int,
            'seed_type'       : 'bore' | 'boss',
            'geometry'        : face node data dict
        }
    """
    seeds = []

    # Rule 1: Reversed cylinders — one seed per unique axis
    for node, data in G.nodes(data=True):
        if data['surface_type'] != 'Cylinder':
            continue
        if data['orientation'] != 'Reversed':
            continue

        # Check if this axis is already represented by an existing bore seed
        already_seeded = False
        for existing in seeds:
            if existing['seed_type'] == 'bore':
                if axes_are_collinear(
                    existing['geometry']['cylinder'],
                    data['cylinder'],
                    direction_tol=config.axis_direction_tol,
                    distance_tol=config.axis_collinear_distance_tol
                ):
                    already_seeded = True
                    break

        if not already_seeded:
            seeds.append({
                'seed_face_index': node,
                'seed_type': 'bore',
                'geometry': data
            })

    # Rule 2: Forward cylinders — one seed per unique axis not covered by a bore
    for node, data in G.nodes(data=True):
        if data['surface_type'] != 'Cylinder':
            continue
        if data['orientation'] != 'Forward':
            continue

        # Check against all existing seeds (bore and boss)
        already_seeded = False
        for existing in seeds:
            if existing['seed_type'] in ('bore', 'boss'):
                if axes_are_collinear(
                    existing['geometry']['cylinder'],
                    data['cylinder'],
                    direction_tol=config.axis_direction_tol,
                    distance_tol=config.axis_collinear_distance_tol
                ):
                    already_seeded = True
                    break

        if not already_seeded:
            seeds.append({
                'seed_face_index': node,
                'seed_type': 'boss',
                'geometry': data
            })

    # Rule 3: Plane faces — one seed per unique normal direction.
    # Deduplication uses planes_are_parallel (parallel OR anti-parallel normals
    # count as the same orientation family). grow_cluster will expand each seed
    # by collecting all graph-adjacent planes with a parallel normal.
    #
    # A plane is skipped if:
    #   - It is smaller than config.min_plane_seed_area (tiny fillets/chamfers
    #     that will be absorbed by adjacent cylinder clusters), OR
    #   - It is graph-adjacent to a cylinder seed face (it will be claimed by
    #     that cylinder cluster's cap-plane BFS — no need to duplicate it).
    cylinder_seed_indices = {s['seed_face_index'] for s in seeds}

    for node, data in G.nodes(data=True):
        if data['surface_type'] != 'Plane':
            continue

        if data['area'] < config.min_plane_seed_area:
            continue

        # Skip if directly adjacent to any cylinder seed face
        if any(nb in cylinder_seed_indices for nb in G.neighbors(node)):
            continue

        already_seeded = False
        for existing in seeds:
            if existing['seed_type'] == 'plane':
                if planes_are_parallel(
                    existing['geometry'],
                    data,
                    tol=config.plane_parallel_tol
                ):
                    already_seeded = True
                    break

        if not already_seeded:
            seeds.append({
                'seed_face_index': node,
                'seed_type': 'plane',
                'geometry': data
            })

    return seeds


# ---------------------------------------------------------------------------
# Phase 3 — Cluster growth
# ---------------------------------------------------------------------------

def grow_cluster(G: nx.Graph, seed: Dict, config: ClusteringConfig) -> List[int]:
    """
    BFS from the seed face, collecting all adjacent faces that belong to
    the same manufacturing feature.

    Growth rules applied at each BFS step:

    For cylinder-seeded clusters (bore or boss):
      - Adjacent cylinder with collinear axis     -> same feature
        (handles counterbores, chamfers, multi-diameter holes)
      - Adjacent plane that closes the cylinder   -> bottom/top cap of feature
        (uses plane_closes_cylinder from geometry_utils)

    For plane-seeded clusters (future extension):
      - Adjacent planes with parallel normals     -> same planar feature
        (handles steps, datum faces, pocket floors)

    Note: BFS continues from newly added faces, so transitively connected
    faces (e.g., counterbore wall -> cap plane -> chamfer cylinder) are
    all captured in one pass.

    Returns list of face indices belonging to this cluster.
    """
    seed_idx  = seed['seed_face_index']
    seed_data = seed['geometry']

    cluster = {seed_idx}
    queue   = [seed_idx]

    while queue:
        current_idx  = queue.pop(0)
        current_data = G.nodes[current_idx]

        for neighbour_idx in G.neighbors(current_idx):
            if neighbour_idx in cluster:
                continue

            neighbour_data = G.nodes[neighbour_idx]

            added = False

            # --- Cylinder seed: grow to adjacent cylinders on same axis ---
            if seed_data['surface_type'] == 'Cylinder':
                if neighbour_data['surface_type'] == 'Cylinder':
                    if axes_are_collinear(
                        seed_data['cylinder'],
                        neighbour_data['cylinder'],
                        direction_tol=config.axis_direction_tol,
                        distance_tol=config.axis_collinear_distance_tol
                    ):
                        cluster.add(neighbour_idx)
                        queue.append(neighbour_idx)
                        added = True

                # --- Cylinder seed: grow to cap planes ---
                if not added and neighbour_data['surface_type'] == 'Plane':
                    if plane_closes_cylinder(
                        neighbour_data,
                        seed_data,
                        direction_tol=config.plane_axis_direction_tol,
                        position_tol=config.plane_position_tol
                    ):
                        cluster.add(neighbour_idx)
                        queue.append(neighbour_idx)
                        added = True

                # --- Rule 3: Cylinder seed → Cone neighbour (countersink / chamfer) ---
                if not added and neighbour_data['surface_type'] == 'Cone':
                    if cone_axis_collinear_with_cylinder(
                        neighbour_data,
                        seed_data,
                        direction_tol=config.axis_direction_tol,
                        distance_tol=config.axis_collinear_distance_tol
                    ):
                        cluster.add(neighbour_idx)
                        queue.append(neighbour_idx)
                        added = True

            # --- Plane seed (for future planar feature support) ---
            elif seed_data['surface_type'] == 'Plane':
                if neighbour_data['surface_type'] == 'Plane':
                    if planes_are_parallel(
                        seed_data,
                        neighbour_data,
                        tol=config.plane_parallel_tol
                    ):
                        cluster.add(neighbour_idx)
                        queue.append(neighbour_idx)
                        added = True

            # --- Rule 4: Torus bridge (fillet / blend) — applies for any seed type ---
            if not added and neighbour_data['surface_type'] == 'Torus':
                if torus_is_bridge(neighbour_idx, cluster, G):
                    cluster.add(neighbour_idx)
                    queue.append(neighbour_idx)

    if seed_data['surface_type'] == 'Cylinder':
        for node, data in G.nodes(data=True):
            if node in cluster:
                continue
            if data['surface_type'] != 'Cylinder':
                continue
            if axes_are_collinear(
                seed_data['cylinder'],
                data['cylinder'],
                direction_tol=config.axis_direction_tol,
                distance_tol=config.axis_collinear_distance_tol
            ):
                cluster.add(node)
                queue_extra = [node]
                while queue_extra:
                    extra_idx = queue_extra.pop(0)
                    for nb_idx in G.neighbors(extra_idx):
                        if nb_idx in cluster:
                            continue
                        nb_data = G.nodes[nb_idx]
                        if nb_data['surface_type'] == 'Plane':
                            if plane_closes_cylinder(
                                nb_data,
                                seed_data,
                                direction_tol=config.plane_axis_direction_tol,
                                position_tol=config.plane_position_tol
                            ):
                                cluster.add(nb_idx)
                                queue_extra.append(nb_idx)
    return sorted(cluster)


# ---------------------------------------------------------------------------
# Phase 4 — Overlap resolution and face assignment
# ---------------------------------------------------------------------------

def resolve_overlaps(seed_clusters: List[Dict], all_face_indices: List[int],
                     G: nx.Graph) -> List[Dict]:
    """
    Ensure every face belongs to exactly one cluster.

    Strategy:
    - If a face is claimed by exactly one cluster      -> assign to that cluster
    - If a face is claimed by multiple clusters        -> assign to the cluster
      whose seed face is geometrically closest
      (measured as BFS hop distance in the graph)
    - If a face is claimed by no cluster               -> create a 'background'
      cluster to hold stock faces, datum surfaces, unmachined faces

    Returns the resolved cluster list with face_indices updated.
    """
    # Build claim map: face_index -> list of cluster_ids that want it
    claim_map: Dict[int, List[int]] = {idx: [] for idx in all_face_indices}

    for cluster_id, cluster in enumerate(seed_clusters):
        for face_idx in cluster['face_indices']:
            claim_map[face_idx].append(cluster_id)

    # Resolve multi-claimed faces using BFS hop distance to each seed
    for face_idx, claimants in claim_map.items():
        if len(claimants) <= 1:
            continue

        # Find closest seed by BFS path length
        best_cluster_id = None
        best_distance   = float('inf')

        for cluster_id in claimants:
            seed_idx = seed_clusters[cluster_id]['seed_face_index']
            try:
                dist = nx.shortest_path_length(G, source=face_idx, target=seed_idx)
            except nx.NetworkXNoPath:
                dist = float('inf')

            if dist < best_distance:
                best_distance   = dist
                best_cluster_id = cluster_id

        # Remove this face from all other clusters
        for cluster_id in claimants:
            if cluster_id != best_cluster_id:
                if face_idx in seed_clusters[cluster_id]['face_indices']:
                    seed_clusters[cluster_id]['face_indices'].remove(face_idx)

    # Collect unclaimed faces into a background cluster
    all_claimed = set()
    for cluster in seed_clusters:
        all_claimed.update(cluster['face_indices'])

    unclaimed = [idx for idx in all_face_indices if idx not in all_claimed]

    if unclaimed:
        seed_clusters.append({
            'cluster_id'       : len(seed_clusters),
            'seed_face_index'  : None,
            'seed_type'        : 'background',
            'face_indices'     : sorted(unclaimed),
            'feature_axis'     : None,
            'depth'            : None,
            'radii'            : [],
            'is_principal_axis': None,
            'face_count'       : len(unclaimed),
        })

    return seed_clusters


# ---------------------------------------------------------------------------
# Post-clustering enrichment: plane cluster analysis and slot detection
# ---------------------------------------------------------------------------

def _analyse_plane_cluster(cluster, G):
    """
    Inspect the neighbours of a plane cluster to detect pocket walls and
    internal corner radii.

    A pocket floor has adjacent Plane faces whose normals are perpendicular
    to the floor normal (the walls).  Small Cylinder faces at wall-floor
    junctions give the internal corner radius.

    Returns a dict with:
        has_perpendicular_walls : bool
        perp_wall_count         : int   -- number of adjacent perp plane faces
        internal_corner_radius  : float or None
        face_area               : float -- total area of cluster faces (mm^2)
    """
    defaults = {
        'has_perpendicular_walls': False,
        'perp_wall_count'        : 0,
        'internal_corner_radius' : None,
        'face_area'              : None,
    }

    seed_idx = cluster.get('seed_face_index')
    if seed_idx is None or seed_idx not in G.nodes:
        return defaults

    plane_info   = G.nodes[seed_idx].get('plane', {})
    plane_normal_raw = plane_info.get('normal')
    if not plane_normal_raw:
        return defaults

    # plane_normal may be a dict {"x":..,"y":..,"z":..} or a list -- normalise
    plane_normal = _vec(plane_normal_raw)
    pn = _norm(plane_normal)
    if pn < 1e-10:
        return defaults

    face_set   = set(cluster.get('face_indices', []))
    total_area = sum(G.nodes[i].get('area', 0.0) for i in face_set
                     if i in G.nodes)

    perp_count   = 0
    corner_radii = []
    perp_seen    = set()   # face indices already counted as walls

    for face_idx in face_set:
        if face_idx not in G.nodes:
            continue
        for nb_idx in G.neighbors(face_idx):
            if nb_idx in face_set or nb_idx in perp_seen:
                continue
            nb_data = G.nodes[nb_idx]
            nb_type = nb_data.get('surface_type')

            if nb_type == 'Plane':
                nb_normal_raw = nb_data.get('plane', {}).get('normal')
                if nb_normal_raw:
                    nb_normal = _vec(nb_normal_raw)
                    nn = _norm(nb_normal)
                    if nn > 1e-10:
                        cos_a = abs(_dot(plane_normal, nb_normal)) / (pn * nn)
                        # cos(80 deg) ~0.17 -- accept faces within 10 deg of perp
                        if cos_a < 0.17:
                            perp_seen.add(nb_idx)
                            perp_count += 1

            elif nb_type == 'Cylinder':
                cyl = nb_data.get('cylinder', {})
                r   = cyl.get('radius')
                if r is not None and r > 0:
                    corner_radii.append(r)

    return {
        'has_perpendicular_walls': perp_count >= 1,
        'perp_wall_count'        : perp_count,
        'internal_corner_radius' : min(corner_radii) if corner_radii else None,
        'face_area'              : total_area,
    }


def detect_slots(clusters, config):
    """
    Find pairs of bore clusters that together form a slot (keyway, channel, etc.).

    A slot consists of two semi-cylindrical end-faces with:
        - Equal radii    (within 5%)
        - Parallel axes  (find_seeds deduplicates collinear bore axes, so two
                          separate bore clusters with parallel axes are guaranteed
                          non-collinear -- i.e. truly offset = a slot pair)
        - Equal depth    (within 0.5 mm)

    Matching pairs are merged into a new cluster with seed_type='slot'.
    internal_corner_radius is set to the arc radius (half the slot width).
    The two consumed bore clusters are removed from the list.

    Returns the updated clusters list.
    """
    RADIUS_TOL_FRAC = 0.05   # 5 percent
    DEPTH_TOL_MM    = 0.5    # mm
    DIRECTION_TOL   = 1e-3   # for _axes_parallel

    bore_ids = [
        c['cluster_id']
        for c in clusters
        if c.get('seed_type') == 'bore'
           and len(c.get('radii', [])) == 1
           and c.get('depth') is not None
           and c.get('feature_axis') is not None
    ]

    used              = set()
    new_slot_clusters = []
    clusters_by_id    = {c['cluster_id']: c for c in clusters}

    for i, id_a in enumerate(bore_ids):
        if id_a in used:
            continue
        ca      = clusters_by_id[id_a]
        ra      = ca['radii'][0]
        axis_a  = ca['feature_axis']
        depth_a = ca['depth']
        if ra <= 0:
            continue

        for id_b in bore_ids[i + 1:]:
            if id_b in used:
                continue
            cb      = clusters_by_id[id_b]
            rb      = cb['radii'][0]
            axis_b  = cb['feature_axis']
            depth_b = cb['depth']
            if rb <= 0:
                continue

            # Radius match
            if abs(ra - rb) / max(ra, rb) > RADIUS_TOL_FRAC:
                continue

            # Axis parallel
            if not _axes_parallel(axis_a, axis_b, tol=DIRECTION_TOL):
                continue

            # Depth match
            if abs(depth_a - depth_b) > DEPTH_TOL_MM:
                continue

            # Build merged slot cluster
            merged_faces = sorted(set(ca['face_indices']) | set(cb['face_indices']))
            new_slot_clusters.append({
                'cluster_id'             : -1,         # renumbered later
                'seed_face_index'        : ca['seed_face_index'],
                'seed_type'              : 'slot',
                'face_indices'           : merged_faces,
                'face_count'             : len(merged_faces),
                'feature_axis'           : axis_a,
                'depth'                  : depth_a,
                'radii'                  : [ra],
                'is_principal_axis'      : ca.get('is_principal_axis'),
                'internal_corner_radius' : round(ra, 4),
                'has_perpendicular_walls': False,
                'perp_wall_count'        : 0,
                'face_area'              : None,
            })
            used.add(id_a)
            used.add(id_b)
            break   # id_a consumed; move to next candidate

    updated = [c for c in clusters if c['cluster_id'] not in used]
    updated.extend(new_slot_clusters)
    return updated


# ---------------------------------------------------------------------------
# Main clustering function
# ---------------------------------------------------------------------------

def cluster_features(json_data: Dict,
                     config: Optional[ClusteringConfig] = None,
                     verbose: bool = False) -> List[Dict]:
    """
    Full clustering pipeline. Takes raw PythonOCC JSON, returns a list of
    feature cluster dicts ready for the classification step.

    Parameters
    ----------
    json_data : dict
        Parsed PythonOCC JSON output.
    config    : ClusteringConfig, optional
        Tolerance settings. Uses defaults if not provided.
    verbose   : bool
        If True, prints graph summary and clustering progress.

    Returns
    -------
    clusters : list of dicts, each with:
        {
          'cluster_id'             : int,
          'seed_face_index'        : int or None,
          'seed_type'              : 'bore' | 'boss' | 'plane' | 'slot' | 'background',
          'face_indices'           : [int, ...],
          'face_count'             : int,
          'feature_axis'           : [dx, dy, dz] or None,
          'depth'                  : float or None,
          'radii'                  : [float, ...],        # sorted ascending
          'is_principal_axis'      : bool or None,
          'has_perpendicular_walls': bool,               # plane clusters only
          'perp_wall_count'        : int,                # plane clusters only
          'internal_corner_radius' : float or None,      # slot and pocket clusters
          'face_area'              : float or None,      # plane clusters only
        }
    """
    if config is None:
        config = ClusteringConfig()

    faces = json_data['faces']['faces']
    all_face_indices = list(range(len(faces)))

    # --- Phase 1: Build graph ---
    G = build_face_graph(json_data)
    if verbose:
        graph_summary(G)

    # --- Phase 2: Find seeds ---
    seeds = find_seeds(G, config)
    if verbose:
        print(f"\nFound {len(seeds)} seeds:")
        for s in seeds:
            print(f"  Face {s['seed_face_index']:3d} -> {s['seed_type']}")

    # --- Phase 3: Grow clusters ---
    raw_clusters = []
    for cluster_id, seed in enumerate(seeds):
        face_indices = grow_cluster(G, seed, config)
        cluster_faces = [faces[i] for i in face_indices]

        axis = get_feature_axis(cluster_faces)
        raw_clusters.append({
            'cluster_id'        : cluster_id,
            'seed_face_index'   : seed['seed_face_index'],
            'seed_type'         : seed['seed_type'],
            'face_indices'      : face_indices,
            'face_count'        : len(face_indices),
            'feature_axis'      : axis,
            'depth'             : get_feature_depth(cluster_faces),
            'radii'             : get_radii_sorted(cluster_faces),
            'is_principal_axis' : is_principal_axis(axis) if axis else None,
        })

    if verbose:
        print(f"\nRaw clusters before overlap resolution: {len(raw_clusters)}")
        for c in raw_clusters:
            print(f"  Cluster {c['cluster_id']:3d} | seed={c['seed_face_index']} "
                  f"| type={c['seed_type']:10s} | faces={c['face_indices']}")

    # --- Phase 4: Resolve overlaps ---
    clusters = resolve_overlaps(raw_clusters, all_face_indices, G)

    # --- Phase 5: Detect slots (pairs of bore clusters that form a slot) ---
    clusters = detect_slots(clusters, config)

    # --- Phase 6: Enrich plane clusters with pocket-detection fields ---
    for c in clusters:
        st = c.get('seed_type')
        if st in ('plane', 'plane_feature'):
            c.update(_analyse_plane_cluster(c, G))
        elif st == 'slot':
            # internal_corner_radius already set by detect_slots
            c.setdefault('has_perpendicular_walls', False)
            c.setdefault('perp_wall_count', 0)
            c.setdefault('face_area', None)
        else:
            c['has_perpendicular_walls'] = False
            c['perp_wall_count']         = 0
            c.setdefault('internal_corner_radius', None)
            c['face_area']               = None

    # Renumber cluster_ids cleanly after all modifications
    for i, c in enumerate(clusters):
        c['cluster_id'] = i

    if verbose:
        print(f"\nFinal clusters after slot detection and enrichment: {len(clusters)}")
        for c in clusters:
            icr = c.get('internal_corner_radius')
            icr_s = f" icr={icr}" if icr is not None else ""
            print(f"  Cluster {c['cluster_id']:3d} | seed={c['seed_face_index']} "
                  f"| type={c['seed_type']:10s} | faces={c['face_indices']} "
                  f"| radii={c['radii']} | depth={c['depth']} "
                  f"| principal={c['is_principal_axis']}{icr_s}")

    return clusters


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def save_clusters(clusters: List[Dict], output_path: str):
    """Save cluster results to a JSON file."""
    with open(output_path, 'w') as f:
        json.dump({'clusters': clusters}, f, indent=2)
    print(f"Clusters saved to: {output_path}")


# ---------------------------------------------------------------------------
# Entry point for standalone use
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python cluster_features.py <path_to_features_json>")
        print("       python cluster_features.py <path_to_features_json> <output_path>")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else input_path.replace('.json', '_clustered.json')

    with open(input_path) as f:
        data = json.load(f)

    clusters = cluster_features(data, verbose=True)
    save_clusters(clusters, output_path)
