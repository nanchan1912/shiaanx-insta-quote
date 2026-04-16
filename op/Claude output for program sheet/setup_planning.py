"""
setup_planning.py
-----------------
Setup planning step. Takes the process-selected JSON output from
process_selection.py and groups features into machine setups.

A "setup" is one physical configuration of the part on the machine table.
Every time the part is re-clamped at a different angle, that is a new setup.
Minimising setups reduces changeover time and accumulated positioning error.

This answers the question:
  "How many times does the part need to be repositioned, and which features
   get machined in each position?"

Approach
--------
Principal axis grouping heuristic (practical VMC approach):

  Step 1 — Group all features by their feature_axis direction.
            Features whose axes are parallel or anti-parallel (pointing the
            same line in space) share the same setup because the spindle
            only needs to point in one direction to reach all of them.

  Step 2 — For each group aligned to a principal axis (±X, ±Y, ±Z), create
            one setup. Label it by the machine direction (the direction the
            spindle points to machine those features — opposite to the hole axis).

  Step 3 — For each group with a non-principal axis (angled features),
            create a separate angled setup. Calculate the rotation angle
            from the default VMC orientation and record it so the operator
            knows exactly how to reposition the part.

  Step 4 — Assign remaining uncategorised features (planar faces, etc.)
            to the most appropriate existing setup based on adjacency.

  Step 5 — Order setups by feature count descending (do the most work
            first when the part is freshly clamped and most rigid).

Why not OR-Tools or graph colouring?
  Both require full 3D accessibility analysis (ray casting) to model
  which features genuinely conflict. That data is not available from the
  JSON alone. The heuristic produces correct results for the vast majority
  of VMC parts (which are designed to be machinable in 1–3 setups) and
  its output contract is designed so OR-Tools can replace the grouping
  logic later without changing anything downstream.

VMC Coordinate Convention Used Here
-------------------------------------
The default VMC spindle points in the -Y direction (tool comes from above,
Y is the vertical axis). This matches the bounding box data seen on the
Hub part (ymin=-1, ymax=11.8 — Y is the height axis).

Setup directions are expressed as the SPINDLE direction (where the tool
comes from), which is OPPOSITE to the feature axis (the direction the hole
points). For example:
  feature_axis = [0, -1, 0]  → hole points downward
  spindle_direction = [0, 1, 0]  → spindle approaches from above ✓

Usage
-----
    python setup_planning.py <processes_json> [output_json]

    python setup_planning.py Hub_processes.json Hub_setups.json

Or from Python:
    from setup_planning import plan_setups
    result = plan_setups(processes_data)

Output structure
----------------
The output JSON has a top-level 'setups' list alongside the original
'clusters'. Each setup dict:

{
  "setup_id"          : int,     # 1-based
  "setup_type"        : str,     # 'principal' or 'angled'
  "spindle_direction" : [x,y,z], # direction tool approaches from
  "feature_axis"      : [x,y,z], # direction features point (opposite)
  "axis_label"        : str,     # human-readable e.g. '+Y approach (top)'
  "description"       : str,     # plain English setup instruction
  "fixture_note"      : str,     # suggested workholding
  "rotation_from_default" : {    # only for angled setups
      "angle_deg"   : float,     # degrees to rotate from standard position
      "rotation_axis" : [x,y,z], # axis to rotate around
      "rotation_axis_label" : str
  } or None,
  "cluster_ids"       : [int],   # which clusters machine in this setup
  "operation_count"   : int,     # total machining steps in this setup
  "machining_sequence": [        # ordered list of what to do in this setup
      {
          "cluster_id" : int,
          "feature_type": str,
          "operations" : [str]   # operation names in order
      }
  ]
}
"""

import json
import sys
import copy
import math
import numpy as np
from typing import Dict, List, Optional, Tuple

try:
    from coord_system import CoordSystem, apply_coord_system
    _COORD_SYSTEM_AVAILABLE = True
except ImportError:
    _COORD_SYSTEM_AVAILABLE = False


# ---------------------------------------------------------------------------
# VMC convention
# ---------------------------------------------------------------------------

# The default VMC spindle direction — tool approaches from above.
# Y is the vertical axis on the Hub part (ymin=-1, ymax=11.8).
# Spindle points in -Y direction (downward toward part).
VMC_DEFAULT_SPINDLE = np.array([0.0, -1.0, 0.0])

# Tolerance for deciding two axes are parallel/anti-parallel
AXIS_PARALLEL_TOL = 1e-3


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------

def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-10 else v


def _are_same_direction(v1: np.ndarray, v2: np.ndarray,
                        tol: float = AXIS_PARALLEL_TOL) -> bool:
    """
    True only if vectors point in the SAME direction (not anti-parallel).

    Anti-parallel axes (e.g. [0,1,0] and [0,-1,0]) are NOT the same setup:
    a downward-pointing hole needs the spindle from above, an upward-pointing
    hole needs the part flipped. They must be separate setups.
    """
    u1 = _unit(v1)
    u2 = _unit(v2)
    return (np.dot(u1, u2) > 1.0 - tol and
            np.linalg.norm(np.cross(u1, u2)) < tol)


def _is_principal(v: np.ndarray, tol: float = AXIS_PARALLEL_TOL) -> bool:
    """True if vector aligns with ±X, ±Y, or ±Z."""
    u = _unit(v)
    for ax in [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]]:
        if np.linalg.norm(u - np.array(ax, dtype=float)) < tol:
            return True
    return False


def _axis_label(spindle_dir: np.ndarray) -> str:
    """Return a human-readable label for a spindle direction."""
    u = _unit(spindle_dir)
    labels = {
        ( 0,  1,  0): '+Y approach (top — default VMC position)',
        ( 0, -1,  0): '-Y approach (bottom — part flipped upside down)',
        ( 1,  0,  0): '+X approach (right side face)',
        (-1,  0,  0): '-X approach (left side face)',
        ( 0,  0,  1): '+Z approach (front face)',
        ( 0,  0, -1): '-Z approach (rear face)',
    }
    for vec, label in labels.items():
        if np.linalg.norm(u - np.array(vec, dtype=float)) < AXIS_PARALLEL_TOL:
            return label
    return f'angled approach {[round(float(x),3) for x in u]}'


def _rotation_from_default(feature_axis: np.ndarray) -> Dict:
    """
    Calculate the rotation needed to bring a feature axis into alignment
    with the VMC spindle, starting from the default part orientation.

    The VMC spindle points in the -Y direction. A feature whose axis is
    [0,-1,0] needs no rotation. Any other axis requires the part to be
    tilted so that axis aligns with -Y.

    Returns a dict with angle_deg, rotation_axis, and a readable label.
    """
    fa = _unit(feature_axis)

    # The spindle direction we need the feature axis to align with is -Y
    # (tool comes from +Y, enters hole which points in -Y direction)
    # But feature_axis points the direction the hole faces — so we want
    # the feature_axis to point toward -Y (i.e., align with [0,-1,0]).
    target = np.array([0.0, -1.0, 0.0])

    # Angle between feature axis and target
    dot   = float(np.clip(np.dot(fa, target), -1.0, 1.0))
    angle = math.degrees(math.acos(dot))

    # Rotation axis = cross product of feature_axis and target
    cross = np.cross(fa, target)
    cross_norm = np.linalg.norm(cross)

    if cross_norm < 1e-6:
        # Already aligned or exactly opposite — no meaningful rotation axis
        rot_axis       = np.array([1.0, 0.0, 0.0])  # arbitrary
        rot_axis_label = 'no rotation needed' if angle < 1 else '180° flip'
    else:
        rot_axis = cross / cross_norm
        # Name the rotation axis
        if   abs(rot_axis[0]) > 0.9:
            rot_axis_label = ('+X axis (A-axis tilt)'
                              if rot_axis[0] > 0 else '-X axis (A-axis tilt)')
        elif abs(rot_axis[1]) > 0.9:
            rot_axis_label = ('+Y axis (C-axis rotation)'
                              if rot_axis[1] > 0 else '-Y axis (C-axis rotation)')
        elif abs(rot_axis[2]) > 0.9:
            rot_axis_label = ('+Z axis (B-axis tilt)'
                              if rot_axis[2] > 0 else '-Z axis (B-axis tilt)')
        else:
            rot_axis_label = (f'compound axis [{round(float(rot_axis[0]),3)}, '
                              f'{round(float(rot_axis[1]),3)}, '
                              f'{round(float(rot_axis[2]),3)}] '
                              f'— use sine plate or 5-axis fixture')

    return {
        'angle_deg'          : round(angle, 2),
        'rotation_axis'      : [round(float(x), 4) for x in rot_axis],
        'rotation_axis_label': rot_axis_label,
    }


# ---------------------------------------------------------------------------
# Setup description builder
# ---------------------------------------------------------------------------

def _build_description(setup_type: str, axis_label: str,
                        rotation: Optional[Dict]) -> Tuple[str, str]:
    """
    Return (description, fixture_note) strings for a setup.
    """
    if setup_type == 'principal':
        if 'top' in axis_label or '+Y' in axis_label:
            desc    = ('Standard top-face setup. Clamp part with top face '
                       'accessible to spindle. Machine all features in this '
                       'setup before repositioning.')
            fixture = 'Standard vise or fixture plate, jaws on side faces.'

        elif 'bottom' in axis_label or '-Y' in axis_label:
            desc    = ('Flipped setup — part inverted so bottom face is '
                       'accessible to spindle. Clamp on previously machined '
                       'top features or use step-jaw vise.')
            fixture = 'Step-jaw vise or fixture plate with bosses as datums.'

        elif 'right' in axis_label or '+X' in axis_label:
            desc    = ('Right-side face setup. Rotate part 90° so right face '
                       'is accessible. Clamp on bottom and left faces.')
            fixture = '90° angle plate or tombstone fixture.'

        elif 'left' in axis_label or '-X' in axis_label:
            desc    = ('Left-side face setup. Rotate part 90° so left face '
                       'is accessible. Clamp on bottom and right faces.')
            fixture = '90° angle plate or tombstone fixture.'

        elif 'front' in axis_label or '+Z' in axis_label:
            desc    = ('Front-face setup. Rotate part 90° so front face '
                       'is accessible to spindle.')
            fixture = '90° angle plate or tombstone fixture.'

        elif 'rear' in axis_label or '-Z' in axis_label:
            desc    = ('Rear-face setup. Rotate part 90° so rear face '
                       'is accessible to spindle.')
            fixture = '90° angle plate or tombstone fixture.'

        else:
            desc    = f'Setup for {axis_label}.'
            fixture = 'Standard vise or fixture plate.'

    else:  # angled
        angle = rotation['angle_deg'] if rotation else '?'
        ax    = rotation['rotation_axis_label'] if rotation else '?'
        desc  = (f'Angled setup. Tilt part {angle}° around {ax} from '
                 f'the default orientation. Verify with dial indicator before '
                 f'machining. All features in this setup share the same '
                 f'angled axis and can be machined without re-clamping.')
        fixture = (f'Sine plate or angled fixture set to {angle}°, '
                   f'or 4th/5th axis rotary table if available.')

    return desc, fixture


# ---------------------------------------------------------------------------
# Main planning logic
# ---------------------------------------------------------------------------

def plan_setups(processes_data: Dict,
               coord_sys: 'CoordSystem' = None) -> Dict:
    """
    Group features into machine setups.

    Parameters
    ----------
    processes_data : dict
        Parsed JSON from process_selection.py. Must have 'clusters' key.
    coord_sys : CoordSystem or None
        If provided, embed the coordinate system into the output and
        annotate every setup and cluster with machine-space axes.
        If None, CAD axes are used as-is (only correct if CAD axes
        already match machine axes).

    Returns
    -------
    dict
        Original data with 'setups' list added at top level.
        Each cluster also gains a 'setup_id' field.
        If coord_sys provided, also gains 'coord_system' and
        machine-space axis fields on every cluster and setup.
    """
    result   = copy.deepcopy(processes_data)
    clusters = result['clusters']

    # ------------------------------------------------------------------
    # Step 1: Collect clusters that need machining
    # ------------------------------------------------------------------
    machinable = [
        c for c in clusters
        if c.get('feature_type') not in ('background',)
        and c.get('machine_type') not in ('none',)
        and c.get('feature_axis') is not None
    ]

    # Clusters with no axis (planar_face) handled separately
    no_axis = [
        c for c in clusters
        if c.get('feature_type') not in ('background',)
        and c.get('machine_type') not in ('none',)
        and c.get('feature_axis') is None
    ]

    # ------------------------------------------------------------------
    # Step 2: Group machinable clusters by axis direction
    # ------------------------------------------------------------------
    # Each group = list of clusters whose axes point in the SAME direction
    axis_groups = []   # list of {'rep_axis': ndarray, 'clusters': [...]}

    for c in machinable:
        ax = np.array(c['feature_axis'], dtype=float)
        ax_unit = _unit(ax)

        # Find an existing group pointing in exactly the same direction
        placed = False
        for group in axis_groups:
            if _are_same_direction(ax_unit, group['rep_axis']):
                group['clusters'].append(c)
                placed = True
                break

        if not placed:
            axis_groups.append({'rep_axis': ax_unit, 'clusters': [c]})

    # ------------------------------------------------------------------
    # Step 3: Build a Setup dict for each axis group
    # ------------------------------------------------------------------
    setups = []
    setup_id = 1

    # Sort: principal axis groups first (simpler setups), angled last
    axis_groups.sort(key=lambda g: (0 if _is_principal(g['rep_axis']) else 1,
                                    -len(g['clusters'])))

    for group in axis_groups:
        rep_ax      = group['rep_axis']
        group_clust = group['clusters']
        is_principal = _is_principal(rep_ax)

        # Spindle direction is OPPOSITE to feature axis.
        # Each direction group now contains only same-direction axes
        # (anti-parallel axes are separate groups = separate setups).
        spindle_dir = _unit(-rep_ax)

        # For angled setups, calculate rotation info
        if is_principal:
            rotation    = None
            setup_type  = 'principal'
        else:
            # Feature axis = the direction the holes point.
            # Use rep_ax (already normalised).
            rotation   = _rotation_from_default(rep_ax)
            setup_type = 'angled'

        ax_label            = _axis_label(spindle_dir)
        description, fixture = _build_description(setup_type, ax_label, rotation)

        # Count total operations in this setup
        op_count = sum(
            len(c.get('process_sequence', []))
            for c in group_clust
        )

        # Build machining sequence (ordered: simpler features first,
        # then complex; within same complexity, by cluster_id)
        def _seq_complexity(c):
            return len(c.get('process_sequence', []))

        ordered = sorted(group_clust, key=_seq_complexity)
        machining_sequence = [
            {
                'cluster_id'  : c['cluster_id'],
                'feature_type': c['feature_type'],
                'operations'  : [s['operation']
                                 for s in c.get('process_sequence', [])],
            }
            for c in ordered
        ]

        # ------------------------------------------------------------------
        # WCS assignment (§2a)
        # ------------------------------------------------------------------
        wcs_sequence = ["G54", "G55", "G56", "G57", "G58", "G59"]
        wcs = wcs_sequence[min(setup_id - 1, len(wcs_sequence) - 1)]

        # Origin X/Y based on spindle direction; Z is always TOP
        sd = spindle_dir  # already a unit vector
        if abs(sd[1]) > 0.9:          # ±Y (top-down or bottom-up)
            origin_x = "CENTER"
            origin_y = "CENTER"
        elif sd[0] > 0.9:             # +X (right side)
            origin_x = "+"
            origin_y = "CENTER"
        elif sd[0] < -0.9:            # -X (left side)
            origin_x = "-"
            origin_y = "CENTER"
        elif abs(sd[2]) > 0.9:        # ±Z (front/rear)
            origin_x = "CENTER"
            origin_y = "CENTER"
        else:
            origin_x = "CENTER"
            origin_y = "CENTER"
        origin_z = "TOP"

        # ------------------------------------------------------------------
        # Bounding box dimensions (§2b)
        # ------------------------------------------------------------------
        bbox = processes_data.get('bounding_box', {})
        if bbox:
            xmin = bbox.get('xmin', None)
            xmax = bbox.get('xmax', None)
            ymin = bbox.get('ymin', None)
            ymax = bbox.get('ymax', None)
            zmin = bbox.get('zmin', None)
            zmax = bbox.get('zmax', None)

            def _dim(a, b):
                return round(float(b - a), 4) if (a is not None and b is not None) else None

            if abs(sd[1]) > 0.9:          # ±Y spindle (top-down / bottom-up)
                face_width  = _dim(xmin, xmax)
                face_height = _dim(zmin, zmax)
                depth       = _dim(ymin, ymax)
            elif abs(sd[0]) > 0.9:        # ±X spindle (left/right side)
                face_width  = _dim(ymin, ymax)
                face_height = _dim(zmin, zmax)
                depth       = _dim(xmin, xmax)
            elif abs(sd[2]) > 0.9:        # ±Z spindle (front/rear)
                face_width  = _dim(xmin, xmax)
                face_height = _dim(ymin, ymax)
                depth       = _dim(zmin, zmax)
            else:
                face_width  = None
                face_height = None
                depth       = None
        else:
            # Fallback: derive setup depth from deepest cluster in this group.
            # Face dimensions require the full STEP bounding box (not available
            # from cluster data alone). Compute what we can.
            max_depth = 0.0
            max_radius = 0.0
            for c in group_clust:
                cd = c.get('depth')
                if cd is not None:
                    max_depth = max(max_depth, abs(float(cd)))
                # Track largest feature radius for face dimension estimate
                for r in (c.get('radii') or []):
                    if isinstance(r, (int, float)):
                        max_radius = max(max_radius, float(r))
                # Also check face_area for planar faces
                fa = c.get('face_area')
                if fa and isinstance(fa, (int, float)):
                    # Rough estimate: assume square face → side = sqrt(area)
                    side = math.sqrt(float(fa))
                    max_radius = max(max_radius, side / 2)

            depth       = round(max_depth, 4) if max_depth > 0 else None
            face_width  = None  # Cannot reliably derive without STEP bbox
            face_height = None
            # NOTE: for accurate face dimensions, ensure feature_extraction.py
            # emits a top-level 'bounding_box' dict with xmin/xmax/ymin/ymax/zmin/zmax

        setup = {
            'setup_id'              : setup_id,
            'setup_type'            : setup_type,
            'spindle_direction'     : [round(float(x), 4) for x in spindle_dir],
            'feature_axis'          : [round(float(x), 4) for x in rep_ax],
            'axis_label'            : ax_label,
            'description'           : description,
            'fixture_note'          : fixture,
            'rotation_from_default' : rotation,
            'wcs'                   : wcs,
            'origin_x'              : origin_x,
            'origin_y'              : origin_y,
            'origin_z'              : origin_z,
            'setup_face_width'      : face_width,
            'setup_face_height'     : face_height,
            'setup_depth'           : depth,
            'cluster_ids'           : sorted(c['cluster_id'] for c in group_clust),
            'operation_count'       : op_count,
            'machining_sequence'    : machining_sequence,
        }
        setups.append(setup)
        setup_id += 1

    # ------------------------------------------------------------------
    # Step 4: Handle no-axis clusters (planar_face)
    # Assign to the setup with the most operations (likely the primary setup)
    # ------------------------------------------------------------------
    if no_axis:
        if setups:
            primary_setup = max(setups, key=lambda s: s['operation_count'])
            for c in no_axis:
                primary_setup['cluster_ids'].append(c['cluster_id'])
                primary_setup['operation_count'] += len(c.get('process_sequence', []))
                primary_setup['machining_sequence'].append({
                    'cluster_id'  : c['cluster_id'],
                    'feature_type': c['feature_type'],
                    'operations'  : [s['operation']
                                     for s in c.get('process_sequence', [])],
                })
        else:
            # Edge case: only planar faces, no cylindrical features
            setups.append({
                'setup_id'              : setup_id,
                'setup_type'            : 'principal',
                'spindle_direction'     : [0.0, -1.0, 0.0],
                'feature_axis'          : None,
                'axis_label'            : '+Y approach (top — default VMC position)',
                'description'           : 'Face milling setup — top face.',
                'fixture_note'          : 'Standard vise or fixture plate.',
                'rotation_from_default' : None,
                'cluster_ids'           : [c['cluster_id'] for c in no_axis],
                'operation_count'       : sum(len(c.get('process_sequence', []))
                                              for c in no_axis),
                'machining_sequence'    : [
                    {
                        'cluster_id'  : c['cluster_id'],
                        'feature_type': c['feature_type'],
                        'operations'  : [s['operation']
                                         for s in c.get('process_sequence', [])],
                    }
                    for c in no_axis
                ],
            })

    # ------------------------------------------------------------------
    # Step 5: Write setup_id back onto each cluster for traceability
    # ------------------------------------------------------------------
    cluster_to_setup = {}
    for s in setups:
        for cid in s['cluster_ids']:
            cluster_to_setup[cid] = s['setup_id']

    for c in clusters:
        c['setup_id'] = cluster_to_setup.get(c['cluster_id'], None)

    result['setups'] = setups
    result['setup_count'] = len(setups)

    # Apply coordinate system transformation if provided
    if coord_sys is not None and _COORD_SYSTEM_AVAILABLE:
        result = apply_coord_system(result, coord_sys)

    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_setup_summary(data: Dict):
    """Print a human-readable summary of setup planning results."""
    setups   = data.get('setups', [])
    clusters = {c['cluster_id']: c for c in data['clusters']}

    print(f"Setup planning summary — {len(setups)} setup(s) required\n")

    for s in setups:
        sid   = s['setup_id']
        stype = s['setup_type'].upper()
        label = s['axis_label']
        ops   = s['operation_count']
        cids  = s['cluster_ids']

        print(f"  Setup {sid} [{stype}] — {label}")
        print(f"    Clusters  : {cids}")
        print(f"    Operations: {ops} total")
        print(f"    Fixture   : {s['fixture_note']}")

        rot = s.get('rotation_from_default')
        if rot:
            print(f"    Rotation  : {rot['angle_deg']}° around {rot['rotation_axis_label']}")

        print(f"    WCS       : {s.get('wcs', 'N/A')}  "
              f"Origin X={s.get('origin_x', '?')}  "
              f"Y={s.get('origin_y', '?')}  "
              f"Z={s.get('origin_z', '?')}")

        fw = s.get('setup_face_width')
        fh = s.get('setup_face_height')
        sd = s.get('setup_depth')
        if fw is not None or fh is not None or sd is not None:
            print(f"    BBox      : face_width={fw} mm  "
                  f"face_height={fh} mm  "
                  f"depth={sd} mm")
        else:
            print(f"    BBox      : not available")

        print(f"    Sequence  :")
        for step in s['machining_sequence']:
            ops_str = ' -> '.join(step['operations'])
            print(f"      C{step['cluster_id']:2d} ({step['feature_type']:22s}): {ops_str}")
        print()


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def save_setups(data: Dict, output_path: str):
    """Save setup planning results to a JSON file."""
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Setup plan saved to: {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python setup_planning.py <processes_json> [output_json] "
              "[--features <features_json>] [--up +Y|+Z|+X] "
              "[--zero top_face_centre|bottom_face_centre|origin]")
        sys.exit(1)

    input_path    = sys.argv[1]
    output_path   = None
    features_path = None
    up_axis       = '+Y'
    zero_conv     = 'top_face_centre'

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '--features' and i + 1 < len(sys.argv):
            features_path = sys.argv[i + 1]; i += 2
        elif arg == '--up' and i + 1 < len(sys.argv):
            up_axis = sys.argv[i + 1]; i += 2
        elif arg == '--zero' and i + 1 < len(sys.argv):
            zero_conv = sys.argv[i + 1]; i += 2
        elif not arg.startswith('--') and output_path is None:
            output_path = arg; i += 1
        else:
            i += 1

    if output_path is None:
        output_path = input_path.replace('.json', '_setups.json')

    with open(input_path) as f:
        data = json.load(f)

    # Build coord system if features JSON provided
    coord_sys = None
    if features_path and _COORD_SYSTEM_AVAILABLE:
        with open(features_path) as f:
            features_data = json.load(f)
        coord_sys = CoordSystem.from_features(
            features_data,
            cad_up_axis = up_axis,
            work_zero   = zero_conv,
        )
        from coord_system import print_coord_summary
        print_coord_summary(coord_sys)
    elif not _COORD_SYSTEM_AVAILABLE:
        print("Warning: coord_system.py not found — running without coordinate transform.")
    else:
        print("Note: no --features file provided. CAD axes used as-is.")
        print("      Pass --features <features_json> to apply coordinate transform.")
        print()

    result = plan_setups(data, coord_sys=coord_sys)
    print_setup_summary(result)
    save_setups(result, output_path)
