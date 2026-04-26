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
import os
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

# WCS corner-origin heuristic (AD-006): min is treated as CAD origin if
# |min| < frac * (max - min). Default is 2%.
WCS_CORNER_ORIGIN_FRAC = 0.02

# WCS register assignment (setup_id 1 -> G54, etc.)
WCS_SEQUENCE = ["G54", "G55", "G56", "G57", "G58", "G59"]


# ---------------------------------------------------------------------------
# Rule sheet loaders (Sheet 5: 05_setup_planning.json, Sheet 6: 06_workholding.json)
# ---------------------------------------------------------------------------
_RULE_SHEET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rule_sheets')
_SETUP_RULE_SHEET_PATH = os.path.join(_RULE_SHEET_DIR, '05_setup_planning.json')
_WORKHOLD_RULE_SHEET_PATH = os.path.join(_RULE_SHEET_DIR, '06_workholding.json')


def _load_json_if_exists(path: str) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def load_setup_planning_rule_sheet(path: str = None) -> Optional[Dict]:
    """Load Sheet 5 (setup planning) JSON. Safe-by-default if missing/invalid."""
    return _load_json_if_exists(path or _SETUP_RULE_SHEET_PATH)


def load_workholding_rule_sheet(path: str = None) -> Optional[Dict]:
    """Load Sheet 6 (workholding) JSON. Safe-by-default if missing/invalid."""
    return _load_json_if_exists(path or _WORKHOLD_RULE_SHEET_PATH)


def _apply_setup_planning_rules(rules: Dict) -> None:
    global VMC_DEFAULT_SPINDLE, AXIS_PARALLEL_TOL
    global WCS_CORNER_ORIGIN_FRAC, WCS_SEQUENCE
    global _ALL_FACES

    if not isinstance(rules, dict):
        return

    vmc = rules.get('vmc_convention') or {}
    if 'default_spindle_direction_cad' in vmc:
        try:
            VMC_DEFAULT_SPINDLE = np.array(vmc['default_spindle_direction_cad'], dtype=float)
        except Exception:
            pass

    if 'axis_parallel_tolerance' in rules:
        try:
            AXIS_PARALLEL_TOL = float(rules['axis_parallel_tolerance'])
        except Exception:
            pass

    wcs = rules.get('wcs') or {}
    seq = wcs.get('sequence')
    if isinstance(seq, list) and seq:
        WCS_SEQUENCE = [str(x) for x in seq]

    corner = rules.get('wcs_origin_corner_heuristic') or {}
    if 'fraction_of_dimension' in corner:
        try:
            WCS_CORNER_ORIGIN_FRAC = float(corner['fraction_of_dimension'])
        except Exception:
            pass

    stock = rules.get('stock_state') or {}
    faces = stock.get('all_part_faces')
    if isinstance(faces, list) and faces:
        _ALL_FACES = [str(f) for f in faces]


_SETUP_RULES = load_setup_planning_rule_sheet()
_WORKHOLD_RULES = load_workholding_rule_sheet()
if _SETUP_RULES is not None:
    _apply_setup_planning_rules(_SETUP_RULES)


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
# Workholding config builder
# ---------------------------------------------------------------------------

def _build_workholding(spindle_dir: np.ndarray,
                       setup_type: str,
                       rotation: Optional[Dict],
                       bbox: Dict,
                       setup_index: int) -> Dict:
    """
    Build structured workholding configuration for a setup.

    Parameters
    ----------
    spindle_dir : np.ndarray  — unit vector: direction tool approaches from
    setup_type  : str         — 'principal' or 'angled'
    rotation    : dict|None   — rotation info for angled setups
    bbox        : dict        — bounding box with xmin/xmax/ymin/ymax/zmin/zmax
    setup_index : int         — 1-based setup number (1 = first setup)

    Returns
    -------
    dict with keys:
        type             : str        workholding device type
        clamp_faces      : [str]      faces where jaws/clamps contact the part
        rest_face        : str|None   face the part rests on (datum bottom)
        clearance_faces  : [str]      faces that must be clear for spindle access
        jaw_opening_mm   : float|None part dimension in clamping direction
        datum_from_setup : int|None   setup whose machined surfaces serve as datum
        notes            : str        operator instruction
    """
    sd = _unit(spindle_dir)

    # Compute part dimensions from bbox if available
    x_dim = y_dim = z_dim = None
    if bbox:
        xmin, xmax = bbox.get('xmin'), bbox.get('xmax')
        ymin, ymax = bbox.get('ymin'), bbox.get('ymax')
        zmin, zmax = bbox.get('zmin'), bbox.get('zmax')
        if xmin is not None and xmax is not None:
            x_dim = round(float(xmax - xmin), 2)
        if ymin is not None and ymax is not None:
            y_dim = round(float(ymax - ymin), 2)
        if zmin is not None and zmax is not None:
            z_dim = round(float(zmax - zmin), 2)

    datum_from_setup = setup_index - 1 if setup_index > 1 else None

    # ------------------------------------------------------------------
    # Rule-sheet driven workholding (Sheet 6) — best-effort, safe fallback
    # ------------------------------------------------------------------
    if isinstance(_WORKHOLD_RULES, dict):
        try:
            # Angled setup template
            if setup_type == 'angled' and rotation is not None:
                t = ((_WORKHOLD_RULES.get('angled_setup') or {}).get('template')) or {}
                angle = rotation.get('angle_deg')
                ax_label = rotation.get('rotation_axis_label')
                notes = t.get('notes_pattern') or ''
                if notes:
                    notes = (f'{notes} '
                             f'(angle={angle}°, axis={ax_label})').strip()
                else:
                    notes = (f'Sine plate set to {angle}° around {ax_label}. '
                             f'Verify angle with dial indicator before machining.')
                return {
                    'type': t.get('type', 'sine_plate'),
                    'clamp_faces': t.get('clamp_faces', ['+X', '-X']),
                    'rest_face': t.get('rest_face', '-Y'),
                    'clearance_faces': t.get('clearance_faces', ['+Y']),
                    'jaw_opening_mm': x_dim,
                    'datum_from_setup': datum_from_setup,
                    'notes': notes,
                }

            # Principal templates: choose by dominant spindle component (same logic as hardcoded path)
            templates = _WORKHOLD_RULES.get('principal_spindle_templates') or []
            chosen = None
            if abs(sd[1]) > 0.9:
                # +Y or -Y
                if sd[1] > 0:
                    # first template is expected to be +Y
                    chosen = templates[0] if len(templates) > 0 else None
                else:
                    chosen = templates[1] if len(templates) > 1 else None
            elif abs(sd[0]) > 0.9:
                chosen = templates[2] if len(templates) > 2 else None
            elif abs(sd[2]) > 0.9:
                chosen = templates[3] if len(templates) > 3 else None

            if isinstance(chosen, dict) and chosen:
                wh_type = chosen.get('type')
                if wh_type is None and abs(sd[1]) > 0.9 and sd[1] > 0:
                    wh_type = chosen.get('type_setup_1') if setup_index == 1 else chosen.get('type_later')
                clearance_faces = chosen.get('clearance_faces')
                # Resolve dynamic clearance face for side setups
                if isinstance(clearance_faces, list) and clearance_faces and isinstance(clearance_faces[0], str):
                    if 'spindle_x' in clearance_faces[0]:
                        clearance_faces = ['+X' if sd[0] > 0 else '-X']
                    if 'spindle_z' in clearance_faces[0]:
                        clearance_faces = ['+Z' if sd[2] > 0 else '-Z']

                # jaw_opening mapping
                jaw = None
                jaw_src = chosen.get('jaw_opening_mm_from_bbox')
                if jaw_src == 'x_dim':
                    jaw = x_dim
                elif jaw_src == 'y_dim':
                    jaw = y_dim
                elif jaw_src == 'z_dim':
                    jaw = z_dim

                return {
                    'type': wh_type or 'custom_fixture',
                    'clamp_faces': chosen.get('clamp_faces', []),
                    'rest_face': chosen.get('rest_face'),
                    'clearance_faces': clearance_faces or [],
                    'jaw_opening_mm': jaw,
                    'datum_from_setup': datum_from_setup,
                    'notes': (chosen.get('notes')
                              or 'Workholding from rule sheet (Sheet 6). Verify clamp and clearance faces.'),
                }
        except Exception:
            # If anything goes wrong, fall back to the hardcoded logic below.
            pass

    # --- Angled setup ---
    if setup_type == 'angled' and rotation is not None:
        angle    = rotation['angle_deg']
        ax_label = rotation['rotation_axis_label']
        return {
            'type'             : 'sine_plate',
            'clamp_faces'      : ['+X', '-X'],
            'rest_face'        : '-Y',
            'clearance_faces'  : ['+Y'],
            'jaw_opening_mm'   : x_dim,
            'datum_from_setup' : datum_from_setup,
            'notes'            : (f'Sine plate set to {angle}° around {ax_label}. '
                                  f'Verify angle with dial indicator before machining. '
                                  f'Alternative: 4th/5th axis rotary if available.'),
        }

    # --- Principal setups ---
    if abs(sd[1]) > 0.9:
        if sd[1] > 0:
            # +Y spindle — tool comes from above (top setup)
            wh_type = 'vise' if setup_index == 1 else 'fixture_plate'
            return {
                'type'             : wh_type,
                'clamp_faces'      : ['+X', '-X'],
                'rest_face'        : '-Y',
                'clearance_faces'  : ['+Y'],
                'jaw_opening_mm'   : x_dim,
                'datum_from_setup' : datum_from_setup,
                'notes'            : ('Standard vise, jaws on ±X faces. '
                                      'Part rests on parallels. '
                                      'Ensure full +Y face exposure for spindle access.'),
            }
        else:
            # -Y spindle — tool comes from below (part flipped)
            return {
                'type'             : 'step_jaw_vise',
                'clamp_faces'      : ['+X', '-X'],
                'rest_face'        : '+Y',
                'clearance_faces'  : ['-Y'],
                'jaw_opening_mm'   : x_dim,
                'datum_from_setup' : datum_from_setup,
                'notes'            : ('Part flipped — previously machined top face '
                                      'is now the datum bottom. Use step jaws to '
                                      'clear previously machined features. '
                                      'Verify datum face is fully seated before zeroing.'),
            }

    elif abs(sd[0]) > 0.9:
        # ±X spindle — side face setup
        clearance = '+X' if sd[0] > 0 else '-X'
        return {
            'type'             : 'angle_plate',
            'clamp_faces'      : ['-Y', '+Z'],
            'rest_face'        : '-Y',
            'clearance_faces'  : [clearance],
            'jaw_opening_mm'   : z_dim,
            'datum_from_setup' : datum_from_setup,
            'notes'            : (f'90° angle plate. Mount part with bottom face (-Y) '
                                  f'bolted to plate. {clearance} face must be fully '
                                  f'exposed for spindle access. Indicate in before machining.'),
        }

    elif abs(sd[2]) > 0.9:
        # ±Z spindle — front/rear face setup
        clearance = '+Z' if sd[2] > 0 else '-Z'
        return {
            'type'             : 'angle_plate',
            'clamp_faces'      : ['-Y', '+X'],
            'rest_face'        : '-Y',
            'clearance_faces'  : [clearance],
            'jaw_opening_mm'   : x_dim,
            'datum_from_setup' : datum_from_setup,
            'notes'            : (f'90° angle plate. Mount part with bottom face (-Y) '
                                  f'and side face (+X) referenced. {clearance} face '
                                  f'must be exposed for spindle access.'),
        }

    # Fallback — unusual axis combination
    return {
        'type'             : 'custom_fixture',
        'clamp_faces'      : [],
        'rest_face'        : None,
        'clearance_faces'  : [],
        'jaw_opening_mm'   : None,
        'datum_from_setup' : datum_from_setup,
        'notes'            : 'Custom fixture required — consult manufacturing engineer.',
    }


# ---------------------------------------------------------------------------
# WCS origin computer
# ---------------------------------------------------------------------------

def _compute_wcs_origin(spindle_dir: np.ndarray, bbox: Dict) -> Dict:
    """
    Compute the actual 3D WCS zero point in CAD space for a setup.

    The WCS origin is the point the machinist probes or edges to before
    running the program. It is always on the surface the spindle hits first
    (Z=0 in work coordinates), at either the part center or the CAD-origin
    corner depending on where the bounding box starts.

    Corner zero is used when xmin ≈ 0 (part placed at CAD origin) because
    all G-code coordinates then stay positive, which is easier to verify
    on the machine. Center zero is used for parts not aligned to the CAD
    origin (symmetric travel either side of zero).

    Parameters
    ----------
    spindle_dir : np.ndarray  — unit vector: direction tool approaches from
    bbox        : dict        — bounding box with xmin/xmax/ymin/ymax/zmin/zmax

    Returns
    -------
    dict with keys:
        x_mm      : float|None   CAD X coordinate of WCS X0
        y_mm      : float|None   CAD Y coordinate of WCS Y0
        z_mm      : float|None   CAD Z coordinate of WCS Z0 (top face)
        origin_x  : str          label: 'CENTER', 'CORNER', '+face', '-face'
        origin_y  : str          label for second in-plane axis
        origin_z  : str          always 'TOP'
        note      : str          plain English probe instruction
    """
    if not bbox:
        return {
            'x_mm': None, 'y_mm': None, 'z_mm': None,
            'origin_x': 'CENTER', 'origin_y': 'CENTER', 'origin_z': 'TOP',
            'note': 'No bounding box — set zero at part centre by inspection.',
        }

    xmin = float(bbox.get('xmin', 0))
    xmax = float(bbox.get('xmax', 0))
    ymin = float(bbox.get('ymin', 0))
    ymax = float(bbox.get('ymax', 0))
    zmin = float(bbox.get('zmin', 0))
    zmax = float(bbox.get('zmax', 0))

    x_dim = xmax - xmin
    y_dim = ymax - ymin
    z_dim = zmax - zmin

    # Corner zero when the part sits at CAD origin (mins ≈ 0).
    # Threshold: min < WCS_CORNER_ORIGIN_FRAC of the dimension (handles floating-point near-zero).
    def _at_origin(mn, dim):
        return dim > 0 and abs(mn) < WCS_CORNER_ORIGIN_FRAC * dim

    sd = _unit(spindle_dir)

    # ------------------------------------------------------------------
    # ±Y spindle — top or bottom setup
    # Face plane: CAD X (work X) × CAD Z (work Y). Depth axis: CAD Y.
    # ------------------------------------------------------------------
    if abs(sd[1]) > 0.9:
        z_coord = ymax if sd[1] > 0 else ymin   # top face in CAD Y
        face    = 'top' if sd[1] > 0 else 'bottom'

        x_at_origin = _at_origin(xmin, x_dim)
        z_at_origin = _at_origin(zmin, z_dim)   # work Y uses CAD Z

        if x_at_origin:
            x_coord  = xmin
            origin_x = f'CORNER (xmin={xmin:.3f}mm — all X coords positive)'
        else:
            x_coord  = (xmin + xmax) / 2
            origin_x = f'CENTER (X={x_coord:.3f}mm, {x_coord - xmin:.2f}mm from xmin edge)'

        if z_at_origin:
            y_coord  = zmin
            origin_y = f'CORNER (zmin={zmin:.3f}mm — all Y coords positive)'
        else:
            y_coord  = (zmin + zmax) / 2
            origin_y = f'CENTER (Z={y_coord:.3f}mm, {y_coord - zmin:.2f}mm from zmin edge)'

        note = (f'Probe {face} face for Z0 (Y={z_coord:.3f}mm in CAD). '
                f'X0: {origin_x}. Y0: {origin_y}. '
                f'Part envelope: {x_dim:.2f} × {z_dim:.2f} × {y_dim:.2f}mm (X × Z × Y).')

        return {
            'x_mm': round(x_coord, 4), 'y_mm': round(y_coord, 4),
            'z_mm': round(z_coord, 4),
            'origin_x': origin_x, 'origin_y': origin_y, 'origin_z': 'TOP',
            'note': note,
        }

    # ------------------------------------------------------------------
    # ±X spindle — left or right side setup
    # Face plane: CAD Y (work X) × CAD Z (work Y). Depth axis: CAD X.
    # ------------------------------------------------------------------
    elif abs(sd[0]) > 0.9:
        z_coord = xmax if sd[0] > 0 else xmin
        face    = 'right' if sd[0] > 0 else 'left'

        y_at_origin = _at_origin(ymin, y_dim)
        z_at_origin = _at_origin(zmin, z_dim)

        if y_at_origin:
            x_coord  = ymin
            origin_x = f'CORNER (ymin={ymin:.3f}mm)'
        else:
            x_coord  = (ymin + ymax) / 2
            origin_x = f'CENTER (Y={x_coord:.3f}mm)'

        if z_at_origin:
            y_coord  = zmin
            origin_y = f'CORNER (zmin={zmin:.3f}mm)'
        else:
            y_coord  = (zmin + zmax) / 2
            origin_y = f'CENTER (Z={y_coord:.3f}mm)'

        note = (f'Probe {face} face for Z0 (X={z_coord:.3f}mm in CAD). '
                f'X0: {origin_x}. Y0: {origin_y}. '
                f'Part envelope: {y_dim:.2f} × {z_dim:.2f} × {x_dim:.2f}mm.')

        return {
            'x_mm': round(x_coord, 4), 'y_mm': round(y_coord, 4),
            'z_mm': round(z_coord, 4),
            'origin_x': origin_x, 'origin_y': origin_y, 'origin_z': 'TOP',
            'note': note,
        }

    # ------------------------------------------------------------------
    # ±Z spindle — front or rear face setup
    # Face plane: CAD X (work X) × CAD Y (work Y). Depth axis: CAD Z.
    # ------------------------------------------------------------------
    elif abs(sd[2]) > 0.9:
        z_coord = zmax if sd[2] > 0 else zmin
        face    = 'front' if sd[2] > 0 else 'rear'

        x_at_origin = _at_origin(xmin, x_dim)
        y_at_origin = _at_origin(ymin, y_dim)

        if x_at_origin:
            x_coord  = xmin
            origin_x = f'CORNER (xmin={xmin:.3f}mm)'
        else:
            x_coord  = (xmin + xmax) / 2
            origin_x = f'CENTER (X={x_coord:.3f}mm)'

        if y_at_origin:
            y_coord  = ymin
            origin_y = f'CORNER (ymin={ymin:.3f}mm)'
        else:
            y_coord  = (ymin + ymax) / 2
            origin_y = f'CENTER (Y={y_coord:.3f}mm)'

        note = (f'Probe {face} face for Z0 (Z={z_coord:.3f}mm in CAD). '
                f'X0: {origin_x}. Y0: {origin_y}. '
                f'Part envelope: {x_dim:.2f} × {y_dim:.2f} × {z_dim:.2f}mm.')

        return {
            'x_mm': round(x_coord, 4), 'y_mm': round(y_coord, 4),
            'z_mm': round(z_coord, 4),
            'origin_x': origin_x, 'origin_y': origin_y, 'origin_z': 'TOP',
            'note': note,
        }

    # Fallback
    return {
        'x_mm': None, 'y_mm': None, 'z_mm': None,
        'origin_x': 'CENTER', 'origin_y': 'CENTER', 'origin_z': 'TOP',
        'note': 'Unusual spindle direction — set zero at part centre by inspection.',
    }


# ---------------------------------------------------------------------------
# Stock state tracker
# ---------------------------------------------------------------------------

_ALL_FACES = ['+X', '-X', '+Y', '-Y', '+Z', '-Z']


def _compute_stock(setups: List[Dict], setup_index: int) -> Dict:
    """
    Compute the stock state when a setup begins.

    Parameters
    ----------
    setups      : list of setup dicts built so far (in order, with workholding)
    setup_index : 0-based index of the setup whose stock we are computing

    Returns
    -------
    dict with keys:
        type             : 'raw_billet' | 'previous_setup'
        source_setup_id  : int | None   — which setup produced this stock
        remaining_faces  : [str]        — faces not yet machined
        machined_faces   : [str]        — faces machined in earlier setups

    Logic
    -----
    Each setup machines the faces listed in its workholding 'clearance_faces'
    (those are the faces the spindle accessed). We accumulate all clearance
    faces from setups 0 … (setup_index-1) to find what is already machined.
    """
    if setup_index == 0:
        return {
            'type'            : 'raw_billet',
            'source_setup_id' : None,
            'remaining_faces' : list(_ALL_FACES),
            'machined_faces'  : [],
        }

    machined = []
    for s in setups[:setup_index]:
        wh = s.get('workholding', {})
        for face in wh.get('clearance_faces', []):
            if face not in machined:
                machined.append(face)

    remaining = [f for f in _ALL_FACES if f not in machined]

    return {
        'type'            : 'previous_setup',
        'source_setup_id' : setups[setup_index - 1]['setup_id'],
        'remaining_faces' : remaining,
        'machined_faces'  : machined,
    }


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
        wcs = WCS_SEQUENCE[min(setup_id - 1, len(WCS_SEQUENCE) - 1)]

        wcs_origin = _compute_wcs_origin(
            spindle_dir,
            processes_data.get('bounding_box', {}),
        )
        origin_x = wcs_origin['origin_x']
        origin_y = wcs_origin['origin_y']
        origin_z = wcs_origin['origin_z']

        # ------------------------------------------------------------------
        # Bounding box dimensions (§2b)
        # ------------------------------------------------------------------
        sd   = spindle_dir   # unit vector — used in bbox dimension mapping below
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
            'wcs_origin_mm'         : wcs_origin,
            'setup_face_width'      : face_width,
            'setup_face_height'     : face_height,
            'setup_depth'           : depth,
            'cluster_ids'           : sorted(c['cluster_id'] for c in group_clust),
            'operation_count'       : op_count,
            'machining_sequence'    : machining_sequence,
            'workholding'           : _build_workholding(
                                          spindle_dir,
                                          setup_type,
                                          rotation,
                                          processes_data.get('bounding_box', {}),
                                          setup_id,
                                      ),
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
                'workholding'           : _build_workholding(
                                              np.array([0.0, 1.0, 0.0]),
                                              'principal',
                                              None,
                                              processes_data.get('bounding_box', {}),
                                              setup_id,
                                          ),
            })

    # ------------------------------------------------------------------
    # Step 5: Attach stock state to each setup (second pass)
    # ------------------------------------------------------------------
    for i, s in enumerate(setups):
        s['stock'] = _compute_stock(setups, i)

    # ------------------------------------------------------------------
    # Step 6: Write setup_id back onto each cluster for traceability
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

        stk = s.get('stock')
        if stk:
            src = f" (from setup {stk['source_setup_id']})" if stk.get('source_setup_id') else ''
            print(f"    Stock     : {stk['type']}{src}")
            print(f"                machined so far : {stk['machined_faces'] or 'none'}")
            print(f"                remaining faces : {stk['remaining_faces']}")

        wh = s.get('workholding')
        if wh:
            jaw = f"  jaw_opening={wh['jaw_opening_mm']}mm" if wh.get('jaw_opening_mm') else ''
            datum = f"  datum_from_setup={wh['datum_from_setup']}" if wh.get('datum_from_setup') else ''
            print(f"    Workholding: type={wh['type']}  "
                  f"clamp={wh['clamp_faces']}  "
                  f"rest={wh['rest_face']}"
                  f"{jaw}{datum}")

        rot = s.get('rotation_from_default')
        if rot:
            print(f"    Rotation  : {rot['angle_deg']}° around {rot['rotation_axis_label']}")

        print(f"    WCS       : {s.get('wcs', 'N/A')}")
        wo = s.get('wcs_origin_mm', {})
        if wo:
            origin_x = s.get('origin_x', '?')
            origin_y = s.get('origin_y', '?')
            origin_z = s.get('origin_z')
            origin_z_label = origin_z if origin_z is not None else 'TOP'
            cad_x = wo.get('x_mm') if wo.get('x_mm') is not None else 0.0
            cad_y = wo.get('y_mm') if wo.get('y_mm') is not None else 0.0
            cad_z = wo.get('z_mm') if wo.get('z_mm') is not None else 0.0
            print(f"    Origin    : X={origin_x}")
            print(f"                Y={origin_y}")
            print(f"                Z={origin_z_label}  "
                  f"(CAD point: {cad_x:.3f}mm, "
                  f"{cad_y:.3f}mm, "
                  f"{cad_z:.3f}mm)")
            print(f"    Probe note: {wo.get('note', '')}")

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
