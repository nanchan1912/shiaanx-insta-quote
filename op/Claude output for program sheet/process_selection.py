"""
process_selection.py
--------------------
Process selection step. Takes the classified JSON output from
classify_features.py and assigns a manufacturing process sequence
to every feature cluster.

This answers the question:
  "Given this feature type and its dimensions, what sequence of
   machining operations is needed to produce it?"

The rules are encoded from:
  - Machinery's Handbook (29th ed.), Chapter 26 — Process Engineering
  - Chang & Wysk, "Introduction to Automated Process Planning Systems"
    (Prentice Hall), Chapters 4 and 5

Usage
-----
    python process_selection.py <classified_json> [output_json]

    python process_selection.py Hub_classified.json Hub_processes.json

Or from Python:
    from process_selection import select_processes
    result = select_processes(classified_data)

Output added to each cluster
-----------------------------
Each cluster gains two new fields:

    machine_type : str
        'milling'  — VMC or HMC (vertical/horizontal machining centre)
        'turning'  — lathe or turning centre
        'both'     — feature can be produced on either; sequence differs
        'none'     — background / planar stock face, no dedicated operation

    process_sequence : list of dicts
        Ordered list of operations to produce this feature.
        Each operation dict:
        {
            "step"        : int,        # 1-based execution order
            "operation"   : str,        # operation name (see vocabulary below)
            "machine"     : str,        # 'milling' or 'turning'
            "diameter_mm" : float,      # nominal tool diameter to use
            "depth_mm"    : float|None, # axial depth for this operation
            "drill_cycle" : str|None,   # 'standard'|'peck'|'deep_peck'|None
            "reason"      : str         # plain English explanation
        }

Operation Vocabulary
--------------------
Milling / drilling:
    spot_drill          Small conical drill to locate and guide subsequent drill.
                        Used before any twist drill on a milling machine.
    micro_drill         Twist drill for diameters < 1mm. No spot drill (would snap).
    twist_drill         Standard jobber drill for holes 1–13mm diameter.
    pilot_drill         Smaller drill run first to guide a larger drill (d > 13mm).
    core_drill          Large twist drill for finishing to size (d 13–32mm).
    boring_bar          Single-point boring tool for precision diameter control
                        or diameters > 32mm.
    circular_interp     End mill following a circular CNC path. Used for large
                        diameters (> 32mm) on a milling machine.
    counterbore_mill    End mill or dedicated counterbore cutter to open up the
                        entry of a stepped hole.
    contour_mill        End mill following the outside profile of a boss.
    face_mill           Face milling cutter for flat faces and large bore facing.
    reamer              Reaming tool to achieve H7/H8 tolerance after drilling.

Turning:
    face_turn           Facing pass to square the end of a turned feature.
    rough_turn          Roughing pass removing bulk material from OD.
    finish_turn         Finishing pass to final diameter and surface finish.
    rough_bore          Internal roughing pass on a lathe.
    finish_bore         Internal finishing pass on a lathe.

Drill Cycle Types (milling only)
---------------------------------
    standard    G81 — drill straight through in one pass.
                Used when DDR (depth/diameter ratio) ≤ 3.
    peck        G83 — drill in increments, retract to clear chips.
                Used when DDR is 3–5.
    deep_peck   G83 with small peck increment — for DDR > 5.
                Requires through-spindle coolant if available.

Diameter Breakpoints (Machinery's Handbook Ch.26, metric)
----------------------------------------------------------
    < 1mm   : micro_drill (spot drill omitted — too fragile)
    1–13mm  : spot_drill → twist_drill
    13–32mm : spot_drill → pilot_drill → core_drill
    > 32mm  : circular_interp (milling) or rough_bore + finish_bore (turning)

DDR Breakpoints (Chang & Wysk Ch.5)
--------------------------------------
    ≤ 3     : standard drill cycle
    3–5     : peck drill cycle
    > 5     : deep peck drill cycle
"""

import json
import sys
import copy
from typing import Dict, List, Tuple, Optional


# ---------------------------------------------------------------------------
# Thresholds — adjust here without touching logic
# ---------------------------------------------------------------------------

# *** MACHINE PREFERENCE ***
# Controls which process sequence is used as primary for features that can
# be produced on either a milling machine or a lathe (boss, large_bore).
#
# 'milling' — use milling/drilling sequence as primary.
#             Turning sequence is still generated and stored as
#             process_sequence_turning for reference.
#             Use this when your shop runs VMC / HMC.
#
# 'turning' — use turning sequence as primary.
#             Milling sequence stored as process_sequence_milling.
#             Use this when your shop runs a lathe or turning centre.
#
# 'both'    — generate both sequences, no primary designated.
#             Both are stored. Downstream (setup planning) decides.
#             Use this when building a general-purpose system.
#
# Features that can ONLY be done on one machine (through_hole, blind_hole,
# counterbore, planar_face) are unaffected by this setting — they always
# go to milling regardless.
PREFERRED_MACHINE = 'milling'   # <-- change this line only

# Drill diameter breakpoints (mm)
MICRO_DRILL_MAX_DIA   =  1.0   # below this: no spot drill, use micro drill
TWIST_DRILL_MAX_DIA   = 13.0   # 1–13mm: standard twist drill
CORE_DRILL_MAX_DIA    = 32.0   # 13–32mm: pilot + core drill
# above 32mm: boring bar (turning) or circular interpolation (milling)

# DDR (depth / diameter) breakpoints for drill cycle selection
DDR_STANDARD_MAX  = 3.0   # ≤ 3: standard cycle
DDR_PECK_MAX      = 5.0   # 3–5: peck cycle
# above 5: deep peck cycle

# Diameter above which a boss is better produced by turning than milling
BOSS_TURNING_MIN_DIA = 6.0   # mm — below this, end mill is preferred

# Stock to leave (mm) per material grade for roughing (RF) passes.
# 'xy' = radial/lateral stock, 'z' = axial stock.
# Finishing passes always use stock_to_leave_xy = 0 and stock_to_leave_z = 0.
#
# Values sourced from:
#   Machinery's Handbook 29th ed., §Milling Cutters — Roughing / Finishing allowances
#   Sandvik Coromant "Milling application guide" (aluminium grades)
MATERIAL_STOCK_TABLE = {
    # Aluminium alloys — 6xxx series (6061, 6063, 6082)
    'aluminium_6061':     {'xy': 0.1,  'z': 0.1},
    'aluminium_6063':     {'xy': 0.1,  'z': 0.1},
    'aluminium_6082':     {'xy': 0.1,  'z': 0.1},
    # Aluminium alloys — 7xxx series (7075, 7050)
    'aluminium_7075':     {'xy': 0.15, 'z': 0.1},
    'aluminium_7050':     {'xy': 0.15, 'z': 0.1},
    # Generic aluminium — used when grade is unspecified
    'aluminium':          {'xy': 0.1,  'z': 0.1},
    # Mild / low-carbon steel (e.g. EN3B, S235, 1018)
    'mild_steel':         {'xy': 0.2,  'z': 0.15},
    'steel':              {'xy': 0.2,  'z': 0.15},
    # Stainless steel (304, 316, 17-4PH)
    'stainless_steel':    {'xy': 0.25, 'z': 0.2},
    'stainless_steel_316':{'xy': 0.3,  'z': 0.2},
    # Titanium (Grade 5 / Ti-6Al-4V)
    'titanium':           {'xy': 0.15, 'z': 0.1},
    # Brass / copper alloys
    'brass':              {'xy': 0.1,  'z': 0.1},
}
STOCK_TO_LEAVE_DEFAULT = {'xy': 0.1, 'z': 0.1}

# Face mill: maximum axial depth of cut per pass (mm).
# If total feature depth exceeds this, a roughing pass is emitted first.
# Values are conservative — override per machine if spindle power allows more.
FACE_MILL_MAX_AP = {
    'aluminium':          2.0,
    'aluminium_6061':     2.0,
    'aluminium_6063':     2.0,
    'aluminium_6082':     2.0,
    'aluminium_7075':     1.5,
    'aluminium_7050':     1.5,
    'mild_steel':         1.0,
    'steel':              1.0,
    'stainless_steel':    0.5,
    'stainless_steel_316':0.5,
    'titanium':           0.5,
    'brass':              2.0,
}
FACE_MILL_MAX_AP_DEFAULT = 1.0

# Operations split into RF + FINISH (non-face-mill milling ops)
RF_SPLIT_OPS = {'contour_mill', 'pocket_mill', 'counterbore_mill'}

# Drilling operations — emitted as-is with pass_type = None
DRILL_OPS = {'spot_drill', 'micro_drill', 'twist_drill', 'pilot_drill',
             'core_drill', 'boring_bar', 'circular_interp', 'tap', 'reamer'}

# Feature types that support CORNER_R pass when internal_corner_radius is present.
# 'slot' and 'pocket' are not yet emitted by classify_features.py but are listed
# here so that CORNER_R logic activates automatically once those types are added.
CORNER_R_FEATURE_TYPES = {'slot', 'pocket', 'slot_angled', 'pocket_angled'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand_rf_passes(steps: List[Dict], material: str = 'aluminium',
                      cluster: Dict = None) -> List[Dict]:
    """
    Expand milling operations into rough + finish passes based on operation type.

    Rules applied per operation:

    contour_mill / pocket_mill / counterbore_mill  (RF_SPLIT_OPS)
        Always split into two passes:
          1. RF pass     — pass_type='RF',     stock_to_leave from MATERIAL_STOCK_TABLE
          2. FINISH pass — pass_type='FINISH',  stock_to_leave_xy/z = 0.0
          3. CORNER_R    — emitted only when cluster.internal_corner_radius exists
                           AND feature_type in CORNER_R_FEATURE_TYPES
                           AND corner_radius < implied tool radius from step diameter_mm

    face_mill
        Conditionally split based on depth vs single-pass capability:
          - If depth_mm is None OR depth_mm <= FACE_MILL_MAX_AP[material]:
              Single FINISH pass — depth is within one-pass capability, no rough needed.
          - If depth_mm > FACE_MILL_MAX_AP[material]:
              RF pass   — rough down to stock_to_leave above floor
              FINISH pass — spring pass to clear remaining stock

    spot_drill / twist_drill / tap / and all other DRILL_OPS
        Emitted unchanged with pass_type = None (no rough/finish concept for drilling).

    Any unrecognised operation
        Emitted unchanged (future-proof fallback).

    Step numbers are reassigned sequentially after all expansions.

    Parameters
    ----------
    steps    : list of operation dicts produced by _process_* functions
    material : material key — looked up in MATERIAL_STOCK_TABLE and FACE_MILL_MAX_AP
    cluster  : optional cluster dict — used for CORNER_R detection (§1b)
    """
    stock   = MATERIAL_STOCK_TABLE.get(material, STOCK_TO_LEAVE_DEFAULT)
    max_ap  = FACE_MILL_MAX_AP.get(material, FACE_MILL_MAX_AP_DEFAULT)
    expanded = []

    for step in steps:
        op    = step.get('operation', '')
        depth = step.get('depth_mm')

        # ----------------------------------------------------------------
        # Drilling / non-milling ops — pass through, mark pass_type = None
        # ----------------------------------------------------------------
        if op in DRILL_OPS:
            s = copy.copy(step)
            s['pass_type']         = None
            s['stock_to_leave_xy'] = None
            s['stock_to_leave_z']  = None
            expanded.append(s)

        # ----------------------------------------------------------------
        # Contour / pocket / counterbore — always RF + FINISH
        # ----------------------------------------------------------------
        elif op in RF_SPLIT_OPS:
            rough = copy.copy(step)
            rough['pass_type']         = 'RF'
            rough['stock_to_leave_xy'] = stock['xy']
            rough['stock_to_leave_z']  = stock['z']
            rough['reason']            = step['reason'] + ' — roughing pass (RF)'
            expanded.append(rough)

            finish = copy.copy(step)
            finish['pass_type']         = 'FINISH'
            finish['stock_to_leave_xy'] = 0.0
            finish['stock_to_leave_z']  = 0.0
            finish['reason']            = step['reason'] + ' — finishing pass'
            expanded.append(finish)

            # §1b: CORNER_R pass — emitted when the cluster has internal corner
            # radius data and the corner is tighter than the primary tool radius.
            # Condition: feature_type in CORNER_R_FEATURE_TYPES (slot, pocket)
            #            AND internal_corner_radius < step diameter_mm / 2
            # Note: slot/pocket feature types are not yet emitted by
            # classify_features.py; this triggers automatically once they are.
            if cluster is not None:
                ft       = cluster.get('feature_type', '')
                corner_r = cluster.get('internal_corner_radius')
                step_dia = step.get('diameter_mm') or 0
                if (corner_r is not None
                        and ft in CORNER_R_FEATURE_TYPES
                        and corner_r < step_dia / 2):
                    cr_step = copy.copy(step)
                    cr_step['pass_type']         = 'CORNER_R'
                    cr_step['stock_to_leave_xy'] = 0.0
                    cr_step['stock_to_leave_z']  = 0.0
                    # diameter_mm = max tool diameter that fits the corner
                    cr_step['diameter_mm']       = round(corner_r * 2, 4)
                    cr_step['reason']            = (
                        step['reason'] +
                        f' — corner rest machining (corner_r={corner_r}mm '
                        f'< primary_tool_r={round(step_dia/2, 4)}mm)'
                    )
                    expanded.append(cr_step)

        # ----------------------------------------------------------------
        # Face mill — split only when depth exceeds single-pass capability
        # ----------------------------------------------------------------
        elif op == 'face_mill':
            if depth is not None and depth > max_ap:
                # Depth exceeds one-pass limit — roughing pass required
                rough = copy.copy(step)
                rough['pass_type']         = 'RF'
                rough['stock_to_leave_xy'] = stock['xy']
                rough['stock_to_leave_z']  = stock['z']
                rough['reason']            = (step['reason'] +
                    f' — roughing pass (RF, depth={depth}mm > max_ap={max_ap}mm)')
                expanded.append(rough)

                finish = copy.copy(step)
                finish['pass_type']         = 'FINISH'
                finish['stock_to_leave_xy'] = 0.0
                finish['stock_to_leave_z']  = 0.0
                finish['reason']            = step['reason'] + ' — spring/finish pass'
                expanded.append(finish)
            else:
                # Depth within single-pass capability — one FINISH pass only
                single = copy.copy(step)
                single['pass_type']         = 'FINISH'
                single['stock_to_leave_xy'] = 0.0
                single['stock_to_leave_z']  = 0.0
                single['reason']            = step['reason'] + ' — single finish pass'
                expanded.append(single)

        # ----------------------------------------------------------------
        # Unknown / future operation types — emit unchanged
        # ----------------------------------------------------------------
        else:
            expanded.append(step)

    # Reassign step numbers sequentially after all expansions
    for i, s in enumerate(expanded, start=1):
        s['step'] = i

    return expanded


def _drill_cycle(ddr: Optional[float]) -> str:
    """Return the drill cycle name based on depth-to-diameter ratio."""
    if ddr is None or ddr <= DDR_STANDARD_MAX:
        return 'standard'
    elif ddr <= DDR_PECK_MAX:
        return 'peck'
    else:
        return 'deep_peck'


def _drilling_steps(diameter_mm: float, depth_mm: Optional[float],
                    ddr: Optional[float], start_step: int = 1) -> List[Dict]:
    """
    Return the ordered drilling operation steps for a given diameter.

    This function is called for every hole — through, blind, or the inner
    bore of a counterbore. It encodes the Machinery's Handbook diameter
    breakpoint rules.

    Parameters
    ----------
    diameter_mm : float  — nominal hole diameter
    depth_mm    : float  — axial depth (None if unknown)
    ddr         : float  — depth/diameter ratio (None if unknown)
    start_step  : int    — step number to start counting from
    """
    steps = []
    cycle = _drill_cycle(ddr)
    step  = start_step

    if diameter_mm < MICRO_DRILL_MAX_DIA:
        # Sub-1mm: no spot drill (it would snap the drill)
        steps.append({
            'step'        : step,
            'operation'   : 'micro_drill',
            'machine'     : 'milling',
            'diameter_mm' : round(diameter_mm, 4),
            'depth_mm'    : depth_mm,
            'drill_cycle' : cycle,
            'reason'      : (f'Hole d={diameter_mm:.3f}mm — micro drill, '
                             f'no spot drill (fragile at this size)')
        })
        step += 1

    elif diameter_mm <= TWIST_DRILL_MAX_DIA:
        # 1–13mm: spot drill to locate, then twist drill
        steps.append({
            'step'        : step,
            'operation'   : 'spot_drill',
            'machine'     : 'milling',
            'diameter_mm' : round(diameter_mm, 4),  # hole diameter being located — tool_selection picks actual spot drill size
            'depth_mm'    : None,
            'drill_cycle' : None,
            'reason'      : (f'Locate for d={diameter_mm:.3f}mm hole — '
                             f'prevents drill wandering')
        })
        step += 1
        steps.append({
            'step'        : step,
            'operation'   : 'twist_drill',
            'machine'     : 'milling',
            'diameter_mm' : round(diameter_mm, 4),
            'depth_mm'    : depth_mm,
            'drill_cycle' : cycle,
            'reason'      : (f'Drill to d={diameter_mm:.3f}mm '
                             f'depth={depth_mm}mm DDR={ddr} -> {cycle} cycle')
        })
        step += 1

    elif diameter_mm <= CORE_DRILL_MAX_DIA:
        # 13–32mm: spot drill, pilot drill at ~60% diameter, then core drill
        pilot_dia = round(diameter_mm * 0.6, 4)
        steps.append({
            'step'        : step,
            'operation'   : 'spot_drill',
            'machine'     : 'milling',
            'diameter_mm' : round(diameter_mm, 4),  # hole diameter being located — tool_selection picks actual spot drill size
            'depth_mm'    : None,
            'drill_cycle' : None,
            'reason'      : f'Locate for d={diameter_mm:.3f}mm hole'
        })
        step += 1
        steps.append({
            'step'        : step,
            'operation'   : 'pilot_drill',
            'machine'     : 'milling',
            'diameter_mm' : pilot_dia,
            'depth_mm'    : depth_mm,
            'drill_cycle' : cycle,
            'reason'      : (f'Pilot at d={pilot_dia}mm (60% of final) to '
                             f'guide core drill and reduce cutting force')
        })
        step += 1
        steps.append({
            'step'        : step,
            'operation'   : 'core_drill',
            'machine'     : 'milling',
            'diameter_mm' : round(diameter_mm, 4),
            'depth_mm'    : depth_mm,
            'drill_cycle' : cycle,
            'reason'      : f'Open to final d={diameter_mm:.3f}mm'
        })
        step += 1

    else:
        # > 32mm: boring bar on milling machine (circular interpolation
        # is an alternative but boring gives better tolerance)
        steps.append({
            'step'        : step,
            'operation'   : 'boring_bar',
            'machine'     : 'milling',
            'diameter_mm' : round(diameter_mm, 4),
            'depth_mm'    : depth_mm,
            'drill_cycle' : None,
            'reason'      : (f'Hole d={diameter_mm:.3f}mm > 32mm — '
                             f'boring bar for precision diameter')
        })
        step += 1

    return steps


# ---------------------------------------------------------------------------
# Per-feature-type process rules
# ---------------------------------------------------------------------------

def _process_through_hole(cluster: Dict) -> Tuple[str, List[Dict]]:
    """Drilling a hole that passes completely through the part."""
    radius    = cluster['radii'][0]
    diameter  = round(2 * radius, 4)
    depth     = cluster['depth']
    ddr       = round(depth / diameter, 3) if (depth and diameter) else None

    steps = _drilling_steps(diameter, depth, ddr, start_step=1)
    return 'milling', steps


def _process_blind_hole(cluster: Dict) -> Tuple[str, List[Dict]]:
    """Drilling to a depth that stops short of passing through."""
    radius    = cluster['radii'][0]
    diameter  = round(2 * radius, 4)
    depth     = cluster['depth']
    ddr       = round(depth / diameter, 3) if (depth and diameter) else None

    steps = _drilling_steps(diameter, depth, ddr, start_step=1)

    # For a blind hole, annotate the last step to clarify it stops at depth
    if steps:
        last = steps[-1]
        last['reason'] += ' — blind hole, stop at depth'

    return 'milling', steps


def _process_counterbore(cluster: Dict) -> Tuple[str, List[Dict]]:
    """
    Stepped hole: smallest radius = inner bore, larger radii = counterbore steps.

    Process:
      1. Drill the inner bore (smallest diameter) using standard drilling rules.
      2. For each larger diameter (sorted ascending), add a counterbore_mill step.
    """
    radii_sorted = sorted(cluster['radii'])   # ascending: smallest bore first
    inner_r      = radii_sorted[0]
    inner_dia    = round(2 * inner_r, 4)
    depth        = cluster['depth']
    ddr          = round(depth / inner_dia, 3) if (depth and inner_dia) else None

    steps = _drilling_steps(inner_dia, depth, ddr, start_step=1)
    next_step = len(steps) + 1

    # Add a counterbore_mill step for each larger radius
    # Counterbore depth is estimated as the difference in cylinder areas
    # divided by the circumference — a shallow shoulder cut
    for cb_radius in radii_sorted[1:]:
        cb_dia = round(2 * cb_radius, 4)
        steps.append({
            'step'        : next_step,
            'operation'   : 'counterbore_mill',
            'machine'     : 'milling',
            'diameter_mm' : cb_dia,
            'depth_mm'    : None,   # shoulder depth determined at tool selection
            'drill_cycle' : None,
            'reason'      : (f'Open counterbore step to d={cb_dia}mm '
                             f'(inner bore d={inner_dia}mm already drilled)')
        })
        next_step += 1

    return 'milling', steps


def _process_large_bore_milling(cluster: Dict) -> List[Dict]:
    """Large bore on a milling machine — circular interpolation."""
    max_radius = max(cluster['radii'])
    max_dia    = round(2 * max_radius, 4)
    depth      = cluster['depth']

    return [
        {
            'step'        : 1,
            'operation'   : 'face_mill',
            'machine'     : 'milling',
            'diameter_mm' : max_dia,
            'depth_mm'    : depth,
            'drill_cycle' : None,
            'reason'      : f'Face mill large bore area d={max_dia}mm'
        },
        {
            'step'        : 2,
            'operation'   : 'circular_interp',
            'machine'     : 'milling',
            'diameter_mm' : max_dia,
            'depth_mm'    : depth,
            'drill_cycle' : None,
            'reason'      : (f'Circular interpolation with end mill to reach '
                             f'd={max_dia}mm — diameter too large for drill')
        }
    ]


def _process_large_bore_turning(cluster: Dict) -> List[Dict]:
    """Large bore on a lathe — face then bore to size."""
    min_dia = round(2 * min(cluster['radii']), 4)
    max_dia = round(2 * max(cluster['radii']), 4)
    depth   = cluster['depth']

    return [
        {
            'step'        : 1,
            'operation'   : 'face_turn',
            'machine'     : 'turning',
            'diameter_mm' : max_dia,
            'depth_mm'    : None,
            'drill_cycle' : None,
            'reason'      : 'Face to clean entry plane before boring'
        },
        {
            'step'        : 2,
            'operation'   : 'rough_bore',
            'machine'     : 'turning',
            'diameter_mm' : round(min_dia * 0.95, 4),   # rough leaves 5% for finish
            'depth_mm'    : depth,
            'drill_cycle' : None,
            'reason'      : (f'Rough bore to ~95% of final diameter '
                             f'd≈{round(min_dia*0.95,2)}mm')
        },
        {
            'step'        : 3,
            'operation'   : 'finish_bore',
            'machine'     : 'turning',
            'diameter_mm' : round(max_dia, 4),
            'depth_mm'    : depth,
            'drill_cycle' : None,
            'reason'      : f'Finish bore to final d={max_dia}mm'
        }
    ]


def _process_boss_milling(cluster: Dict) -> List[Dict]:
    """Boss (protruding cylinder) on a milling machine — contour mill."""
    radius   = cluster['radii'][0]
    diameter = round(2 * radius, 4)
    depth    = cluster['depth']

    return [
        {
            'step'        : 1,
            'operation'   : 'contour_mill',
            'machine'     : 'milling',
            'diameter_mm' : diameter,
            'depth_mm'    : depth,
            'drill_cycle' : None,
            'reason'      : (f'Contour mill end mill around boss OD '
                             f'd={diameter}mm h={depth}mm')
        }
    ]


def _process_boss_turning(cluster: Dict) -> List[Dict]:
    """Boss on a lathe — rough and finish turn OD."""
    radius   = cluster['radii'][0]
    diameter = round(2 * radius, 4)
    depth    = cluster['depth']

    return [
        {
            'step'        : 1,
            'operation'   : 'rough_turn',
            'machine'     : 'turning',
            'diameter_mm' : round(diameter * 1.05, 4),  # rough slightly oversize
            'depth_mm'    : depth,
            'drill_cycle' : None,
            'reason'      : f'Rough turn boss OD, leave 5% for finish pass'
        },
        {
            'step'        : 2,
            'operation'   : 'finish_turn',
            'machine'     : 'turning',
            'diameter_mm' : diameter,
            'depth_mm'    : depth,
            'drill_cycle' : None,
            'reason'      : f'Finish turn to final OD d={diameter}mm'
        }
    ]


def _process_planar_face(cluster: Dict) -> Tuple[str, List[Dict]]:
    """Flat face — face milling on a milling machine."""
    return 'milling', [
        {
            'step'        : 1,
            'operation'   : 'face_mill',
            'machine'     : 'milling',
            'diameter_mm' : None,   # face mill cutter size chosen at tool selection
            'depth_mm'    : None,
            'drill_cycle' : None,
            'reason'      : 'Face mill flat datum/planar surface'
        }
    ]


# ---------------------------------------------------------------------------
# Angled feature note
# ---------------------------------------------------------------------------

def _add_angled_note(steps: List[Dict], axis: List[float]) -> List[Dict]:
    """
    Prepend a fixture setup step to any angled feature's process sequence.
    This tells the setup planner the part must be rotated before this feature
    can be machined.
    """
    setup_step = {
        'step'        : 0,          # step 0 = setup, before machining steps
        'operation'   : 'fixture_rotation',
        'machine'     : 'milling',
        'diameter_mm' : None,
        'depth_mm'    : None,
        'drill_cycle' : None,
        'reason'      : (f'Feature axis {[round(x,3) for x in axis]} is not '
                         f'aligned to a principal axis — part must be rotated '
                         f'or an angled fixture used before drilling')
    }
    # Re-number existing steps to start at 1 after the setup step
    for s in steps:
        s['step'] += 1
    return [setup_step] + steps


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def select_process(cluster: Dict, machine_preference: str = None,
                   material: str = 'aluminium') -> Dict:
    """
    Select the process sequence for a single classified cluster.

    Parameters
    ----------
    cluster            : dict — one classified cluster
    machine_preference : str  — 'milling', 'turning', or 'both'.
                                Overrides the PREFERRED_MACHINE file setting
                                when provided. Controls which sequence is
                                primary for features that can go on either
                                machine (boss, large_bore).

    Returns the cluster dict with these new fields added:
        machine_type           : str  — 'milling' | 'turning' | 'both' | 'none'
        machine_selected       : str  — which machine was actually chosen and why
        process_sequence       : list — primary (chosen) operation sequence
        process_sequence_turning  : list — turning alternative (if applicable)
        process_sequence_milling  : list — milling alternative (if applicable)
    """
    preference = machine_preference or PREFERRED_MACHINE
    ft      = cluster.get('feature_type', 'unknown')
    is_ang  = ft.endswith('_angled')
    axis    = cluster.get('feature_axis') or []

    machine_type     = 'none'
    machine_selected = 'none'
    process_sequence = []

    # ------------------------------------------------------------------
    # Non-machined features
    # ------------------------------------------------------------------
    if ft == 'background':
        machine_type     = 'none'
        machine_selected = 'not a machined feature'
        process_sequence = []

    elif ft == 'planar_face':
        machine_type, process_sequence = _process_planar_face(cluster)
        machine_selected = 'milling — only option for planar faces'

    # ------------------------------------------------------------------
    # Bore family — always milling/drilling regardless of preference
    # ------------------------------------------------------------------
    elif ft in ('through_hole', 'through_hole_angled'):
        machine_type, process_sequence = _process_through_hole(cluster)
        machine_selected = 'milling — drilling is milling-machine operation'
        if is_ang:
            process_sequence = _add_angled_note(process_sequence, axis)

    elif ft in ('blind_hole', 'blind_hole_angled'):
        machine_type, process_sequence = _process_blind_hole(cluster)
        machine_selected = 'milling — drilling is milling-machine operation'
        if is_ang:
            process_sequence = _add_angled_note(process_sequence, axis)

    elif ft in ('counterbore', 'counterbore_angled'):
        machine_type, process_sequence = _process_counterbore(cluster)
        machine_selected = 'milling — drilling is milling-machine operation'
        if is_ang:
            process_sequence = _add_angled_note(process_sequence, axis)

    # ------------------------------------------------------------------
    # Large bore — preference decides primary sequence
    # ------------------------------------------------------------------
    elif ft == 'large_bore':
        milling_seq = _process_large_bore_milling(cluster)
        turning_seq = _process_large_bore_turning(cluster)

        if preference == 'milling':
            machine_type     = 'milling'
            machine_selected = 'milling — preferred machine (PREFERRED_MACHINE=milling)'
            process_sequence = milling_seq
            cluster['process_sequence_turning'] = turning_seq

        elif preference == 'turning':
            machine_type     = 'turning'
            machine_selected = 'turning — preferred machine (PREFERRED_MACHINE=turning)'
            process_sequence = turning_seq
            cluster['process_sequence_milling'] = milling_seq

        else:  # 'both'
            machine_type     = 'both'
            machine_selected = 'both sequences generated — setup planning will decide'
            process_sequence = milling_seq
            cluster['process_sequence_turning'] = turning_seq

    # ------------------------------------------------------------------
    # Boss — preference decides primary sequence
    # ------------------------------------------------------------------
    elif ft in ('boss', 'boss_angled'):
        milling_seq = _process_boss_milling(cluster)
        turning_seq = _process_boss_turning(cluster)

        if is_ang:
            milling_seq = _add_angled_note(milling_seq, axis)

        if preference == 'milling':
            machine_type     = 'milling'
            machine_selected = 'milling — preferred machine (PREFERRED_MACHINE=milling)'
            process_sequence = milling_seq
            cluster['process_sequence_turning'] = turning_seq

        elif preference == 'turning':
            machine_type     = 'turning'
            machine_selected = 'turning — preferred machine (PREFERRED_MACHINE=turning)'
            process_sequence = turning_seq
            cluster['process_sequence_milling'] = milling_seq

        else:  # 'both'
            machine_type     = 'both'
            machine_selected = 'both sequences generated — setup planning will decide'
            process_sequence = milling_seq
            cluster['process_sequence_turning'] = turning_seq

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------
    else:
        machine_type     = 'unknown'
        machine_selected = f'no rule for feature type "{ft}"'
        process_sequence = [{
            'step'        : 1,
            'operation'   : 'manual_review',
            'machine'     : 'unknown',
            'diameter_mm' : None,
            'depth_mm'    : None,
            'drill_cycle' : None,
            'reason'      : f'Feature type "{ft}" has no process rule — review manually'
        }]

    # Expand milling ops into RF + FINISH passes (and CORNER_R if applicable — §1b)
    if machine_type == 'milling':
        process_sequence = _expand_rf_passes(process_sequence, material=material,
                                             cluster=cluster)

    # §1c: Through-feature tagging — propagate 'through' flag from cluster to
    # each operation step. Downstream modules (parameter_calculation, program_sheet)
    # can use this to apply different strategies for through vs blind features.
    if cluster.get('through', False):
        for s in process_sequence:
            s['through'] = True

    cluster['machine_type']      = machine_type
    cluster['machine_selected']  = machine_selected
    cluster['process_sequence']  = process_sequence
    return cluster


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def select_processes(classified_data: Dict,
                     machine_preference: str = None,
                     material: str = None) -> Dict:
    """
    Apply process selection to all clusters in the classified JSON.

    Parameters
    ----------
    classified_data    : dict — parsed JSON from classify_features.py
    machine_preference : str  — 'milling', 'turning', or 'both'.
                                Overrides PREFERRED_MACHINE file setting.
                                If None, uses PREFERRED_MACHINE.
    material           : str  — material key for stock-to-leave lookup
                                (e.g. 'aluminium', 'steel', 'titanium').
                                If None, reads from classified_data['material']
                                or defaults to 'aluminium'.

    Returns a new dict with machine_type, machine_selected, and
    process_sequence added to every cluster.
    """
    mat = material or classified_data.get('material', 'aluminium')
    result = copy.deepcopy(classified_data)
    for cluster in result['clusters']:
        select_process(cluster, machine_preference=machine_preference,
                       material=mat)
    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_process_summary(data: Dict, effective_preference: str = None):
    """Print a human-readable summary of process selection results."""
    clusters = data['clusters']
    pref     = effective_preference or PREFERRED_MACHINE
    print(f"Process selection summary — {len(clusters)} clusters")
    print(f"Machine preference: {pref}\n")

    for c in clusters:
        ft   = c.get('feature_type', '?')
        mt   = c.get('machine_type', '?')
        sel  = c.get('machine_selected', '')
        seq  = c.get('process_sequence', [])
        cid  = c['cluster_id']

        if ft == 'background':
            continue

        ops = ' -> '.join(s['operation'] for s in seq)
        print(f"  C{cid:2d} | {ft:22s} | chosen={mt:8s} | {ops}")

        # Print the alternative sequence if it exists
        for alt_key, alt_label in [
            ('process_sequence_turning', 'turning alt'),
            ('process_sequence_milling', 'milling alt'),
        ]:
            alt_seq = c.get(alt_key)
            if alt_seq:
                alt_ops = ' -> '.join(s['operation'] for s in alt_seq)
                print(f"      ({alt_label:12s})          |          | {alt_ops}")

    print()


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def save_processes(data: Dict, output_path: str):
    """Save process selection results to a JSON file."""
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Process selection saved to: {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python process_selection.py <classified_json> [output_json] [--machine milling|turning|both]")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path  = None
    cli_machine  = None
    cli_material = None

    for i, arg in enumerate(sys.argv[2:], start=2):
        if arg == '--machine' and i + 1 < len(sys.argv):
            cli_machine = sys.argv[i + 1]
        elif arg == '--material' and i + 1 < len(sys.argv):
            cli_material = sys.argv[i + 1]
        elif not arg.startswith('--') and output_path is None:
            output_path = arg

    if output_path is None:
        output_path = input_path.replace('.json', '_processes.json')

    if cli_machine and cli_machine not in ('milling', 'turning', 'both'):
        print(f"Error: --machine must be 'milling', 'turning', or 'both'. Got: {cli_machine}")
        sys.exit(1)

    if cli_material and cli_material not in MATERIAL_STOCK_TABLE:
        known = ', '.join(sorted(MATERIAL_STOCK_TABLE))
        print(f"Warning: --material '{cli_material}' not in table. Known: {known}. Using default stock values.")

    with open(input_path) as f:
        data = json.load(f)

    result = select_processes(data, machine_preference=cli_machine,
                              material=cli_material)
    effective = cli_machine or PREFERRED_MACHINE
    print_process_summary(result, effective_preference=effective)
    save_processes(result, output_path)
