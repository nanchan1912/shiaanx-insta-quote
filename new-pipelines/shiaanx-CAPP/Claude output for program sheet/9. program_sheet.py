"""
program_sheet.py
----------------
Final step in the CNC planning pipeline. Takes the parameter-calculated
JSON from parameter_calculation.py and produces a printable PDF program
sheet for the machinist.

The program sheet is the document that goes to the machine with the
operator. It contains everything needed to set up and run the job:

  Page 1 — Job header, coordinate system setup, tool list
  Page N — One page per setup, with fixture instructions and
            a full operations table (S, F, ap, ae, Q, cycle type)
  Final   — Warnings and flagged items requiring human review

Usage
-----
    python program_sheet.py <params_json> [output_pdf]
      [--part-name "Hub"] [--programmer "Your Name"] [--revision "A"]

    python program_sheet.py Hub_params.json Hub_program_sheet.pdf
      --part-name "Hub" --programmer "CNC-AI" --revision "A"

Or from Python:
    from program_sheet import generate_program_sheet
    generate_program_sheet(params_data, output_path,
                           part_name='Hub', programmer='CNC-AI')
"""

import json
import sys
import os
import copy
from datetime import datetime
from typing import Dict, List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.platypus.flowables import HRFlowable


# ---------------------------------------------------------------------------
# Colour palette — industrial / professional
# ---------------------------------------------------------------------------

C_DARK    = colors.HexColor('#1a1a2e')   # dark navy — headers
C_MID     = colors.HexColor('#16213e')   # mid navy
C_ACCENT  = colors.HexColor('#0f3460')   # accent blue
C_LIGHT   = colors.HexColor('#e8f0fe')   # light blue tint — row shading
C_WARN    = colors.HexColor('#fff3cd')   # amber — warning rows
C_WARN_BD = colors.HexColor('#856404')   # amber border
C_ERR     = colors.HexColor('#f8d7da')   # red — error rows
C_WHITE   = colors.white
C_BLACK   = colors.black
C_GRAY    = colors.HexColor('#6c757d')
C_LGRAY   = colors.HexColor('#dee2e6')
C_GREEN   = colors.HexColor('#d4edda')
C_CAP     = colors.HexColor('#fff0e0')   # light orange — RPM capped rows


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

def _styles():
    base = getSampleStyleSheet()
    s = {}

    s['title'] = ParagraphStyle('title',
        fontSize=20, fontName='Helvetica-Bold',
        textColor=C_WHITE, alignment=TA_LEFT, spaceAfter=2)

    s['subtitle'] = ParagraphStyle('subtitle',
        fontSize=10, fontName='Helvetica',
        textColor=C_LIGHT, alignment=TA_LEFT, spaceAfter=0)

    s['h1'] = ParagraphStyle('h1',
        fontSize=13, fontName='Helvetica-Bold',
        textColor=C_WHITE, alignment=TA_LEFT,
        spaceBefore=0, spaceAfter=0)

    s['h2'] = ParagraphStyle('h2',
        fontSize=11, fontName='Helvetica-Bold',
        textColor=C_DARK, spaceBefore=8, spaceAfter=4)

    s['body'] = ParagraphStyle('body',
        fontSize=8.5, fontName='Helvetica',
        textColor=C_BLACK, spaceAfter=2, leading=12)

    s['small'] = ParagraphStyle('small',
        fontSize=7.5, fontName='Helvetica',
        textColor=C_GRAY, spaceAfter=2, leading=10)

    s['warn'] = ParagraphStyle('warn',
        fontSize=8, fontName='Helvetica-Bold',
        textColor=C_WARN_BD, spaceAfter=2)

    s['cell'] = ParagraphStyle('cell',
        fontSize=7.5, fontName='Helvetica',
        textColor=C_BLACK, leading=10)

    s['cell_bold'] = ParagraphStyle('cell_bold',
        fontSize=7.5, fontName='Helvetica-Bold',
        textColor=C_BLACK, leading=10)

    s['cell_small'] = ParagraphStyle('cell_small',
        fontSize=6.5, fontName='Helvetica',
        textColor=C_GRAY, leading=9)

    s['mono'] = ParagraphStyle('mono',
        fontSize=7.5, fontName='Courier',
        textColor=C_DARK, leading=10)

    return s


def _p(text, style):
    """Safe Paragraph — escapes & < > for reportlab XML parser."""
    text = str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return Paragraph(text, style)


def _fmt_time(seconds) -> str:
    """Format seconds as MM:SS. Minimum display 00:01 for non-zero ops."""
    if seconds is None:
        return '--'
    s = int(seconds)
    if s == 0 and seconds > 0:
        s = 1  # never show 00:00 for an operation that takes time
    return f'{s // 60:02d}:{s % 60:02d}'


def _feature_short_name(feature: dict) -> str:
    ftype = feature.get('feature_type', '')
    if ftype == 'through_hole':
        return ''
    elif ftype == 'blind_hole':
        return ''
    elif ftype == 'counterbore':
        return 'COUNTERBORE'
    elif ftype == 'boss':
        return 'BOSS'
    elif ftype == 'planar_face':
        # Distinguish outer profile from simple face
        if len(feature.get('radii', [])) > 1 or feature.get('is_outer'):
            return 'OUTER PROFILE'
        return 'FACE'
    elif ftype == 'large_bore':
        return 'BORE'
    elif ftype == 'slot':
        width = feature.get('width', '')
        thru = ' THRU' if feature.get('through') else ''
        return f'{width} SLOT{thru}' if width else f'SLOT{thru}'
    elif ftype == 'pocket':
        return 'POCKET'
    else:
        return ftype.upper().replace('_', ' ')


def generate_toolpath_name(step: dict, cluster: dict, tool_dia: float,
                           material: str = '') -> str:
    """
    Generate a vendor-style descriptive toolpath name.
    Examples: "ALU 6 ENDMILL OUTER PROFILE RF", "6 ENDMILL OUTER PROFILE RF",
              "3 ENDMILL 5 SLOT FINISH", "1.2 DRILL", "CENTER DRILL"
    """
    # Material prefix (abbreviate to keep names concise)
    MATERIAL_PREFIX = {
        'aluminium':           'ALU',
        'aluminium_6061':      'ALU',
        'aluminium_6063':      'ALU',
        'aluminium_6082':      'ALU',
        'aluminium_7075':      'ALU7075',
        'aluminium_7050':      'ALU7050',
        'mild_steel':          'STL',
        'steel':               'STL',
        'stainless_steel':     'SS',
        'stainless_steel_316': 'SS316',
        'titanium':            'TI',
        'brass':               'BRASS',
    }
    mat_prefix = MATERIAL_PREFIX.get(material.lower(), '') if material else ''

    op        = step.get('operation', '')
    pass_type = step.get('pass_type')  # 'RF' | 'FINISH' | 'CORNER_R' | None

    # Tool type prefix
    if op in ('spot_drill',):
        prefix = 'CENTER DRILL'
    elif op in ('micro_drill', 'twist_drill', 'pilot_drill', 'core_drill'):
        dia_str = str(int(tool_dia) if tool_dia == int(tool_dia) else round(tool_dia, 2))
        prefix = f'{dia_str} DRILL'
    elif op == 'boring_bar':
        prefix = f'{round(tool_dia, 1)} BORING BAR'
    elif op == 'face_mill':
        prefix = f'{round(tool_dia, 0):.0f} FACEMILL'
    elif op in ('contour_mill', 'counterbore_mill', 'pocket_mill', 'circular_interp'):
        dia_str = str(int(tool_dia) if tool_dia == int(tool_dia) else round(tool_dia, 1))
        prefix = f'{dia_str} ENDMILL'
    elif op in ('tap',):
        prefix = f'{round(tool_dia, 1)} TAP'
    elif op in ('reamer',):
        prefix = f'{round(tool_dia, 1)} REAMER'
    else:
        dia_str = str(round(tool_dia, 1)) if tool_dia else ''
        prefix = f'{dia_str} {op.upper().replace("_"," ")}'.strip()

    # Feature description
    feat_name = _feature_short_name(cluster)

    # Pass type suffix
    pass_suffix = {
        'RF':       'RF',
        'FINISH':   'FINISH',
        'CORNER_R': 'CORNER R',
        None:       '',
    }.get(pass_type, '')

    parts = [p for p in [mat_prefix, prefix, feat_name, pass_suffix] if p]
    return ' '.join(parts)


def _header_table(left_content, right_content, bg=C_DARK, width=None):
    """Two-column header band."""
    w = width or (A4[0] - 20*mm)
    t = Table([[left_content, right_content]],
              colWidths=[w * 0.65, w * 0.35])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), bg),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 6),
        ('RIGHTPADDING',(0,0),(-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING',(0,0),(-1,-1), 8),
    ]))
    return t


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_job_header(data: Dict, part_name: str, programmer: str,
                      revision: str, S) -> list:
    """Job identification header — page 1 top."""
    cs      = data.get('coord_system', {})
    mat     = data.get('material', '--')
    max_rpm = data.get('machine_max_rpm', '--')
    coolant = data.get('coolant', '--').replace('_', ' ')
    date    = datetime.now().strftime('%Y-%m-%d')
    n_setup = data.get('setup_count', '--')

    # §6e — stock dimensions from bounding_box
    bbox = data.get('bounding_box', {})
    if bbox:
        dx = bbox.get('xmax', 0) - bbox.get('xmin', 0)
        dy = bbox.get('ymax', 0) - bbox.get('ymin', 0)
        dz = bbox.get('zmax', 0) - bbox.get('zmin', 0)
        stock_str = f'Part envelope: {round(dx,1)} x {round(dy,1)} x {round(dz,1)} mm'
    else:
        stock_str = ''

    left  = [
        _p(f'CNC PROGRAM SHEET', S['title']),
        _p(f'Part: {part_name}  |  Rev: {revision}  |  Date: {date}', S['subtitle']),
        _p(f'Programmer: {programmer}', S['subtitle']),
    ]
    right_lines = [
        f'Material: {mat.upper()}',
        f'Max RPM: {max_rpm}  Coolant: {coolant}',
        f'Setups: {n_setup}',
    ]
    if stock_str:
        right_lines.append(stock_str)

    right = [_p(line, S['subtitle']) for line in right_lines]

    from reportlab.platypus import KeepInFrame
    w = A4[0] - 20*mm

    def _col(items):
        return Table([[item] for item in items],
                     colWidths=[w * 0.6])

    header = _header_table(
        Table([[item] for item in left],   colWidths=[w * 0.62]),
        Table([[item] for item in right],  colWidths=[w * 0.33]),
        bg=C_DARK, width=w
    )
    return [header, Spacer(1, 6*mm)]


def _build_coord_system(data: Dict, S) -> list:
    """Work zero and bounding box information block."""
    cs  = data.get('coord_system', {})
    wz  = cs.get('work_zero_cad', [0, 0, 0])
    mbb = cs.get('machine_bounding_box', {})
    conv = cs.get('work_zero_convention', '--').replace('_', ' ')
    up   = cs.get('cad_up_axis', '--')
    note = cs.get('notes', '')

    flowables = []
    flowables.append(_p('COORDINATE SYSTEM &amp; WORK ZERO (G54)', S['h2']))

    info_rows = [
        ['Work zero convention', conv,
         'Part height (Y)', f'{mbb.get("length_y", "--")} mm'],
        ['CAD up-axis', up,
         'X range (machine)', f'{mbb.get("xmin","--")} to {mbb.get("xmax","--")} mm'],
        ['Work zero in CAD',
         f'X={round(wz[0],3)}  Y={round(wz[1],3)}  Z={round(wz[2],3)}',
         'Z range (machine)', f'{mbb.get("zmin","--")} to {mbb.get("zmax","--")} mm'],
        ['G54 meaning',
         'Set controller G54 offset so that X0 Y0 Z0 = top face centre of part',
         'Safe rapid Y', f'{round(mbb.get("ymax", 5) + 5, 1)} mm above work zero'],
    ]

    col_w = (A4[0] - 20*mm) / 4
    t = Table(info_rows, colWidths=[col_w*1.1, col_w*1.4, col_w*0.9, col_w*0.6])
    t.setStyle(TableStyle([
        ('FONTNAME',  (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE',  (0,0), (-1,-1), 8),
        ('FONTNAME',  (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME',  (2,0), (2,-1), 'Helvetica-Bold'),
        ('BACKGROUND',(0,0), (-1,-1), C_LIGHT),
        ('BACKGROUND',(0,0), (0,-1), colors.HexColor('#d0dff8')),
        ('BACKGROUND',(2,0), (2,-1), colors.HexColor('#d0dff8')),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [C_LIGHT, C_WHITE]),
        ('GRID',      (0,0), (-1,-1), 0.3, C_LGRAY),
        ('TOPPADDING',(0,0), (-1,-1), 4),
        ('BOTTOMPADDING',(0,0),(-1,-1), 4),
        ('LEFTPADDING',(0,0),(-1,-1), 5),
    ]))
    flowables.append(t)
    flowables.append(Spacer(1, 4*mm))
    return flowables


def _build_tool_list(data: Dict, S) -> list:
    """Master tool list — all unique tools across all setups."""
    flowables = []
    flowables.append(_p('TOOL LIST', S['h2']))

    # Collect unique tools from all cluster process sequences
    tools_seen = {}   # tool_id -> step dict (first occurrence)
    tool_ops   = {}   # tool_id -> set of operations

    for c in data.get('clusters', []):
        if c.get('feature_type') == 'background':
            continue
        for step in c.get('process_sequence', []):
            tid = step.get('tool_id')
            if not tid or tid in ('NOT_FOUND', None):
                continue
            if step.get('operation') in ('fixture_rotation',):
                continue
            if tid not in tools_seen:
                tools_seen[tid] = step
                tool_ops[tid]   = set()
            tool_ops[tid].add(step.get('operation', ''))

    # Sort by diameter
    sorted_tools = sorted(tools_seen.items(),
                          key=lambda x: x[1].get('tool_diameter_mm') or 0)

    # Assign T-numbers
    t_numbers = {tid: i+1 for i, (tid, _) in enumerate(sorted_tools)}

    # Table
    headers = ['T#', 'Tool ID', 'Description', 'Dia (mm)',
               'Manufacturer', 'Grade', 'Used for']
    rows = [headers]
    for tid, step in sorted_tools:
        ops_str = ', '.join(sorted(tool_ops[tid]))
        rows.append([
            f'T{t_numbers[tid]:02d}',
            tid,
            step.get('tool_description', '--'),
            str(step.get('tool_diameter_mm', '--')),
            step.get('manufacturer', '--'),
            step.get('grade', '--'),
            ops_str,
        ])

    w = A4[0] - 20*mm
    col_w = [w*0.05, w*0.13, w*0.28, w*0.07, w*0.15, w*0.07, w*0.25]
    t = Table(rows, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0,0), (-1,0), C_ACCENT),
        ('TEXTCOLOR',  (0,0), (-1,0), C_WHITE),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 7.5),
        ('FONTNAME',   (0,1), (-1,-1), 'Helvetica'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [C_WHITE, C_LIGHT]),
        ('GRID',       (0,0), (-1,-1), 0.3, C_LGRAY),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING',(0,0),(-1,-1), 3),
        ('LEFTPADDING',(0,0),(-1,-1), 4),
        ('ALIGN',      (3,0), (3,-1), 'CENTER'),
        ('ALIGN',      (0,0), (0,-1), 'CENTER'),
    ]))
    flowables.append(t)

    # Return tool number mapping for use in setup pages
    flowables.append(Spacer(1, 4*mm))
    return flowables, t_numbers


def _drill_cycle_gcode(cycle: str, depth: float, peck: float,
                       vf: float) -> str:
    """Return the G-code cycle descriptor string for the notes column."""
    if depth is None:
        return '--'
    depth_str = f'Z-{abs(round(depth, 3))}'
    if cycle == 'standard' or cycle is None:
        return f'G81 {depth_str} F{vf}'
    elif cycle in ('peck', 'deep_peck'):
        q = peck or 0
        return f'G83 {depth_str} Q{q} F{vf}'
    return f'{depth_str} F{vf}'


def _build_setup_page(setup: Dict, data: Dict,
                      t_numbers: Dict, S) -> list:
    """One page (or section) per setup with fixture info + operations table."""
    sid   = setup['setup_id']
    stype = setup['setup_type'].upper()
    label = setup['axis_label']
    desc  = setup['description']
    fix   = setup['fixture_note']
    rot   = setup.get('rotation_from_default')
    cids  = setup['cluster_ids']

    flowables = []

    # --- Setup banner ---
    left  = _p(f'SETUP {sid} -- {label}', S['h1'])
    right = _p(f'{stype}  |  {len(cids)} clusters  |  '
               f'{setup.get("operation_count","?")} operations', S['subtitle'])
    banner = _header_table(left, right, bg=C_ACCENT)
    flowables.append(banner)
    flowables.append(Spacer(1, 3*mm))

    # --- Fixture and rotation info ---
    fix_rows = [['Fixture', fix]]
    fix_rows.append(['Description', desc])
    if rot:
        fix_rows.append([
            'Rotation required',
            f'{rot["angle_deg"]} deg around {rot["rotation_axis_label"]} -- '
            f'verify with dial indicator before cutting'
        ])
    else:
        fix_rows.append(['Rotation', 'None -- standard orientation'])

    # §6a — WCS info rows
    wcs      = setup.get('wcs', 'G54')
    origin_x = setup.get('origin_x', '--')
    origin_y = setup.get('origin_y', '--')
    origin_z = setup.get('origin_z', '--')
    face_w   = setup.get('setup_face_width')
    face_h   = setup.get('setup_face_height')
    depth_s  = setup.get('setup_depth')
    face_dim = (f'{round(face_w,1)} x {round(face_h,1)} mm'
                if face_w is not None and face_h is not None else '--')
    max_dep  = f'{round(depth_s,1)} mm' if depth_s is not None else '--'

    fix_rows.append(['WCS', str(wcs)])
    fix_rows.append(['Origin X/Y/Z',
                     f'X={origin_x}  Y={origin_y}  Z={origin_z}'])
    fix_rows.append(['Face (W x H)', face_dim])
    fix_rows.append(['Max depth', max_dep])

    w = A4[0] - 20*mm
    ft = Table(fix_rows, colWidths=[w*0.15, w*0.85])
    ft.setStyle(TableStyle([
        ('FONTNAME',   (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME',   (1,0), (1,-1), 'Helvetica'),
        ('FONTSIZE',   (0,0), (-1,-1), 8),
        ('BACKGROUND', (0,0), (-1,-1), C_LIGHT),
        ('GRID',       (0,0), (-1,-1), 0.3, C_LGRAY),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING',(0,0),(-1,-1), 4),
        ('LEFTPADDING',(0,0),(-1,-1), 5),
        ('VALIGN',     (0,0), (-1,-1), 'TOP'),
    ]))
    flowables.append(ft)
    flowables.append(Spacer(1, 4*mm))

    # --- Build cluster lookup for this setup ---
    cluster_map = {c['cluster_id']: c for c in data['clusters']}

    # --- Operations table (§6b new column layout) ---
    flowables.append(_p('OPERATIONS', S['h2']))

    headers = ['No.', 'ToolPath Name', 'T#', 'Dia\n(mm)', 'Tool R\n(mm)',
               'Depth\nMAX', 'RPM', 'Feed\n(mm/min)', 'DOC\n(mm)',
               'Stk XY\n(mm)', 'Stk Z\n(mm)', 'Time']
    rows = [headers]

    # Track pass_type per data row index (offset +1 for header)
    row_pass_types = []

    def _fmt(v, decimals=1):
        if v is None:
            return '--'
        return str(round(v, decimals))

    global_step = 1
    for cid in cids:
        cluster = cluster_map.get(cid)
        if not cluster:
            continue
        ft_type = cluster.get('feature_type', '--')
        if ft_type == 'background':
            continue

        for step in cluster.get('process_sequence', []):
            op = step.get('operation', '--')
            if op == 'fixture_rotation':
                # Special row spanning all columns
                rows.append([
                    str(global_step),
                    'FIXTURE ROTATION -- ' + step.get('reason', 'Reposition part per setup instructions'),
                    '--', '--', '--', '--', '--', '--', '--', '--', '--', '--',
                ])
                row_pass_types.append('FIXTURE_ROTATION')
                global_step += 1
                continue

            tid      = step.get('tool_id') or '--'
            tnum     = f'T{t_numbers.get(tid, "?"):02d}' if tid != '--' else '--'
            tool_dia = step.get('tool_diameter_mm')
            corner_r = step.get('corner_radius', 0) or 0
            depth    = step.get('depth_mm')
            rpm      = step.get('rpm')
            feed     = step.get('vf_mmpm')
            doc      = step.get('ap_mm')
            stk_xy   = step.get('stock_to_leave_xy')
            stk_z    = step.get('stock_to_leave_z')
            est_time = step.get('estimated_time_s', 0)
            pass_type = step.get('pass_type')

            tp_name = generate_toolpath_name(step, cluster, tool_dia or 0,
                                             material=data.get('material', ''))

            rows.append([
                str(global_step),
                tp_name,
                tnum,
                _fmt(tool_dia, 2) if tool_dia else '--',
                _fmt(corner_r, 2),
                _fmt(depth, 2) if depth is not None else '--',
                _fmt(rpm, 0) if rpm is not None else '--',
                _fmt(feed, 0) if feed is not None else '--',
                _fmt(doc, 3) if doc is not None else '--',
                _fmt(stk_xy, 2) if stk_xy is not None else '--',
                _fmt(stk_z, 2) if stk_z is not None else '--',
                _fmt_time(est_time),
            ])
            row_pass_types.append(pass_type)
            global_step += 1

    # Column widths (§6b specified layout)
    cw = [w*0.04, w*0.22, w*0.04, w*0.06, w*0.05,
          w*0.07, w*0.07, w*0.08, w*0.07, w*0.06, w*0.06, w*0.06]

    ops_table = Table(rows, colWidths=cw, repeatRows=1)

    # Base style
    style_cmds = [
        ('BACKGROUND',    (0,0), (-1,0), C_ACCENT),
        ('TEXTCOLOR',     (0,0), (-1,0), C_WHITE),
        ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,-1), 7),
        ('FONTNAME',      (0,1), (-1,-1), 'Helvetica'),
        ('GRID',          (0,0), (-1,-1), 0.3, C_LGRAY),
        ('TOPPADDING',    (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING',   (0,0), (-1,-1), 3),
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('ALIGN',         (0,0), (0,-1), 'CENTER'),
        ('ALIGN',         (2,0), (11,-1), 'CENTER'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [C_WHITE, C_LIGHT]),
    ]

    # Row-level highlighting by pass_type (§6b colouring rules)
    for i, pt in enumerate(row_pass_types, start=1):
        if pt == 'FIXTURE_ROTATION':
            style_cmds.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#e8d5f5')))
            style_cmds.append(('FONTNAME',   (0,i), (-1,i), 'Helvetica-Bold'))
        elif pt == 'RF':
            style_cmds.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#e8f4f8')))
        elif pt == 'FINISH':
            style_cmds.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#e8f8e8')))
        elif pt == 'CORNER_R':
            style_cmds.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#fff8e8')))

    ops_table.setStyle(TableStyle(style_cmds))
    flowables.append(ops_table)
    flowables.append(Spacer(1, 3*mm))

    # §6a — Per-setup total time footer
    setup_time_s = setup.get('estimated_time_s', 0) or 0
    mm_t = int(setup_time_s // 60)
    ss_t = int(setup_time_s % 60)
    time_footer = f'ESTIMATED SETUP TIME: {mm_t:02d}:{ss_t:02d}  (includes tool changes)'
    flowables.append(_p(time_footer, S['body']))
    flowables.append(Spacer(1, 6*mm))

    return flowables


def _build_warnings(data: Dict, S) -> list:
    """Collect all warnings and substitutions into a final section."""
    flowables = []

    warnings = []
    for c in data.get('clusters', []):
        if c.get('feature_type') == 'background':
            continue
        cid = c['cluster_id']
        for step in c.get('process_sequence', []):
            notes = step.get('tool_notes', '') or ''
            pnotes = step.get('param_notes', '') or ''
            op = step.get('operation', '')

            if 'SUBSTITUTION' in notes:
                warnings.append(('SUBSTITUTION', cid, op, notes))
            if 'WARNING' in notes.upper():
                warnings.append(('WARNING', cid, op, notes))
            if step.get('rpm_capped'):
                warnings.append(('RPM CAPPED', cid, op,
                    f'Effective Vc = {step.get("actual_Vc_mmin")} m/min '
                    f'(reduced from {step.get("Vc_mmin")} m/min). '
                    f'Feed rate adjusted accordingly.'))

    if not warnings:
        return []

    flowables.append(_p('WARNINGS &amp; FLAGGED ITEMS', S['h2']))

    warn_rows = [['Type', 'Cluster', 'Operation', 'Detail']]
    for wtype, cid, op, detail in warnings:
        warn_rows.append([wtype, f'C{cid}', op, detail])

    w = A4[0] - 20*mm
    wt = Table(warn_rows,
               colWidths=[w*0.12, w*0.06, w*0.14, w*0.68],
               repeatRows=1)
    wt.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,0), C_ACCENT),
        ('TEXTCOLOR',    (0,0), (-1,0), C_WHITE),
        ('FONTNAME',     (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',     (0,0), (-1,-1), 7),
        ('FONTNAME',     (0,1), (-1,-1), 'Helvetica'),
        ('GRID',         (0,0), (-1,-1), 0.3, C_LGRAY),
        ('TOPPADDING',   (0,0), (-1,-1), 3),
        ('BOTTOMPADDING',(0,0),(-1,-1), 3),
        ('LEFTPADDING',  (0,0),(-1,-1), 4),
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1), [C_WARN, C_WHITE]),
    ]))
    # Colour rows by warning type
    for i, (wtype, *_) in enumerate(warnings, start=1):
        if wtype == 'SUBSTITUTION':
            wt.setStyle(TableStyle([
                ('BACKGROUND', (0,i), (0,i), colors.HexColor('#cce5ff')),
                ('TEXTCOLOR',  (0,i), (0,i), colors.HexColor('#004085')),
            ]))
        elif wtype == 'RPM CAPPED':
            wt.setStyle(TableStyle([
                ('BACKGROUND', (0,i), (0,i), C_CAP),
            ]))
        elif wtype == 'WARNING':
            wt.setStyle(TableStyle([
                ('BACKGROUND', (0,i), (0,i), C_ERR),
                ('TEXTCOLOR',  (0,i), (0,i), colors.HexColor('#721c24')),
            ]))

    flowables.append(wt)
    flowables.append(Spacer(1, 4*mm))
    return flowables


# ---------------------------------------------------------------------------
# Page number footer
# ---------------------------------------------------------------------------

class _FooterCanvas:
    """Mixin-style canvas for adding page footer to every page."""
    pass


def _on_page(canvas, doc, part_name, revision):
    canvas.saveState()
    w, h = A4
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(C_GRAY)
    canvas.drawString(10*mm, 8*mm,
                      f'{part_name}  Rev {revision}  --  CNC Program Sheet  '
                      f'--  Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    canvas.drawRightString(w - 10*mm, 8*mm,
                           f'Page {doc.page}')
    canvas.setStrokeColor(C_LGRAY)
    canvas.line(10*mm, 12*mm, w - 10*mm, 12*mm)
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_program_sheet(params_data: Dict,
                            output_path: str,
                            part_name:   str = 'Part',
                            programmer:  str = 'CNC-AI',
                            revision:    str = 'A') -> str:
    """
    Generate a PDF program sheet from the params JSON.

    Parameters
    ----------
    params_data : dict -- parsed JSON from parameter_calculation.py
    output_path : str  -- output PDF file path
    part_name   : str  -- part name for header
    programmer  : str  -- programmer name for header
    revision    : str  -- revision letter

    Returns
    -------
    str -- output_path (confirmed written)
    """
    S = _styles()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=10*mm, rightMargin=10*mm,
        topMargin=12*mm,  bottomMargin=18*mm,
        title=f'{part_name} CNC Program Sheet',
        author=programmer,
        subject='CNC Machining Program Sheet',
    )

    story = []

    # --- Page 1: Job header + coord system + tool list ---
    story += _build_job_header(params_data, part_name, programmer, revision, S)
    story += _build_coord_system(params_data, S)

    tool_section, t_numbers = _build_tool_list(params_data, S)
    story += tool_section

    story.append(PageBreak())

    # --- One section per setup ---
    setups = params_data.get('setups', [])
    for i, setup in enumerate(setups):
        story += _build_setup_page(setup, params_data, t_numbers, S)
        if i < len(setups) - 1:
            story.append(PageBreak())

    # --- Warnings page ---
    warn_section = _build_warnings(params_data, S)
    if warn_section:
        story.append(PageBreak())
        story += warn_section

    # Build
    doc.build(
        story,
        onFirstPage=lambda c, d: _on_page(c, d, part_name, revision),
        onLaterPages=lambda c, d: _on_page(c, d, part_name, revision),
    )
    return output_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python program_sheet.py <params_json> [output_pdf] "
              "[--part-name NAME] [--programmer NAME] [--revision A]")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = None
    part_name   = 'Part'
    programmer  = 'CNC-AI'
    revision    = 'A'

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '--part-name' and i + 1 < len(sys.argv):
            part_name = sys.argv[i + 1]; i += 2
        elif arg == '--programmer' and i + 1 < len(sys.argv):
            programmer = sys.argv[i + 1]; i += 2
        elif arg == '--revision' and i + 1 < len(sys.argv):
            revision = sys.argv[i + 1]; i += 2
        elif not arg.startswith('--') and output_path is None:
            output_path = arg; i += 1
        else:
            i += 1

    if output_path is None:
        output_path = input_path.replace('.json', '_program_sheet.pdf')

    with open(input_path) as f:
        data = json.load(f)

    out = generate_program_sheet(
        data, output_path,
        part_name=part_name,
        programmer=programmer,
        revision=revision,
    )
    print(f"Program sheet written to: {out}")
