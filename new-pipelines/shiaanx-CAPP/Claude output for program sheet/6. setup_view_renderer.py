"""
setup_view_renderer.py
----------------------
Renders per-setup isometric views from a STEP file using CadQuery SVG export.

Produces one SVG (and optionally PNG) per CNC setup. Each view is oriented
to show the accessible face for that setup. After CadQuery renders the SVG,
coloured XYZ axis arrows are injected via SVG post-processing.

Used by program_sheet.py to embed 3D setup views in the PDF.

Usage (standalone)
------------------
    python setup_view_renderer.py <step_file> <setups_json> [output_dir]

    python setup_view_renderer.py part.step Hub_setups.json Hub_views/

Or from Python:
    from setup_view_renderer import render_all_setups
    image_paths = render_all_setups("part.step", "Hub_setups.json", "Hub_views/")
    # returns {setup_number: png_path} or {setup_number: svg_path}
"""

import json
import sys
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

# CadQuery import -- optional (graceful fallback if not installed)
try:
    import cadquery as cq
    _CQ_AVAILABLE = True
except ImportError:
    _CQ_AVAILABLE = False

# svglib import -- for SVG -> ReportLab Drawing conversion
try:
    from svglib.svglib import svg2rlg
    _SVGLIB_AVAILABLE = True
except ImportError:
    _SVGLIB_AVAILABLE = False

# cairosvg import -- for SVG -> PNG conversion (alternative)
try:
    import cairosvg
    _CAIROSVG_AVAILABLE = True
except ImportError:
    _CAIROSVG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Projection map -- approach axis -> CadQuery projection direction
# ---------------------------------------------------------------------------
# projectionDir is the direction the camera looks FROM (normalised vector).
# Chosen to show the accessible face prominently with an isometric tilt.

PROJECTION_MAP = {
    "+Y": {"projectionDir": (0.5, -0.7, 0.5),  "label": "Top (default VMC)"},
    "-Y": {"projectionDir": (0.5,  0.7, 0.5),  "label": "Bottom (flipped)"},
    "+X": {"projectionDir": (-0.7, 0.5, 0.5),  "label": "Right side"},
    "-X": {"projectionDir": (0.7,  0.5, 0.5),  "label": "Left side"},
    "+Z": {"projectionDir": (0.5,  0.5, -0.7), "label": "Front"},
    "-Z": {"projectionDir": (0.5,  0.5,  0.7), "label": "Rear"},
}

# Default SVG options for CadQuery export
SVG_OPTIONS_BASE = {
    "width": 400,
    "height": 300,
    "marginLeft": 20,
    "marginTop": 20,
    "showAxes": False,      # We inject our own arrows in post-processing
    "strokeWidth": 0.5,
    "strokeColor": (30, 30, 30),
    "hiddenColor": (180, 180, 180),
    "showHidden": True,
}

# SVG arrowhead marker definition (injected once into each SVG <defs>)
_ARROWHEAD_DEFS = """
<defs>
  <marker id="ah-red"   markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
    <polygon points="0 0, 6 3, 0 6" fill="red"/>
  </marker>
  <marker id="ah-green" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
    <polygon points="0 0, 6 3, 0 6" fill="green"/>
  </marker>
  <marker id="ah-blue"  markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
    <polygon points="0 0, 6 3, 0 6" fill="blue"/>
  </marker>
</defs>
"""

# Axis arrow vectors per approach axis.
# Each tuple: (dx, dy, reserved, color, label)
# Origin is at (ox, oy) = bottom-left region of image (60, 260).
_AXIS_ARROWS = {
    "+Y": [
        (50,   0,  0, "red",   "X"),   # X goes right
        (0,  -50,  0, "green", "Y"),   # Y goes up
        (-35, -35, 0, "blue",  "Z"),   # Z goes up-left (into page)
    ],
    "-Y": [
        (50,   0,  0, "red",   "X"),
        (0,   50,  0, "green", "Y"),
        (-35, -35, 0, "blue",  "Z"),
    ],
    "+X": [
        (0,  -50,  0, "red",   "X"),
        (50,   0,  0, "green", "Y"),
        (-35, -35, 0, "blue",  "Z"),
    ],
    "-X": [
        (0,   50,  0, "red",   "X"),
        (50,   0,  0, "green", "Y"),
        (-35, -35, 0, "blue",  "Z"),
    ],
    "+Z": [
        (50,   0,  0, "red",   "X"),
        (0,  -50,  0, "green", "Y"),
        (-35,  35, 0, "blue",  "Z"),
    ],
    "-Z": [
        (50,   0,  0, "red",   "X"),
        (0,  -50,  0, "green", "Y"),
        (35,   35, 0, "blue",  "Z"),
    ],
}


# ---------------------------------------------------------------------------
# Core rendering functions
# ---------------------------------------------------------------------------

def load_step(step_path: str) -> "cq.Workplane":
    """Load a STEP file into a CadQuery workplane."""
    if not _CQ_AVAILABLE:
        raise ImportError("cadquery is not installed. Run: pip install cadquery")
    return cq.importers.importStep(step_path)


def _approach_from_spindle(spindle_direction: list) -> str:
    """
    Convert spindle_direction vector (from setup JSON) to approach axis string.
    spindle_direction is the direction the spindle comes FROM (opposite of feature axis).
    """
    # Round to handle floating point
    sd = [round(float(x)) for x in spindle_direction]
    mapping = {
        (0,  1, 0): "+Y",
        (0, -1, 0): "-Y",
        (1,  0, 0): "+X",
        (-1, 0, 0): "-X",
        (0,  0, 1): "+Z",
        (0,  0, -1): "-Z",
    }
    key = tuple(sd)
    return mapping.get(key, "+Y")  # default to top view


def render_setup_view(part: "cq.Workplane", approach_axis: str,
                      output_svg: str) -> str:
    """
    Render an SVG view of the part oriented for the given approach axis.
    Returns path to the generated SVG file.
    """
    if not _CQ_AVAILABLE:
        raise ImportError("cadquery is not installed")

    proj = PROJECTION_MAP.get(approach_axis, PROJECTION_MAP["+Y"])
    opts = dict(SVG_OPTIONS_BASE)
    opts["projectionDir"] = proj["projectionDir"]

    os.makedirs(os.path.dirname(os.path.abspath(output_svg)), exist_ok=True)
    cq.exporters.export(part, output_svg, opt=opts)
    return output_svg


def _build_axis_arrows_svg(approach_axis: str, ox: int = 60, oy: int = 260) -> str:
    """
    Build SVG markup for coloured XYZ axis arrows.
    ox, oy = origin point of the axis indicator (bottom-left region by default).
    """
    arrow_defs = _ARROWHEAD_DEFS
    arrows = _AXIS_ARROWS.get(approach_axis, _AXIS_ARROWS["+Y"])

    lines = []
    for dx, dy, _, color, label in arrows:
        x2 = ox + dx
        y2 = oy + dy
        marker = f"ah-{color}"
        lines.append(
            f'<line x1="{ox}" y1="{oy}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="2.5" '
            f'marker-end="url(#{marker})"/>'
        )
        # Label offset: push label past the arrowhead
        lx = x2 + (8 if dx >= 0 else -14)
        ly = y2 + (12 if dy >= 0 else -4)
        lines.append(
            f'<text x="{lx}" y="{ly}" fill="{color}" '
            f'font-family="Arial,Helvetica" font-size="13" font-weight="bold">{label}</text>'
        )

    group = '\n'.join([
        '<g id="axis-indicator">',
        *lines,
        '</g>',
    ])
    return arrow_defs + '\n' + group


def inject_axis_arrows(svg_path: str, approach_axis: str) -> str:
    """
    Post-process an SVG file to inject coloured XYZ axis arrows.
    Modifies the file in place. Returns svg_path.
    """
    with open(svg_path, 'r', encoding='utf-8') as f:
        content = f.read()

    arrow_svg = _build_axis_arrows_svg(approach_axis)

    # Inject arrowhead defs into <defs> if it exists, else before </svg>
    if '<defs>' in content:
        content = content.replace('<defs>', '<defs>\n' + _ARROWHEAD_DEFS, 1)
        # Inject the arrow group (without defs) before </svg>
        arrow_group = arrow_svg.split('</defs>')[-1] if '</defs>' in arrow_svg else arrow_svg
        content = content.replace('</svg>', arrow_group + '\n</svg>', 1)
    else:
        content = content.replace('</svg>', arrow_svg + '\n</svg>', 1)

    with open(svg_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return svg_path


def svg_to_png(svg_path: str, png_path: str,
               width: int = 400, height: int = 300) -> Optional[str]:
    """
    Convert SVG to PNG using cairosvg (preferred) or svglib.
    Returns png_path on success, None if conversion is not available.
    """
    if _CAIROSVG_AVAILABLE:
        try:
            cairosvg.svg2png(
                url=str(Path(svg_path).resolve()),
                write_to=png_path,
                output_width=width,
                output_height=height,
            )
            return png_path
        except Exception as e:
            print(f"  cairosvg failed for {svg_path}: {e}")

    if _SVGLIB_AVAILABLE:
        try:
            from reportlab.graphics import renderPM
            drawing = svg2rlg(svg_path)
            if drawing:
                renderPM.drawToFile(drawing, png_path, fmt="PNG")
                return png_path
        except Exception as e:
            print(f"  svglib failed for {svg_path}: {e}")

    print(f"  WARNING: no SVG->PNG converter available. "
          f"Install cairosvg or svglib. SVG available at {svg_path}")
    return None


def load_svg_as_rl_drawing(svg_path: str):
    """
    Load an SVG file as a ReportLab Drawing object for embedding in PDF.
    Returns None if svglib is not available.
    """
    if not _SVGLIB_AVAILABLE:
        return None
    try:
        return svg2rlg(svg_path)
    except Exception as e:
        print(f"  svglib load failed for {svg_path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_all_setups(step_path: str, setups_json_path: str,
                      output_dir: str) -> Dict[int, str]:
    """
    Render SVG (and PNG where possible) views for all setups.

    Parameters
    ----------
    step_path       : path to the STEP file
    setups_json_path: path to the setups JSON (output of setup_planning.py)
    output_dir      : directory to write SVG/PNG files to

    Returns
    -------
    dict mapping setup_number -> image_path (PNG if available, else SVG)
    """
    if not _CQ_AVAILABLE:
        print("WARNING: cadquery not installed -- cannot render setup views.")
        print("  Install with: pip install cadquery")
        return {}

    print(f"Loading STEP: {step_path}")
    try:
        part = load_step(step_path)
    except Exception as e:
        print(f"ERROR loading STEP file: {e}")
        return {}

    with open(setups_json_path, encoding='utf-8') as f:
        data = json.load(f)

    setups = data.get('setups', [])
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for setup in setups:
        setup_num   = setup['setup_id']
        spindle_dir = setup.get('spindle_direction', [0, -1, 0])
        approach_ax = setup.get('approach_axis') or _approach_from_spindle(spindle_dir)

        svg_path = str(out_dir / f"setup_{setup_num}_view.svg")
        png_path = str(out_dir / f"setup_{setup_num}_view.png")

        print(f"  Setup {setup_num}: approach={approach_ax} -> {svg_path}")

        try:
            render_setup_view(part, approach_ax, svg_path)
            inject_axis_arrows(svg_path, approach_ax)

            # Try PNG conversion
            png = svg_to_png(svg_path, png_path)
            results[setup_num] = png if png else svg_path

        except Exception as e:
            print(f"  ERROR rendering setup {setup_num}: {e}")

    print(f"Rendered {len(results)} setup view(s) to {output_dir}")
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python setup_view_renderer.py <step_file> <setups_json> [output_dir]")
        sys.exit(1)

    step_file   = sys.argv[1]
    setups_json = sys.argv[2]
    out_dir     = sys.argv[3] if len(sys.argv) > 3 else 'setup_views'

    images = render_all_setups(step_file, setups_json, out_dir)
    for sid, path in sorted(images.items()):
        print(f"  Setup {sid}: {path}")
