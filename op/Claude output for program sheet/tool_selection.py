"""
tool_selection.py
-----------------
Tool selection step. Takes the setup-planned JSON from setup_planning.py
and assigns a specific cutting tool to every operation in every cluster.

This answers the question:
  "For each machining operation on each feature, which exact tool should
   be used — manufacturer, grade, diameter, geometry?"

Approach
--------
Rule-based lookup against tool_database.json:

  Step 1 — For each operation step in each cluster's process_sequence,
            determine the ACTUAL tool diameter needed.
            This differs from the stored diameter in several cases:
            - spot_drill: find smallest standard spot drill whose
              locates_holes_up_to_mm >= required diameter
            - twist_drill: find nearest standard drill size. Flag if
              requested size is non-standard.
            - circular_interp: stored diameter is the BORE diameter.
              Tool is an end mill at ~50% of bore diameter, rounded
              to nearest standard end mill size.
            - contour_mill: stored diameter is the FEATURE (boss) diameter.
              Tool is an end mill smaller than the feature.
            - counterbore_mill: stored diameter is the COUNTERBORE diameter.
              Tool is an end mill that fits inside the counterbore.
            - face_mill: stored diameter is the feature area diameter.
              Tool is a face mill cutter wider than the feature.

  Step 2 — Query the database:
            Match operation × resolved_tool_diameter × material
            → return best tool entry + material_params

  Step 3 — Add the tool assignment to each operation step dict.

Output added to each operation step
-------------------------------------
Each step dict gains:

    tool_id          : str    — database tool_id
    tool_description : str    — human-readable description
    tool_diameter_mm : float  — actual tool diameter to use
    manufacturer     : str
    product_line     : str
    grade            : str
    Vc_mmin          : float  — recommended surface speed
    feed_per_rev_mm  : float|None  — for drills
    fz_mm            : float|None  — feed per tooth (for mills)
    tool_notes       : str    — any warnings or substitution notes

Usage
-----
    python tool_selection.py <setups_json> [output_json] [--material aluminium]

    python tool_selection.py Hub_setups.json Hub_tools.json --material aluminium

Or from Python:
    from tool_selection import select_tools
    result = select_tools(setups_data, material='aluminium')
"""

import json
import sys
import copy
import math
import os
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default material if not specified
DEFAULT_MATERIAL = 'aluminium'

# Path to tool database — same directory as this script
_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'tool_database.json')

# For circular interpolation: tool diameter = bore_diameter * this fraction
# Then round to nearest standard end mill size.
CIRC_INTERP_TOOL_FRACTION = 0.45

# For contour_mill (boss): tool diameter = boss_diameter * this fraction
# End mill should be smaller than the boss to allow tool path around it.
CONTOUR_MILL_TOOL_FRACTION = 0.80

# Tolerance for "exact" diameter match on drills (mm)
DRILL_EXACT_TOL = 0.01


# ---------------------------------------------------------------------------
# Database loader
# ---------------------------------------------------------------------------

_DB_CACHE = None

def load_database(db_path: str = None) -> Dict:
    """Load and cache the tool database JSON."""
    global _DB_CACHE
    if _DB_CACHE is not None:
        return _DB_CACHE
    path = db_path or _DB_PATH
    with open(path) as f:
        _DB_CACHE = json.load(f)
    return _DB_CACHE


def _resolve_material(material: str, db: Dict) -> str:
    """Resolve material aliases to canonical name."""
    aliases = db.get('material_aliases', {})
    return aliases.get(material.lower(), material.lower())


# ---------------------------------------------------------------------------
# Tool diameter resolution logic
# ---------------------------------------------------------------------------
# These functions answer: "given what process_selection stored as diameter_mm,
# what is the ACTUAL tool diameter we need from the database?"

def _resolve_spot_drill_diameter(required_dia: float, db: Dict) -> Tuple[float, str]:
    """
    Find the smallest spot drill whose locates_holes_up_to_mm >= required_dia.
    Returns (tool_diameter, note).
    """
    candidates = [
        t for t in db['tools']
        if t.get('operation') == 'spot_drill'
        and t.get('locates_holes_up_to_mm', 0) >= required_dia
    ]
    if not candidates:
        # Fall back to largest available spot drill
        all_spots = [t for t in db['tools'] if t.get('operation') == 'spot_drill']
        if all_spots:
            best = max(all_spots, key=lambda t: t['diameter_mm'])
            return best['diameter_mm'], f'WARNING: no spot drill covers dia={required_dia}mm, using largest available {best["diameter_mm"]}mm'
        return required_dia, f'WARNING: no spot drill found in database for dia={required_dia}mm'

    best = min(candidates, key=lambda t: t['diameter_mm'])
    note = ''
    if abs(best['diameter_mm'] - required_dia) > 0.1:
        note = (f'Spot drill {best["diameter_mm"]}mm used (locates holes up to '
                f'{best["locates_holes_up_to_mm"]}mm — covers required {required_dia}mm)')
    return best['diameter_mm'], note


def _resolve_twist_drill_diameter(required_dia: float, db: Dict) -> Tuple[float, str]:
    """
    Find exact match or nearest standard drill size.
    Returns (tool_diameter, note).
    """
    standard_sizes = db.get('standard_drill_sizes_mm', [])
    note = ''

    # Check if exact match exists in database
    exact = [
        t for t in db['tools']
        if t.get('operation') == 'twist_drill'
        and abs(t['diameter_mm'] - required_dia) <= DRILL_EXACT_TOL
    ]
    if exact:
        return exact[0]['diameter_mm'], ''

    # Find nearest standard size
    if standard_sizes:
        nearest = min(standard_sizes, key=lambda s: abs(s - required_dia))
        if abs(nearest - required_dia) > DRILL_EXACT_TOL:
            note = (f'SUBSTITUTION: {required_dia}mm is non-standard. '
                    f'Using nearest standard size {nearest}mm. '
                    f'Verify hole tolerance before confirming.')
        return nearest, note

    return required_dia, f'WARNING: could not resolve drill size {required_dia}mm'


def _resolve_endmill_for_bore(bore_dia: float, db: Dict) -> Tuple[float, str]:
    """
    For circular_interp: find end mill at ~45% of bore diameter,
    rounded to nearest standard end mill size smaller than the bore.
    Returns (tool_diameter, note).
    """
    target = bore_dia * CIRC_INTERP_TOOL_FRACTION
    standard_sizes = db.get('standard_endmill_sizes_mm', [])

    # Must be smaller than bore diameter
    candidates = [s for s in standard_sizes if s < bore_dia]
    if not candidates:
        return 1.0, f'WARNING: no end mill found smaller than bore dia={bore_dia}mm'

    best = min(candidates, key=lambda s: abs(s - target))
    note = (f'Bore dia={bore_dia}mm — using {best}mm end mill for circular '
            f'interpolation (~{round(best/bore_dia*100)}% of bore diameter)')
    return best, note


def _resolve_endmill_for_contour(feature_dia: float, db: Dict) -> Tuple[float, str]:
    """
    For contour_mill (boss): find end mill smaller than the boss.
    Target = boss_diameter * CONTOUR_MILL_TOOL_FRACTION, rounded down to
    nearest standard size.
    Returns (tool_diameter, note).
    """
    target = feature_dia * CONTOUR_MILL_TOOL_FRACTION
    standard_sizes = db.get('standard_endmill_sizes_mm', [])

    # Must be strictly smaller than feature diameter
    candidates = [s for s in standard_sizes if s < feature_dia]
    if not candidates:
        # Feature too small — use smallest available end mill
        all_ems = [t for t in db['tools']
                   if 'contour_mill' in (t.get('operation') or [])]
        if all_ems:
            smallest = min(all_ems, key=lambda t: t['diameter_mm'])
            return smallest['diameter_mm'], \
                   f'Boss dia={feature_dia}mm very small — using smallest end mill {smallest["diameter_mm"]}mm'
        return 1.0, f'WARNING: no end mill found for boss dia={feature_dia}mm'

    best = min(candidates, key=lambda s: abs(s - target))
    note = (f'Boss dia={feature_dia}mm — using {best}mm end mill to contour '
            f'(~{round(best/feature_dia*100)}% of feature diameter)')
    return best, note


def _resolve_endmill_for_counterbore(cb_dia: float, db: Dict) -> Tuple[float, str]:
    """
    For counterbore_mill: find end mill that fits inside the counterbore.
    Target = cb_diameter (the tool fills the counterbore step exactly).
    Prefer end mill = cb_diameter. If not available, use largest that fits.
    Returns (tool_diameter, note).
    """
    standard_sizes = db.get('standard_endmill_sizes_mm', [])

    # Prefer exact match
    exact_candidates = [s for s in standard_sizes if abs(s - cb_dia) <= 0.05]
    if exact_candidates:
        return exact_candidates[0], ''

    # Otherwise use largest end mill that fits inside the counterbore
    candidates = [s for s in standard_sizes if s <= cb_dia]
    if candidates:
        best = max(candidates)
        note = (f'Counterbore dia={cb_dia}mm — using {best}mm end mill '
                f'(largest standard size that fits)')
        return best, note

    return 1.0, f'WARNING: no end mill found for counterbore dia={cb_dia}mm'


def _resolve_endmill_for_corner_r(max_dia: float, db: Dict) -> Tuple[float, str]:
    """
    For CORNER_R pass: find the largest end mill whose diameter <= max_dia.
    max_dia = 2 × internal_corner_radius (the largest tool that still fits the corner).
    Largest-that-fits = most rigid = best surface quality in the corner.
    Returns (tool_diameter, note).
    """
    standard_sizes = db.get('standard_endmill_sizes_mm', [])

    candidates = [s for s in standard_sizes if s <= max_dia]
    if candidates:
        best = max(candidates)
        note = (f'CORNER_R: max tool dia={max_dia}mm — using {best}mm end mill '
                f'(largest that fits corner)')
        return best, note

    # No standard size fits — use smallest available
    all_sizes = sorted(standard_sizes)
    if all_sizes:
        best = all_sizes[0]
        return best, f'WARNING: no end mill <= {max_dia}mm — using smallest {best}mm'

    return 1.0, f'WARNING: no end mill sizes in database for CORNER_R d<={max_dia}mm'


def _resolve_facemill_diameter(feature_dia: Optional[float], db: Dict) -> Tuple[float, str]:
    """
    For face_mill: find smallest face mill cutter larger than feature diameter.
    Returns (tool_diameter, note).
    """
    face_mills = [t for t in db['tools'] if t.get('operation') == 'face_mill']
    if not face_mills:
        return 50.0, 'WARNING: no face mill in database, defaulting to 50mm'

    if feature_dia is None:
        # Planar face with unknown extent — use smallest face mill
        best = min(face_mills, key=lambda t: t['diameter_mm'])
        return best['diameter_mm'], 'Feature area unknown — using smallest available face mill'

    # Find smallest face mill larger than feature diameter
    candidates = [t for t in face_mills if t['diameter_mm'] >= feature_dia]
    if candidates:
        best = min(candidates, key=lambda t: t['diameter_mm'])
        note = f'Face mill {best["diameter_mm"]}mm covers feature dia={feature_dia}mm'
        return best['diameter_mm'], note

    # Feature larger than all face mills — use largest, make multiple passes
    best = max(face_mills, key=lambda t: t['diameter_mm'])
    note = (f'Feature dia={feature_dia}mm larger than largest face mill '
            f'{best["diameter_mm"]}mm — multiple passes required')
    return best['diameter_mm'], note


# ---------------------------------------------------------------------------
# Database query
# ---------------------------------------------------------------------------

def _query_tool(operation: str, tool_diameter_mm: float,
                material: str, db: Dict) -> Optional[Dict]:
    """
    Find the best matching tool in the database for a given
    operation + diameter + material.

    Matching logic:
    1. operation must match (string or list)
    2. diameter must be within DRILL_EXACT_TOL for drills,
       or >= required for mills (use smallest that fits)
    3. material_params must contain the requested material
    """
    material = _resolve_material(material, db)

    candidates = []
    for tool in db['tools']:
        # Check operation match
        tool_op = tool.get('operation', '')
        if isinstance(tool_op, list):
            op_match = operation in tool_op
        else:
            op_match = tool_op == operation
        if not op_match:
            continue

        # Check material support
        if material not in tool.get('material_params', {}):
            continue

        candidates.append(tool)

    if not candidates:
        return None

    # For drills and spot drills: find closest diameter match
    if operation in ('twist_drill', 'micro_drill', 'pilot_drill',
                     'core_drill', 'spot_drill', 'boring_bar'):
        exact = [t for t in candidates
                 if abs(t['diameter_mm'] - tool_diameter_mm) <= DRILL_EXACT_TOL]
        if exact:
            return exact[0]
        # Nearest size
        return min(candidates, key=lambda t: abs(t['diameter_mm'] - tool_diameter_mm))

    # For mills (end mills, face mills): find smallest tool >= required diameter
    fitting = [t for t in candidates
               if t['diameter_mm'] >= tool_diameter_mm - DRILL_EXACT_TOL]
    if fitting:
        return min(fitting, key=lambda t: t['diameter_mm'])

    # Fallback: nearest
    return min(candidates, key=lambda t: abs(t['diameter_mm'] - tool_diameter_mm))


# ---------------------------------------------------------------------------
# Per-operation tool assignment
# ---------------------------------------------------------------------------

def _assign_tool_to_step(step: Dict, cluster: Dict,
                          material: str, db: Dict) -> Dict:
    """
    Assign a tool to a single operation step dict.
    Returns the step dict with tool fields added.
    """
    op       = step.get('operation', '')
    req_dia  = step.get('diameter_mm')   # what process_selection stored
    depth    = step.get('depth_mm')

    # --- Steps that need no tool ---
    if op in ('fixture_rotation', 'manual_review'):
        step['tool_id']          = None
        step['tool_description'] = 'No tool — setup/operator action required'
        step['tool_diameter_mm'] = None
        step['manufacturer']     = None
        step['product_line']     = None
        step['grade']            = None
        step['Vc_mmin']          = None
        step['feed_per_rev_mm']  = None
        step['fz_mm']            = None
        step['tool_notes']       = ''
        return step

    tool_notes = ''

    # --- CORNER_R pass: select smallest tool that fits the corner radius ---
    # req_dia is 2 × internal_corner_radius set by process_selection §1b.
    if step.get('pass_type') == 'CORNER_R':
        corner_tool_dia, note = _resolve_endmill_for_corner_r(req_dia or 0, db)
        tool_notes += note
        db_tool = _query_tool('contour_mill', corner_tool_dia, material, db)
        if db_tool is None:
            step['tool_id']          = 'NOT_FOUND'
            step['tool_description'] = f'No end mill found for CORNER_R d<={req_dia}mm'
            step['tool_diameter_mm'] = corner_tool_dia
            step['manufacturer']     = None
            step['product_line']     = None
            step['grade']            = None
            step['Vc_mmin']          = None
            step['feed_per_rev_mm']  = None
            step['fz_mm']            = None
            step['tool_notes']       = f'ADD TO DATABASE: endmill d<={req_dia}mm {material}. ' + tool_notes
            return step
        mat_params = db_tool['material_params'][_resolve_material(material, db)]
        step['tool_id']          = db_tool['tool_id']
        step['tool_description'] = db_tool['description']
        step['tool_diameter_mm'] = db_tool['diameter_mm']
        step['manufacturer']     = db_tool.get('manufacturer', '')
        step['product_line']     = db_tool.get('product_line', '')
        step['grade']            = db_tool.get('grade', '')
        step['Vc_mmin']          = mat_params.get('Vc_mmin')
        step['feed_per_rev_mm']  = mat_params.get('feed_per_rev_mm')
        step['fz_mm']            = mat_params.get('fz_mm')
        step['tool_notes']       = (tool_notes + ' ' + mat_params.get('notes', '')).strip()
        return step

    # --- Resolve actual tool diameter ---
    if op == 'spot_drill':
        tool_dia, note = _resolve_spot_drill_diameter(req_dia or 0, db)
        tool_notes += note

    elif op in ('twist_drill', 'micro_drill', 'pilot_drill', 'core_drill'):
        tool_dia, note = _resolve_twist_drill_diameter(req_dia or 0, db)
        tool_notes += note

    elif op == 'circular_interp':
        # req_dia is the BORE diameter — derive tool diameter
        tool_dia, note = _resolve_endmill_for_bore(req_dia or 10, db)
        tool_notes += note

    elif op == 'contour_mill':
        # req_dia is the BOSS diameter — derive tool diameter
        tool_dia, note = _resolve_endmill_for_contour(req_dia or 1, db)
        tool_notes += note

    elif op == 'counterbore_mill':
        # req_dia is the COUNTERBORE diameter
        tool_dia, note = _resolve_endmill_for_counterbore(req_dia or 1, db)
        tool_notes += note

    elif op == 'face_mill':
        # req_dia may be None (planar face) or feature diameter
        tool_dia, note = _resolve_facemill_diameter(req_dia, db)
        tool_notes += note

    elif op == 'boring_bar':
        tool_dia = req_dia or 0
        tool_notes = 'Single-point boring bar — set to final diameter'

    else:
        tool_dia = req_dia or 0
        tool_notes = f'No diameter resolution rule for operation "{op}"'

    # --- Query database ---
    db_tool = _query_tool(op, tool_dia, material, db)

    if db_tool is None:
        step['tool_id']          = 'NOT_FOUND'
        step['tool_description'] = f'No tool found for {op} d={tool_dia}mm {material}'
        step['tool_diameter_mm'] = tool_dia
        step['manufacturer']     = None
        step['product_line']     = None
        step['grade']            = None
        step['Vc_mmin']          = None
        step['feed_per_rev_mm']  = None
        step['fz_mm']            = None
        step['tool_notes']       = f'ADD TO DATABASE: {op} d={tool_dia}mm {material}. ' + tool_notes
        return step

    mat_params = db_tool['material_params'][_resolve_material(material, db)]

    step['tool_id']          = db_tool['tool_id']
    step['tool_description'] = db_tool['description']
    step['tool_diameter_mm'] = db_tool['diameter_mm']
    step['manufacturer']     = db_tool.get('manufacturer', '')
    step['product_line']     = db_tool.get('product_line', '')
    step['grade']            = db_tool.get('grade', '')
    step['Vc_mmin']          = mat_params.get('Vc_mmin')
    step['feed_per_rev_mm']  = mat_params.get('feed_per_rev_mm')
    step['fz_mm']            = mat_params.get('fz_mm')
    step['tool_notes']       = (tool_notes + ' ' + mat_params.get('notes', '')).strip()

    return step


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def select_tools(setups_data: Dict,
                 material: str = DEFAULT_MATERIAL,
                 db_path: str = None) -> Dict:
    """
    Assign tools to every operation in every cluster.

    Parameters
    ----------
    setups_data : dict — parsed JSON from setup_planning.py
    material    : str  — workpiece material (e.g. 'aluminium', 'mild_steel')
    db_path     : str  — path to tool_database.json (defaults to same directory)

    Returns
    -------
    dict — copy of setups_data with tool fields added to every operation step.
           Also adds 'material' and 'tool_database_version' at top level.
    """
    db     = load_database(db_path)
    result = copy.deepcopy(setups_data)

    result['material']              = material
    result['tool_database_version'] = db.get('version', 'unknown')

    for cluster in result.get('clusters', []):
        ft = cluster.get('feature_type', '')
        if ft == 'background':
            continue
        for step in cluster.get('process_sequence', []):
            _assign_tool_to_step(step, cluster, material, db)

        # Also assign tools to turning sequence if present
        for seq_key in ('process_sequence_turning', 'process_sequence_milling'):
            for step in cluster.get(seq_key, []):
                _assign_tool_to_step(step, cluster, material, db)

    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_tool_summary(data: Dict):
    """Print a human-readable summary of tool assignments."""
    material = data.get('material', 'unknown')
    print(f"Tool selection summary — material: {material}\n")

    # Collect unique tools used
    tools_used = {}   # tool_id -> {description, diameter, count}
    warnings   = []

    clusters = data.get('clusters', [])
    for c in clusters:
        ft = c.get('feature_type', '')
        if ft == 'background':
            continue

        cid = c['cluster_id']
        for step in c.get('process_sequence', []):
            op  = step['operation']
            tid = step.get('tool_id')
            if not tid or op in ('fixture_rotation',):
                continue

            if tid not in tools_used:
                tools_used[tid] = {
                    'description' : step.get('tool_description', ''),
                    'diameter_mm' : step.get('tool_diameter_mm'),
                    'operations'  : set(),
                    'count'       : 0,
                }
            tools_used[tid]['operations'].add(op)
            tools_used[tid]['count'] += 1

            notes = step.get('tool_notes', '')
            if notes and ('WARNING' in notes or 'SUBSTITUTION' in notes):
                warnings.append(f'  C{cid:2d} {op}: {notes}')

    print("  Tool list (unique tools required):")
    for tid, info in sorted(tools_used.items(),
                             key=lambda x: (x[1].get('diameter_mm') or 0)):
        ops_str = ', '.join(sorted(info['operations']))
        dia_str = f"d={info['diameter_mm']}mm" if info['diameter_mm'] else 'variable'
        print(f"    {tid:20s} | {dia_str:10s} | {info['description'][:45]}")
    print()

    print("  Per-cluster operation -> tool mapping:")
    for c in clusters:
        ft = c.get('feature_type', '')
        if ft == 'background':
            continue
        cid = c['cluster_id']
        seq = c.get('process_sequence', [])
        if not seq:
            continue
        print(f"    C{cid:2d} [{ft:22s}]")
        for step in seq:
            op  = step['operation']
            tid = step.get('tool_id') or '—'
            dia = step.get('tool_diameter_mm')
            dia_str = f"d={dia}mm" if dia else ''
            vc  = step.get('Vc_mmin')
            vc_str = f"Vc={vc}m/min" if vc else ''
            print(f"         {op:22s} -> {tid:20s} {dia_str:10s} {vc_str}")
    print()

    if warnings:
        print(f"  [!] Warnings / substitutions ({len(warnings)}):")
        for w in warnings:
            print(w)
        print()


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def save_tools(data: Dict, output_path: str):
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Tool selection saved to: {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python tool_selection.py <setups_json> [output_json] "
              "[--material aluminium|mild_steel|stainless_steel] "
              "[--db tool_database.json]")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = None
    material    = DEFAULT_MATERIAL
    db_path     = None

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '--material' and i + 1 < len(sys.argv):
            material = sys.argv[i + 1]; i += 2
        elif arg == '--db' and i + 1 < len(sys.argv):
            db_path = sys.argv[i + 1]; i += 2
        elif not arg.startswith('--') and output_path is None:
            output_path = arg; i += 1
        else:
            i += 1

    if output_path is None:
        output_path = input_path.replace('.json', '_tools.json')

    with open(input_path) as f:
        data = json.load(f)

    result = select_tools(data, material=material, db_path=db_path)
    print_tool_summary(result)
    save_tools(result, output_path)
