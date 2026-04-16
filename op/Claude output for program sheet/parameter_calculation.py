"""
parameter_calculation.py
------------------------
Parameter calculation step. Takes the tool-selected JSON from
tool_selection.py and computes the actual cutting parameters for
every operation step.

This answers the question:
  "For each operation, what are the exact numbers to put into the
   CNC program — spindle speed (S), feed rate (F), depth of cut,
   and drill cycle parameters?"

Formulas used
-------------
All from Machinery's Handbook 29th ed., Ch.26-27 and standard
machining practice:

  RPM  = (Vc × 1000) / (π × D)
    Vc = surface speed in m/min (from tool database)
    D  = tool diameter in mm

  Vf (feed rate, mm/min):
    Drills:  Vf = RPM × feed_per_rev
    Mills:   Vf = RPM × fz × z
      fz = feed per tooth (mm/tooth, from tool database)
      z  = number of flutes

  Depth of cut:
    ap = axial depth of cut (mm) — from tool_database ap_max_mm
    ae = radial depth of cut (mm) — from tool_database ae_max_mm
         or ae_fraction × tool_diameter for face mills

  Peck increment (for G83 peck drill cycle):
    standard peck: Q = 0.5 × D   (DDR ≤ 5)
    deep peck:     Q = 0.3 × D   (DDR > 5)
    Through-spindle coolant allows larger peck increments.

  RPM cap: all calculated RPMs are capped at MAX_SPINDLE_RPM.
    If capped, actual Vc is recalculated and flagged.

Coolant effect on Vc
---------------------
Through-spindle coolant (TSC) provides active chip evacuation inside
the drill flute. For drills < 3mm this is significant — without TSC,
small drills require peck cycles and reduced Vc to avoid chip packing.
With TSC, full recommended Vc is used and peck increments are larger.

Output added to each step
--------------------------
Each operation step gains:

  rpm            : int    — spindle speed (S word in G-code)
  vf_mmpm        : float  — feed rate mm/min (F word in G-code)
  ap_mm          : float  — axial depth of cut
  ae_mm          : float|None — radial depth (mills only)
  peck_mm        : float|None — peck increment Q (drill cycles only)
  rpm_capped     : bool   — True if RPM was limited by machine max
  actual_Vc_mmin : float  — effective surface speed after RPM cap
  param_notes    : str    — any warnings or adjustments

Usage
-----
    python parameter_calculation.py <tools_json> [output_json]
      [--max-rpm 10000] [--coolant through_spindle|flood|mist|dry]

    python parameter_calculation.py Hub_tools.json Hub_params.json
      --max-rpm 10000 --coolant through_spindle

Or from Python:
    from parameter_calculation import calculate_parameters
    result = calculate_parameters(tools_data,
                                  max_rpm=10000,
                                  coolant='through_spindle')
"""

import json
import math
import sys
import copy
import os
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Machine configuration — override via CLI or function args
# ---------------------------------------------------------------------------

MAX_SPINDLE_RPM = 10000          # VMC maximum spindle speed
COOLANT         = 'through_spindle'  # 'through_spindle' | 'flood' | 'mist' | 'dry'

# Peck increment as fraction of drill diameter, by cycle type and coolant
PECK_FRACTIONS = {
    'through_spindle': {'peck': 0.8, 'deep_peck': 0.5},
    'flood':           {'peck': 0.5, 'deep_peck': 0.3},
    'mist':            {'peck': 0.4, 'deep_peck': 0.25},
    'dry':             {'peck': 0.3, 'deep_peck': 0.2},
}

# Vc multiplier for through-spindle coolant on small drills (< 3mm)
# TSC actively clears chips inside the flute — allows higher speed
TSC_VC_BOOST_SMALL_DRILL = 1.15   # 15% above catalogue value

# Minimum feed rate (safety floor — never command below this)
MIN_VF_MMPM = 10.0

# RPM rounding — round to nearest N for cleaner G-code values
RPM_ROUND_TO = 10

# Feed rate rounding
VF_ROUND_TO = 1   # round to nearest 1 mm/min

# Path to tool database (same directory as this script)
_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'tool_database.json')

# ---------------------------------------------------------------------------
# Cycle time estimation
# ---------------------------------------------------------------------------

TOOL_CHANGE_TIME_S = 8.0  # seconds per ATC tool change

# ---------------------------------------------------------------------------
# Tool database lookup (for fields not carried forward by tool_selection)
# ---------------------------------------------------------------------------

_DB_CACHE = None

def _load_db(db_path: str = None) -> Dict:
    global _DB_CACHE
    if _DB_CACHE is not None:
        return _DB_CACHE
    path = db_path or _DB_PATH
    with open(path) as f:
        _DB_CACHE = json.load(f)
    return _DB_CACHE


def _get_tool_record(tool_id: str, db: Dict) -> Optional[Dict]:
    """Return the full tool record from the database by tool_id."""
    for t in db.get('tools', []):
        if t.get('tool_id') == tool_id:
            return t
    return None


def _get_mat_params(tool_record: Dict, material: str) -> Dict:
    """Return the material_params dict for a tool + material combination."""
    return tool_record.get('material_params', {}).get(material, {})


def _resolve_material(material: str, db: Dict) -> str:
    """Resolve material aliases to canonical name."""
    aliases = db.get('material_aliases', {})
    return aliases.get(material.lower(), material.lower())


# ---------------------------------------------------------------------------
# Cycle time estimation helper
# ---------------------------------------------------------------------------

def _estimate_cycle_time(op: str, step: dict, cluster: dict) -> float:
    """Estimate cycle time in seconds. Ballpark only."""
    feed_rate = step.get('vf_mmpm') or 0.0
    if feed_rate <= 0:
        return 10.0

    if op in ('spot_drill', 'twist_drill', 'micro_drill', 'pilot_drill', 'core_drill'):
        depth = abs(step.get('ap_mm') or step.get('depth_mm') or 0)
        peck = step.get('peck_mm') or depth
        num_pecks = math.ceil(depth / peck) if peck > 0 else 1
        drill_time = (depth / feed_rate) * 60
        retract_time = num_pecks * 0.5
        return round(drill_time + retract_time, 1)

    elif op == 'face_mill':
        # Estimate from depth and basic face area heuristic
        depth = abs(step.get('ap_mm') or 1.0)
        total_depth = abs(step.get('depth_mm') or depth)
        ae = step.get('ae_mm') or 1.0
        # rough face area from cluster bounding box if available
        face_w = cluster.get('face_width') or cluster.get('bbox_size_mm', [20])[0]
        face_l = cluster.get('face_length') or (cluster.get('bbox_size_mm', [20, 20]) + [20])[1]
        if isinstance(face_w, (int, float)) and isinstance(face_l, (int, float)):
            num_passes_radial = math.ceil(face_w / ae) if ae > 0 else 1
            num_passes_axial = math.ceil(total_depth / depth) if depth > 0 else 1
            path_length = num_passes_radial * face_l * num_passes_axial
        else:
            path_length = 100.0
        cut_time = (path_length / feed_rate) * 60
        return round(cut_time + num_passes_radial * 1.0, 1)

    elif op in ('contour_mill', 'pocket_mill', 'counterbore_mill'):
        depth_per_pass = abs(step.get('ap_mm') or 1.0)
        total_depth = abs(step.get('depth_mm') or depth_per_pass)
        pass_type = step.get('pass_type')

        # Perimeter approximation
        radii = cluster.get('radii', [5])
        dia = 2 * (radii[0] if radii else 5)
        perimeter = math.pi * dia

        if pass_type == 'RF':
            num_axial = math.ceil(total_depth / depth_per_pass) if depth_per_pass > 0 else 1
            path_length = perimeter * num_axial
        elif pass_type == 'FINISH':
            path_length = perimeter  # single spring pass
        elif pass_type == 'CORNER_R':
            path_length = perimeter * 0.3  # corners only
        else:
            path_length = perimeter

        cut_time = (path_length / feed_rate) * 60
        return round(cut_time + 2.0, 1)

    elif op == 'boring_bar':
        depth = abs(step.get('ap_mm') or step.get('depth_mm') or 0)
        cut_time = (depth / feed_rate) * 60
        return round(cut_time + 2.0, 1)

    return 10.0  # fallback


# ---------------------------------------------------------------------------
# Core formulas
# ---------------------------------------------------------------------------

def _calc_rpm(Vc_mmin: float, diameter_mm: float) -> float:
    """
    RPM = (Vc × 1000) / (π × D)
    Standard formula — Machinery's Handbook Ch.26.
    """
    if diameter_mm <= 0:
        return 0.0
    return (Vc_mmin * 1000.0) / (math.pi * diameter_mm)


def _apply_rpm_cap(rpm_raw: float, max_rpm: int,
                   Vc_mmin: float, diameter_mm: float
                   ) -> Tuple[int, bool, float]:
    """
    Cap RPM at machine maximum. Recalculate actual Vc if capped.
    Returns (rpm_final, was_capped, actual_Vc).
    """
    if rpm_raw > max_rpm:
        rpm_final  = max_rpm
        actual_Vc  = (max_rpm * math.pi * diameter_mm) / 1000.0
        was_capped = True
    else:
        rpm_final  = int(round(rpm_raw / RPM_ROUND_TO) * RPM_ROUND_TO)
        actual_Vc  = Vc_mmin
        was_capped = False
    return rpm_final, was_capped, round(actual_Vc, 1)


def _calc_vf_drill(rpm: int, feed_per_rev: float) -> float:
    """
    Feed rate for drills: Vf = RPM × feed_per_rev
    """
    vf = rpm * feed_per_rev
    return max(vf, MIN_VF_MMPM)


def _calc_vf_mill(rpm: int, fz: float, flute_count: int) -> float:
    """
    Feed rate for mills: Vf = RPM × fz × z
    """
    vf = rpm * fz * flute_count
    return max(vf, MIN_VF_MMPM)


def _round_vf(vf: float) -> float:
    return round(vf / VF_ROUND_TO) * VF_ROUND_TO


def _peck_increment(drill_diameter: float, cycle: str, coolant: str) -> float:
    """
    Peck increment Q for G83 drill cycle.
    Larger peck = fewer retracts = faster cycle.
    Through-spindle coolant allows larger increments.
    """
    fractions = PECK_FRACTIONS.get(coolant, PECK_FRACTIONS['flood'])
    fraction  = fractions.get(cycle, 0.5)
    return round(drill_diameter * fraction, 3)


# ---------------------------------------------------------------------------
# Per-operation parameter calculation
# ---------------------------------------------------------------------------

def _calc_step_params(step: Dict, cluster: Dict,
                      material: str, max_rpm: int,
                      coolant: str, db: Dict) -> Dict:
    """
    Calculate cutting parameters for a single operation step.
    Adds rpm, vf_mmpm, ap_mm, ae_mm, peck_mm, rpm_capped,
    actual_Vc_mmin, param_notes to the step dict in place.
    """
    op      = step.get('operation', '')
    tool_id = step.get('tool_id')

    # Steps with no tool
    if op in ('fixture_rotation', 'manual_review') or not tool_id:
        step['rpm']            = None
        step['vf_mmpm']        = None
        step['ap_mm']          = None
        step['ae_mm']          = None
        step['peck_mm']        = None
        step['rpm_capped']     = False
        step['actual_Vc_mmin'] = None
        step['param_notes']    = ''
        return step

    if tool_id == 'NOT_FOUND':
        step['rpm']            = None
        step['vf_mmpm']        = None
        step['ap_mm']          = None
        step['ae_mm']          = None
        step['peck_mm']        = None
        step['rpm_capped']     = False
        step['actual_Vc_mmin'] = None
        step['param_notes']    = 'Tool not found — parameters cannot be calculated'
        return step

    # Retrieve fields from step (set by tool_selection)
    fpr         = step.get('feed_per_rev_mm')   # drills
    tool_dia    = step.get('tool_diameter_mm') or 0.0
    drill_cycle = step.get('drill_cycle')
    depth       = step.get('depth_mm')

    # ------------------------------------------------------------------
    # Depth fallback — derive from cluster when step doesn't have it
    # ------------------------------------------------------------------
    if depth is None:
        if op == 'spot_drill':
            # Spot drill depth: for a 90° point, countersink depth ≈
            # hole_diameter / 2 (half the included angle). Use the cluster
            # depth as the hole depth reference, but the countersink only
            # goes ~1/3 of the drill diameter deep.
            # Vendor shows values like -1.825 for a 1mm hole with a 3mm
            # spot drill — roughly diameter × 0.6.
            spot_depth = round(tool_dia * 0.5, 3)
            depth = spot_depth
            step['depth_mm'] = depth
        elif op == 'face_mill':
            # Face mills: cluster.depth is usually None for planar_face.
            # Use ap_mm as the depth (single pass depth = total depth
            # for light face skimming).
            cluster_depth = cluster.get('depth')
            if cluster_depth is not None:
                depth = abs(cluster_depth)
            else:
                # Use ap_max from tool database as a reasonable face depth
                depth = step.get('ap_mm') or 2.0
            step['depth_mm'] = depth
        elif op in ('counterbore_mill', 'contour_mill', 'pocket_mill',
                     'circular_interp', 'boring_bar'):
            # Milling ops: fall back to cluster depth
            cluster_depth = cluster.get('depth')
            if cluster_depth is not None:
                depth = abs(cluster_depth)
                step['depth_mm'] = depth

    # Retrieve fields from database (not carried forward by tool_selection)
    tool_rec         = _get_tool_record(tool_id, db)
    material_resolved = _resolve_material(material, db)
    mat_params       = _get_mat_params(tool_rec, material_resolved) if tool_rec else {}
    flute_count      = (tool_rec or {}).get('flute_count') or (tool_rec or {}).get('insert_count') or 2
    ap_max           = mat_params.get('ap_max_mm')
    ae_max           = mat_params.get('ae_max_mm')
    ae_fraction      = mat_params.get('ae_fraction')

    # ------------------------------------------------------------------
    # Vc / fz — differentiated by pass_type (§4a)
    # ------------------------------------------------------------------
    pass_type = step.get('pass_type')  # 'RF' | 'FINISH' | 'CORNER_R' | None

    # Use pass-type-specific Vc/fz if available in tool record
    if pass_type == 'RF':
        Vc = tool_rec.get('Vc_rough', {}).get(material_resolved,
             mat_params.get('Vc_mmin', 0.0)) if tool_rec else mat_params.get('Vc_mmin', 0.0)
        fz = tool_rec.get('fz_rough', {}).get(material_resolved,
             mat_params.get('fz_mm')) if tool_rec else mat_params.get('fz_mm')
    elif pass_type in ('FINISH', 'CORNER_R'):
        Vc = tool_rec.get('Vc_finish', {}).get(material_resolved,
             mat_params.get('Vc_mmin', 0.0)) if tool_rec else mat_params.get('Vc_mmin', 0.0)
        fz = tool_rec.get('fz_finish', {}).get(material_resolved,
             mat_params.get('fz_mm')) if tool_rec else mat_params.get('fz_mm')
    else:
        Vc = mat_params.get('Vc_mmin', 0.0)
        fz = mat_params.get('fz_mm')
    # Override with step-level values if they were explicitly set by tool_selection
    if not Vc:
        Vc = step.get('Vc_mmin') or 0.0
    if fz is None:
        fz = step.get('fz_mm')

    notes = []

    # ------------------------------------------------------------------
    # Through-spindle coolant Vc boost for small drills
    # ------------------------------------------------------------------
    if (coolant == 'through_spindle'
            and op in ('twist_drill', 'micro_drill', 'pilot_drill', 'core_drill')
            and tool_dia < 3.0):
        Vc_used = Vc * TSC_VC_BOOST_SMALL_DRILL
        notes.append(f'TSC boost: Vc {Vc} -> {round(Vc_used,1)} m/min (+15% for d<3mm)')
    else:
        Vc_used = Vc

    # ------------------------------------------------------------------
    # RPM
    # ------------------------------------------------------------------
    rpm_raw             = _calc_rpm(Vc_used, tool_dia)
    rpm, capped, act_Vc = _apply_rpm_cap(rpm_raw, max_rpm, Vc_used, tool_dia)

    if capped:
        notes.append(
            f'RPM capped at {max_rpm} (calculated {int(rpm_raw)} exceeds machine max). '
            f'Effective Vc = {act_Vc} m/min (reduced from {round(Vc_used,1)})'
        )

    # ------------------------------------------------------------------
    # Feed rate
    # ------------------------------------------------------------------
    if op in ('spot_drill', 'twist_drill', 'micro_drill',
              'pilot_drill', 'core_drill', 'boring_bar'):
        # Drills: feed per revolution
        if fpr:
            vf = _round_vf(_calc_vf_drill(rpm, fpr))
        else:
            vf = MIN_VF_MMPM
            notes.append('feed_per_rev not set — using minimum feed rate')

    else:
        # Mills: feed per tooth × flutes
        if fz:
            vf = _round_vf(_calc_vf_mill(rpm, fz, flute_count))
        else:
            vf = MIN_VF_MMPM
            notes.append('fz not set — using minimum feed rate')

    # ------------------------------------------------------------------
    # Axial depth of cut (ap)
    # ------------------------------------------------------------------
    if op in ('spot_drill', 'twist_drill', 'micro_drill',
              'pilot_drill', 'core_drill'):
        # Drills: ap = full hole depth (G81/G83 handles this)
        ap = depth  # may be None for spot drill — that is fine
    elif op == 'boring_bar':
        ap = depth
    elif ap_max is not None:
        # Use tool database ap_max, but don't exceed actual feature depth
        if depth is not None:
            ap = min(ap_max, depth)
        else:
            ap = ap_max
    else:
        ap = None
        notes.append('ap not available in tool database')

    # ------------------------------------------------------------------
    # Radial depth of cut (ae)
    # ------------------------------------------------------------------
    if op in ('spot_drill', 'twist_drill', 'micro_drill',
              'pilot_drill', 'core_drill', 'boring_bar'):
        ae = None   # not applicable for rotating-tool drilling

    elif op == 'face_mill':
        # ae = fraction of face mill diameter
        if ae_fraction and tool_dia:
            ae = round(ae_fraction * tool_dia, 2)
        elif ae_max:
            ae = ae_max
        else:
            ae = round(0.75 * tool_dia, 2)
            notes.append('ae_fraction not in DB — defaulting to 75% of cutter dia')

    elif op in ('counterbore_mill',):
        # Counterbore: tool fills the bore — ae = radial cut = half tool dia
        ae = round(tool_dia / 2.0, 3)

    elif op == 'circular_interp':
        # Circular interpolation: ae = radial step per pass
        # Use tool database ae_max (typically 50% of diameter)
        ae = ae_max if ae_max else round(tool_dia * 0.5, 3)

    elif op == 'contour_mill':
        # Contour milling: ae ratio based on pass_type (§4a)
        if pass_type == 'RF':
            ae_ratio = 0.5
        elif pass_type == 'FINISH':
            ae_ratio = 0.15
        elif pass_type == 'CORNER_R':
            ae_ratio = 0.10
        else:
            ae_ratio = 0.5
        if ae_max is not None:
            ae = round(min(ae_max, tool_dia * ae_ratio), 3)
        else:
            ae = round(tool_dia * ae_ratio, 3)

    elif op == 'pocket_mill':
        # Pocket milling: ae ratio based on pass_type (§4a)
        if pass_type == 'RF':
            ae_ratio = 0.5
        elif pass_type == 'FINISH':
            ae_ratio = 0.15
        elif pass_type == 'CORNER_R':
            ae_ratio = 0.10
        else:
            ae_ratio = 0.5
        if ae_max is not None:
            ae = round(min(ae_max, tool_dia * ae_ratio), 3)
        else:
            ae = round(tool_dia * ae_ratio, 3)

    else:
        ae = ae_max

    # Round ae
    if ae is not None:
        ae = round(ae, 3)

    # ------------------------------------------------------------------
    # Peck increment (Q) for drill cycles
    # ------------------------------------------------------------------
    peck = None
    if drill_cycle in ('peck', 'deep_peck'):
        peck = _peck_increment(tool_dia, drill_cycle, coolant)
        notes.append(
            f'G83 peck cycle — Q={peck}mm '
            f'({drill_cycle}, coolant={coolant})'
        )
    elif drill_cycle == 'standard':
        notes.append('G81 standard drill cycle (no peck)')

    # ------------------------------------------------------------------
    # Write results back to step
    # ------------------------------------------------------------------
    step['rpm']            = rpm
    step['vf_mmpm']        = vf
    step['ap_mm']          = round(ap, 4) if ap is not None else None
    step['ae_mm']          = ae
    step['peck_mm']        = peck
    step['rpm_capped']     = capped
    step['actual_Vc_mmin'] = act_Vc
    step['param_notes']    = ' | '.join(notes)

    # Carry-through fields from process_selection (§4a) — preserve if already set
    if 'stock_to_leave_xy' not in step:
        step['stock_to_leave_xy'] = step.get('stock_to_leave_xy')
    if 'stock_to_leave_z' not in step:
        step['stock_to_leave_z'] = step.get('stock_to_leave_z')
    if 'pass_type' not in step:
        step['pass_type'] = pass_type

    # Cycle time estimate (§4c)
    step['estimated_time_s'] = _estimate_cycle_time(op, step, cluster)

    return step


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def calculate_parameters(tools_data: Dict,
                          max_rpm:  int = MAX_SPINDLE_RPM,
                          coolant:  str = COOLANT,
                          db_path:  str = None) -> Dict:
    """
    Calculate cutting parameters for all operation steps.

    Parameters
    ----------
    tools_data : dict — parsed JSON from tool_selection.py
    max_rpm    : int  — machine maximum spindle speed RPM
    coolant    : str  — 'through_spindle' | 'flood' | 'mist' | 'dry'
    db_path    : str  — path to tool_database.json

    Returns
    -------
    dict — copy of tools_data with rpm, vf_mmpm, ap_mm, ae_mm,
           peck_mm, rpm_capped, actual_Vc_mmin, param_notes
           added to every operation step.
    """
    db       = _load_db(db_path)
    material = tools_data.get('material', 'aluminium')
    result   = copy.deepcopy(tools_data)

    result['machine_max_rpm'] = max_rpm
    result['coolant']         = coolant

    for cluster in result.get('clusters', []):
        ft = cluster.get('feature_type', '')
        if ft == 'background':
            continue

        for step in cluster.get('process_sequence', []):
            _calc_step_params(step, cluster, material, max_rpm, coolant, db)

        for seq_key in ('process_sequence_turning', 'process_sequence_milling'):
            for step in cluster.get(seq_key, []):
                _calc_step_params(step, cluster, material, max_rpm, coolant, db)

    # Per-setup estimated time totals (§4c)
    for setup in result.get('setups', []):
        setup_cluster_ids = setup.get('cluster_ids', [])
        clusters_in_setup = [c for c in result['clusters'] if c['cluster_id'] in setup_cluster_ids]

        total_s = sum(
            step.get('estimated_time_s', 0)
            for c in clusters_in_setup
            for step in c.get('process_sequence', [])
            if step.get('operation') not in ('fixture_rotation', 'manual_review')
        )
        # Add tool change times
        unique_tools = len(set(
            step.get('tool_id')
            for c in clusters_in_setup
            for step in c.get('process_sequence', [])
            if step.get('tool_id') and step.get('tool_id') != 'NOT_FOUND'
        ))
        total_s += unique_tools * TOOL_CHANGE_TIME_S
        setup['estimated_time_s'] = round(total_s, 1)

    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_param_summary(data: Dict):
    """Print a concise summary table of all computed parameters."""
    max_rpm  = data.get('machine_max_rpm', '?')
    coolant  = data.get('coolant', '?')
    material = data.get('material', '?')

    print(f"Parameter calculation summary")
    print(f"  Material: {material} | Max RPM: {max_rpm} | Coolant: {coolant}\n")

    capped_count  = 0
    warning_count = 0

    header = (f"  {'C':>3} {'Feature':22s} {'Operation':22s} "
              f"{'Tool dia':>9} {'RPM':>7} {'Vf mm/min':>10} "
              f"{'ap mm':>7} {'ae mm':>7} {'Q mm':>6} {'Cap':>4}")
    print(header)
    print('  ' + '-' * (len(header) - 2))

    for c in data.get('clusters', []):
        ft = c.get('feature_type', '')
        if ft == 'background':
            continue
        cid = c['cluster_id']

        for step in c.get('process_sequence', []):
            op  = step.get('operation', '')
            if op in ('fixture_rotation',):
                continue

            rpm   = step.get('rpm')
            vf    = step.get('vf_mmpm')
            ap    = step.get('ap_mm')
            ae    = step.get('ae_mm')
            peck  = step.get('peck_mm')
            cap   = step.get('rpm_capped', False)
            dia   = step.get('tool_diameter_mm')
            notes = step.get('param_notes', '')

            if cap:
                capped_count += 1
            if 'WARNING' in (notes or '').upper():
                warning_count += 1

            cap_str  = '[CAP]' if cap else ''
            rpm_str  = str(rpm)  if rpm  is not None else '—'
            vf_str   = str(vf)   if vf   is not None else '—'
            ap_str   = str(ap)   if ap   is not None else '—'
            ae_str   = str(ae)   if ae   is not None else '—'
            peck_str = str(peck) if peck is not None else '—'
            dia_str  = f'{dia}mm' if dia is not None else '—'

            print(f"  {cid:>3} {ft:22s} {op:22s} "
                  f"{dia_str:>9} {rpm_str:>7} {vf_str:>10} "
                  f"{ap_str:>7} {ae_str:>7} {peck_str:>6} {cap_str:>4}")

    print()
    if capped_count:
        print(f"  [CAP] {capped_count} operation(s) had RPM capped at {max_rpm}. "
              f"Effective Vc reduced — check param_notes in JSON for details.")
    else:
        print(f"  No RPM caps — all operations within machine limit.")
    print()

    # Print TSC notes and substitutions
    notes_printed = set()
    for c in data.get('clusters', []):
        if c.get('feature_type') == 'background':
            continue
        for step in c.get('process_sequence', []):
            n = step.get('param_notes', '')
            if n and n not in notes_printed:
                if 'TSC' in n or 'capped' in n or 'WARNING' in n:
                    print(f"  Note C{c['cluster_id']:2d} {step['operation']:22s}: {n}")
                    notes_printed.add(n)


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def save_params(data: Dict, output_path: str):
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Parameters saved to: {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python parameter_calculation.py <tools_json> [output_json] "
              "[--max-rpm 10000] [--coolant through_spindle|flood|mist|dry]")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = None
    max_rpm     = MAX_SPINDLE_RPM
    coolant     = COOLANT

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '--max-rpm' and i + 1 < len(sys.argv):
            max_rpm = int(sys.argv[i + 1]); i += 2
        elif arg == '--coolant' and i + 1 < len(sys.argv):
            coolant = sys.argv[i + 1]; i += 2
        elif not arg.startswith('--') and output_path is None:
            output_path = arg; i += 1
        else:
            i += 1

    if output_path is None:
        output_path = input_path.replace('.json', '_params.json')

    with open(input_path) as f:
        data = json.load(f)

    result = calculate_parameters(data, max_rpm=max_rpm, coolant=coolant)
    print_param_summary(result)
    save_params(result, output_path)
