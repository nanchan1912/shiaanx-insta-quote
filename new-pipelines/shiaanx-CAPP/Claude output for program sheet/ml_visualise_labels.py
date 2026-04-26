"""
visualise_labels.py — Interactive 3D viewer: colour each face by its MFCAD++ GT label.

Usage:
    conda run -n occ python "visualise_labels.py" <path/to/part.step>

Controls (standard OCC viewer):
    Left-drag   — rotate
    Middle-drag — pan
    Scroll      — zoom
    V           — fit all
    Q / Esc     — quit

What it shows:
    Each face is coloured by its MFCAD++ ground truth class (label encoded in the
    ADVANCED_FACE name field of the STEP file).  A text legend is printed to the
    terminal.  Faces with the same colour belong to the same feature class.

Label source: ADVANCED_FACE('N', ...) name field → class ID N (0–24).
Taxonomy:     rule_sheets/07_label_taxonomy.json → human-readable class name.
"""

import re
import sys
import json
from pathlib import Path

from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepTools import breptools
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import topods
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.AIS import AIS_Shape
from OCC.Display.SimpleGui import init_display


# ---------------------------------------------------------------------------
# 25 visually distinct colours (one per MFCAD++ class 0–24)
# Chosen to be maximally distinguishable against a grey background.
# Format: (R, G, B) each 0.0–1.0
# ---------------------------------------------------------------------------
CLASS_COLOURS = [
    (0.90, 0.60, 0.00),   # 0  Chamfer           — amber
    (0.00, 0.45, 0.70),   # 1  Through hole       — blue
    (0.00, 0.75, 0.50),   # 2  Triangular passage — teal
    (0.80, 0.40, 0.00),   # 3  Rect passage       — burnt orange
    (0.60, 0.00, 0.60),   # 4  6-sided passage    — purple
    (0.35, 0.70, 0.90),   # 5  Tri through slot   — sky blue
    (0.90, 0.20, 0.20),   # 6  Rect through slot  — red
    (0.20, 0.80, 0.20),   # 7  Circ through slot  — green
    (0.95, 0.90, 0.25),   # 8  Rect through step  — yellow
    (0.50, 0.50, 1.00),   # 9  2-sided through step — lavender
    (1.00, 0.50, 0.80),   # 10 Slanted through step — pink
    (0.30, 0.30, 0.30),   # 11 O-ring             — dark grey
    (0.00, 0.60, 0.90),   # 12 Blind hole         — deep sky
    (0.70, 0.90, 0.40),   # 13 Triangular pocket  — lime
    (0.95, 0.60, 0.10),   # 14 Rect pocket        — orange
    (0.40, 0.00, 0.80),   # 15 6-sided pocket     — violet
    (0.00, 0.80, 0.80),   # 16 Circ end pocket    — cyan
    (0.80, 0.00, 0.40),   # 17 Rect blind slot    — crimson
    (0.10, 0.70, 0.30),   # 18 Vert circ end blind slot — forest green
    (0.90, 0.30, 0.70),   # 19 Horiz circ end blind slot — magenta
    (0.60, 0.80, 0.00),   # 20 Triangular blind step — yellow-green
    (0.10, 0.40, 0.80),   # 21 Circular blind step — cobalt
    (0.80, 0.60, 0.40),   # 22 Rect blind step    — tan
    (0.50, 0.80, 0.80),   # 23 Round              — light teal
    (0.85, 0.85, 0.85),   # 24 Stock              — light grey
]

UNKNOWN_COLOUR = (1.0, 0.0, 1.0)  # magenta for any label outside 0–24


# ---------------------------------------------------------------------------
# Load taxonomy for human-readable names
# ---------------------------------------------------------------------------

def load_taxonomy(script_dir: Path) -> dict:
    path = script_dir / "rule_sheets" / "07_label_taxonomy.json"
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    return {m["mfcad_id"]: m["mfcad_name"] for m in data["mappings"]}


# ---------------------------------------------------------------------------
# Extract per-face labels from STEP file (text parse)
# ---------------------------------------------------------------------------

ADVANCED_FACE_RE = re.compile(r"ADVANCED_FACE\('(\d+)'", re.IGNORECASE)


def extract_step_labels(step_path: Path) -> list:
    labels = []
    with open(step_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = ADVANCED_FACE_RE.search(line)
            if m:
                labels.append(int(m.group(1)))
    return labels


# ---------------------------------------------------------------------------
# Load STEP geometry via OCC
# ---------------------------------------------------------------------------

def load_step(step_path: Path):
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(step_path))
    if status != 1:
        raise RuntimeError(f"STEPControl_Reader failed (status {status}): {step_path}")
    reader.TransferRoots()
    return reader.OneShape()


# ---------------------------------------------------------------------------
# Main viewer
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python visualise_labels.py <part.step>")
        sys.exit(1)

    step_path = Path(sys.argv[1])
    if not step_path.exists():
        print(f"ERROR: file not found: {step_path}")
        sys.exit(1)

    script_dir = Path(__file__).parent
    taxonomy = load_taxonomy(script_dir)

    print(f"\nLoading: {step_path.name}")
    face_labels = extract_step_labels(step_path)
    print(f"  GT labels found: {len(face_labels)} faces")

    shape = load_step(step_path)

    # Collect faces in OCC iteration order
    faces = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        faces.append(topods.Face(explorer.Current()))
        explorer.Next()

    print(f"  OCC faces:        {len(faces)}")
    if len(faces) != len(face_labels):
        print(f"  WARNING: face count mismatch ({len(faces)} OCC vs {len(face_labels)} labels) "
              f"— first {min(len(faces), len(face_labels))} faces will be labelled")

    n = min(len(faces), len(face_labels))

    # Print legend
    seen_labels = sorted(set(face_labels[:n]))
    print(f"\nLabel legend ({len(seen_labels)} classes present):")
    print(f"  {'ID':>3}  {'MFCAD++ name':30}  Colour (R, G, B)")
    print(f"  {'-'*3}  {'-'*30}  ---------------")
    for lbl in seen_labels:
        name = taxonomy.get(lbl, f"unknown_{lbl}")
        r, g, b = CLASS_COLOURS[lbl] if lbl < len(CLASS_COLOURS) else UNKNOWN_COLOUR
        print(f"  {lbl:>3}  {name:30}  ({r:.2f}, {g:.2f}, {b:.2f})")

    # Init display
    display, start_display, add_menu, add_function_to_menu = init_display()
    display.EraseAll()

    # Add each face as a coloured AIS_Shape
    for i in range(n):
        face = faces[i]
        label = face_labels[i]
        r, g, b = CLASS_COLOURS[label] if label < len(CLASS_COLOURS) else UNKNOWN_COLOUR

        ais = AIS_Shape(face)
        display.Context.Display(ais, False)
        display.Context.SetColor(
            ais,
            Quantity_Color(r, g, b, Quantity_TOC_RGB),
            False
        )
        display.Context.SetTransparency(ais, 0.0, False)

    # Any remaining faces with no label — show in white
    for i in range(n, len(faces)):
        ais = AIS_Shape(faces[i])
        display.Context.Display(ais, False)
        display.Context.SetColor(
            ais,
            Quantity_Color(1.0, 1.0, 1.0, Quantity_TOC_RGB),
            False
        )

    display.Context.UpdateCurrentViewer()
    display.FitAll()
    display.Repaint()
    display.View_Iso()
    display.FitAll()

    part_name = step_path.stem
    print(f"\nViewer open — {part_name}  ({n} faces coloured)")
    print("  Left-drag=rotate  Middle-drag=pan  Scroll=zoom  V=fit  Q/Esc=quit\n")

    start_display()


if __name__ == "__main__":
    main()
