from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

OUTPUT_PATH = r"C:\Users\Siddhant Gupta\Documents\ShiaanX\Pipeline_Analysis_Report.pdf"

doc = SimpleDocTemplate(
    OUTPUT_PATH,
    pagesize=A4,
    leftMargin=18*mm, rightMargin=18*mm,
    topMargin=20*mm, bottomMargin=20*mm
)

styles = getSampleStyleSheet()

H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=4, textColor=colors.HexColor("#1a2e4a"))
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, spaceAfter=3, spaceBefore=12, textColor=colors.HexColor("#1a2e4a"))
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, spaceAfter=2, spaceBefore=8, textColor=colors.HexColor("#2a4a6a"))
BODY = ParagraphStyle("BODY", parent=styles["Normal"], fontSize=9, spaceAfter=4, leading=13)
BOLD = ParagraphStyle("BOLD", parent=BODY, fontName="Helvetica-Bold")
META = ParagraphStyle("META", parent=BODY, fontSize=8, textColor=colors.HexColor("#555555"))
NOTE = ParagraphStyle("NOTE", parent=BODY, fontSize=8, textColor=colors.HexColor("#333333"), leftIndent=10, borderPad=4)

TABLE_HEADER = colors.HexColor("#1a2e4a")
TABLE_ALT = colors.HexColor("#f0f4f8")
TABLE_BORDER = colors.HexColor("#cccccc")

def make_table(headers, rows, col_widths=None):
    data = [headers] + rows
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.4, TABLE_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ])
    page_w = A4[0] - 36*mm
    if col_widths is None:
        n = len(headers)
        col_widths = [page_w / n] * n
    cell_data = []
    for row in data:
        cell_data.append([Paragraph(str(c), META) for c in row])
    return Table(cell_data, colWidths=col_widths, style=style, repeatRows=1)

story = []

# ── Title ──────────────────────────────────────────────────────────────────
story.append(Paragraph("ShiaanX Pipeline Analysis Report", H1))
story.append(Paragraph("<b>Part:</b> Botlabs Hinge — MIRROR_2.3MCYH2_2 / 2.3MCYH2_2", BODY))
story.append(Paragraph("<b>Date:</b> 2026-03-21", BODY))
story.append(Paragraph(
    "<b>Sources:</b> Siddhant pipeline (Claude output for program sheet/Botlabs Hinge/) vs "
    "Jayanth pipeline (Jayanth's docs/out1.json)", META))
story.append(HRFlowable(width="100%", thickness=1, color=TABLE_HEADER, spaceAfter=8))

# ── Section 1 ──────────────────────────────────────────────────────────────
story.append(Paragraph("1. Pipeline Comparison: Siddhant vs Jayanth", H2))
story.append(Paragraph(
    "Both pipelines process the same physical part (Botlabs Hinge, 2.3MCYH2_2). "
    "Comparison is stage by stage.", BODY))

# Stage 1
story.append(Paragraph("Stage 1: Feature Extraction", H3))
t = make_table(
    ["Dimension", "Jayanth", "Siddhant"],
    [
        ["Method", "freecad-occ-worker", "OCC-based face clustering"],
        ["Output", "25 raw features", "14 semantic clusters"],
        ["Feature IDs", "boss-2, hole-4, pocket-12...", "cluster_0 through cluster_13"],
        ["Feature types", "Generic: boss, hole, pocket", "Specific: through_hole, counterbore, boss, planar_face, background"],
        ["Confidence scoring", "None", "Yes — high / medium per cluster"],
        ["Background faces", "Not tracked", "Explicitly grouped into cluster_13 (20 faces)"],
    ],
    col_widths=[60*mm, 70*mm, 73*mm]
)
story.append(t)
story.append(Spacer(1, 3))
story.append(Paragraph(
    "<b>Critical difference — Feature Recognition Quality:</b> Jayanth detected counterbore holes as two "
    "separate entities (outer boss + inner hole). Siddhant correctly identified them as a single counterbore "
    "feature (radii [0.63, 1.4] = inner bore + outer step). Jayanth also detected 13 pocket features operating "
    "on full part geometry; Siddhant focuses on principal-axis features.", NOTE))

# Stage 2
story.append(Paragraph("Stage 2: Process Planning", H3))
t = make_table(
    ["Dimension", "Jayanth", "Siddhant"],
    [
        ["Ops per feature", "1 (single operation assigned)", "2–4 steps in sequence"],
        ["Spot drilling", "Not included", "Yes — spot_drill as Step 1 for every hole"],
        ["Drill cycle selection", "Fixed: standard", "DDR-computed: standard vs deep_peck"],
        ["DDR shown", "No", "Yes — e.g. DDR=6.032 -> deep_peck cycle"],
        ["Counterbore sequence", "Not recognized", "4-step: spot + twist_drill + cbore_mill (RF) + cbore_mill (FINISH)"],
        ["Rough + finish passes", "No", "Yes — separate RF and FINISH passes with stock-to-leave values"],
        ["Machine selection", "Not addressed", "Explicit: milling vs turning with reason"],
        ["Alternative process paths", "No", "Yes — both process_sequence (milling) and process_sequence_turning for bosses"],
        ["Planar face operations", "Not detected", "face_mill assigned to all 4 planar faces"],
        ["Reasoning stored", "No", "Yes — every step has a reason field"],
    ],
    col_widths=[60*mm, 65*mm, 78*mm]
)
story.append(t)

# Stage 3
story.append(Paragraph("Stage 3: Setup Planning", H3))
t = make_table(
    ["Dimension", "Jayanth", "Siddhant"],
    [
        ["Setup planning", "None", "4 setups defined"],
        ["Spindle direction", "Not computed", "Vector per setup (e.g. [0,1,0] = top face)"],
        ["WCS assignment", "None", "G54, G55, G56, G57 per setup"],
        ["Axis labels", "None", "+Y approach (top — default VMC position), etc."],
        ["Fixture notes", "None", "Per setup: vise, angle plate, step-jaw vise"],
        ["Feature to setup assignment", "None", "Each cluster has setup_id"],
        ["Machining sequence within setup", "None", "Ordered: bosses first -> counterbores -> planar faces"],
        ["Setup count", "0", "4"],
    ],
    col_widths=[60*mm, 65*mm, 78*mm]
)
story.append(t)
story.append(Spacer(1, 3))
story.append(Paragraph(
    "Setup breakdown: Setup 1 (+X, G54) — 8 clusters (bosses, counterbores, planar faces); "
    "Setup 2 (−X, G55) — 2 clusters (boss + through-hole); "
    "Setup 3 (+Y, G56) — 1 cluster (through-hole); "
    "Setup 4 (−Y, G57) — 1 cluster (slot, manual review).", NOTE))

# Stage 4
story.append(Paragraph("Stage 4: Tool Selection", H3))
t = make_table(
    ["Dimension", "Jayanth", "Siddhant"],
    [
        ["Tool type", "Generic (flat_endmill, twist_drill)", "Specific Sandvik Coromant catalog entries"],
        ["Tool ID", "None", "SPOT_090_3MM, DRILL_1.25MM, EM_2FL_3MM, FACEMILL_50MM"],
        ["Manufacturer", "None", "Sandvik Coromant throughout"],
        ["Product line", "None", "CoroDrill 460, SD503, CoroMill Plura, CoroMill 245"],
        ["Grade", "None", "H10F (drills), 1630 (end mills)"],
        ["Non-standard size handling", "Uses exact geometric diameter (1.96mm)", "Substitutes to nearest standard + flags with SUBSTITUTION note"],
        ["Turning tools", "None generated", "NOT_FOUND with ADD TO DATABASE flag — honest gap tracking"],
    ],
    col_widths=[60*mm, 65*mm, 78*mm]
)
story.append(t)

# Stage 5
story.append(Paragraph("Stage 5: Cutting Parameters", H3))
t = make_table(
    ["Dimension", "Jayanth", "Siddhant"],
    [
        ["Feed rate", "Basic mm/min values", "Computed from Vc x feed_per_rev -> VF mm/min"],
        ["Spindle speed (RPM)", "S4000, S8000, S9000 (fixed)", "Computed from Vc and tool diameter"],
        ["RPM capping", "Not present", "rpm_capped: true + actual_Vc_mmin shows if limit was hit"],
        ["Feed per tooth (fz)", "Not specified", "Present for milling tools"],
        ["Feed per rev", "Not specified", "Present for drilling tools"],
        ["Peck depth", "Q1.0 fixed", "Computed per feature"],
        ["ap / ae", "Not specified", "Present"],
        ["Cutting speed (Vc)", "Implicit", "Explicit: Vc_mmin: 90-800 depending on tool"],
    ],
    col_widths=[60*mm, 65*mm, 78*mm]
)
story.append(t)

# Stage 6
story.append(Paragraph("Stage 6: G-Code / CAM Output", H3))
t = make_table(
    ["Dimension", "Jayanth", "Siddhant"],
    [
        ["G-code produced", "Yes — 2 formats (part.gcode, part.ngc)", "PDF program sheet (no raw G-code file)"],
        ["Drilling G-code", "G83 peck cycle with X/Y/Z/Q/F", "Not in separate file"],
        ["Pocket G-code", "Rectangular bounding box traversal (.ngc)", "Not in separate file"],
        ["Boss G-code", "Stub: (2.5D profiling operation) — not actual geometry", "Not present"],
        ["G-code quality", "Partial — holes are real, pockets/bosses are stubs", "N/A"],
        ["Setup-aware G-code", "No (single flat program, no setup blocks)", "N/A"],
        ["Program sheet PDF", "No", "Yes — MIRROR_2.3MCYH2_2_program_sheet.pdf generated"],
    ],
    col_widths=[60*mm, 70*mm, 73*mm]
)
story.append(t)

# Scorecard
story.append(Paragraph("Summary Scorecard", H3))
t = make_table(
    ["Capability", "Jayanth", "Siddhant"],
    [
        ["Feature extraction from STEP", "YES — 25 features", "YES — 14 clusters"],
        ["Counterbore recognition", "NO — Misclassified as boss + hole", "YES — Correctly identified"],
        ["Confidence scoring", "NO", "YES"],
        ["Drawing parsing (tolerances, callouts)", "YES (pymupdf + OCR)", "NO"],
        ["Thread callout detection", "YES — M1.6 grounded to hole-4", "NO"],
        ["Multi-step process sequences", "NO — Single op per feature", "YES"],
        ["Rough + finish pass distinction", "NO", "YES"],
        ["DDR-based drill cycle selection", "NO", "YES"],
        ["Machine selection with reasoning", "NO", "YES"],
        ["Alternative turning paths", "NO", "YES"],
        ["Setup planning", "NO", "YES — 4 setups"],
        ["WCS assignment (G54-G57)", "NO", "YES"],
        ["Fixture notes", "NO", "YES"],
        ["Operation sequencing within setup", "NO", "YES"],
        ["Real tool catalog (Sandvik)", "NO — Generic only", "YES"],
        ["Non-standard size substitution flags", "NO", "YES"],
        ["Cutting parameters (RPM/VF/Vc)", "Partial", "YES — Full"],
        ["RPM capping / actual Vc tracking", "NO", "YES"],
        ["G-code output", "YES — Partial quality", "NO"],
        ["Program sheet PDF", "NO", "YES"],
        ["Pipeline event log + timestamps", "YES", "NO"],
        ["Formal ingestion/pipeline schema", "YES", "NO"],
    ],
    col_widths=[100*mm, 48*mm, 55*mm]
)
story.append(t)

story.append(Spacer(1, 5))
story.append(Paragraph("<b>Bottom line:</b>", BOLD))
story.append(Paragraph(
    "Jayanth is ahead on: drawing intelligence (OCR + callout grounding), G-code generation "
    "(even if partial), formal pipeline architecture with event logs, raw feature count coverage including pockets.", BODY))
story.append(Paragraph(
    "Siddhant is ahead on: counterbore recognition, multi-step process sequences, DDR logic, "
    "rough/finish pass distinction, machine selection reasoning, 4-setup fixturing plan with WCS, "
    "real tool catalog with manufacturer data, cutting parameter computation with RPM capping, and PDF program sheet.", BODY))
story.append(Paragraph(
    "The two pipelines are complementary: Jayanth's drawing parsing + G-code output combined with "
    "Siddhant's process planning + tool selection + setup logic would form a significantly more complete system.", BODY))

story.append(HRFlowable(width="100%", thickness=0.5, color=TABLE_BORDER, spaceBefore=8, spaceAfter=8))

# ── Section 2 ──────────────────────────────────────────────────────────────
story.append(Paragraph("2. Deep Analysis: MIRROR_2.3MCYH2_2 Pipeline (Latest Version)", H2))
story.append(Paragraph(
    "This section covers the most recent files: MIRROR_2.3MCYH2_2_processes.json and "
    "MIRROR_2.3MCYH2_2_setups.json.", BODY))

# Feature summary
story.append(Paragraph("Feature Summary (13 Clusters)", H3))
t = make_table(
    ["Cluster", "Feature Type", "Faces", "Axis", "Depth (mm)", "Radii (mm)", "Conf."],
    [
        ["0", "through_hole", "[11, 12]", "-Y", "1.3537", "[0.9306]", "high"],
        ["1", "through_hole", "[19,20,46,47]", "+X", "3.95", "[0.98]", "high"],
        ["2", "counterbore", "[22-26]", "-X", "7.6", "[0.63, 1.4]", "high"],
        ["3", "counterbore", "[27-31]", "-X", "7.6", "[0.63, 1.4]", "high"],
        ["4", "boss", "[4]", "-X", "0.5", "[2.0]", "high"],
        ["5", "boss", "[15,16,18,37,38,42,45,49]", "-X", "0.9322", "[3.0071]", "high"],
        ["6", "boss", "[32]", "+X", "0.5", "[2.0]", "high"],
        ["7", "planar_face", "[1]", "-", "-", "Area: 6.38 mm2", "high"],
        ["8", "planar_face", "[36]", "-", "-", "Area: 11.79 mm2", "high"],
        ["9", "planar_face", "[43]", "-", "-", "Area: 5.79 mm2", "high"],
        ["10", "planar_face", "[51]", "-", "-", "Area: 15.26 mm2", "high"],
        ["11", "background", "20 faces", "-", "-", "-", "high"],
        ["12", "slot", "[39, 41]", "+Y", "0.25", "[0.8]", "high"],
    ],
    col_widths=[18*mm, 28*mm, 30*mm, 14*mm, 22*mm, 35*mm, 16*mm]
)
story.append(t)

# Process sequences
story.append(Paragraph("Process Sequences — All Clusters", H3))

proc_data = [
    ("Cluster 0 — Through-Hole (d=1.861mm, depth=1.354mm)",
     [("Step 1", "spot_drill", "d=1.8612mm", "Locate for d=1.861mm hole — prevents drill wandering"),
      ("Step 2", "twist_drill", "d=1.8612mm, depth=1.354mm", "Standard drill cycle (DDR=0.727)")]),
    ("Cluster 1 — Through-Hole (d=1.96mm, depth=3.95mm)",
     [("Step 1", "spot_drill", "d=1.96mm", "Locate for d=1.960mm hole"),
      ("Step 2", "twist_drill", "d=1.96mm, depth=3.95mm", "Standard drill cycle (DDR=2.015)")]),
    ("Cluster 2 — Counterbore (bore d=1.26mm, cbore d=2.8mm, depth=7.6mm)",
     [("Step 1", "spot_drill", "d=1.26mm", "Locate for bore"),
      ("Step 2", "twist_drill", "d=1.26mm, depth=7.6mm", "Deep peck cycle (DDR=6.032)"),
      ("Step 3", "counterbore_mill (RF)", "d=2.8mm, stock leave 0.1mm XY+Z", "Roughing pass"),
      ("Step 4", "counterbore_mill (FINISH)", "d=2.8mm, stock leave 0.0mm", "Finishing pass")]),
    ("Cluster 3 — Counterbore (identical to Cluster 2)",
     [("Step 1", "spot_drill", "d=1.26mm", "Locate"),
      ("Step 2", "twist_drill", "d=1.26mm, depth=7.6mm", "Deep peck (DDR=6.032)"),
      ("Step 3", "counterbore_mill (RF)", "d=2.8mm, stock 0.1mm", "Roughing"),
      ("Step 4", "counterbore_mill (FINISH)", "d=2.8mm, stock 0.0mm", "Finishing")]),
    ("Cluster 4 — Boss (OD=4.0mm, h=0.5mm)",
     [("Step 1", "contour_mill (RF)", "d=4.0mm, depth=0.5mm, stock XY=0.1mm Z=0.1mm", "Roughing pass"),
      ("Step 2", "contour_mill (FINISH)", "d=4.0mm, depth=0.5mm, stock 0.0mm", "Finishing pass")]),
    ("Cluster 5 — Boss (OD=6.0142mm, h=0.9322mm)",
     [("Step 1", "contour_mill (RF)", "d=6.0142mm, depth=0.932mm, stock 0.1mm", "Roughing"),
      ("Step 2", "contour_mill (FINISH)", "d=6.0142mm, stock 0.0mm", "Finishing")]),
    ("Cluster 6 — Boss (OD=4.0mm, h=0.5mm — second location)",
     [("Step 1", "contour_mill (RF)", "d=4.0mm, stock 0.1mm", "Roughing"),
      ("Step 2", "contour_mill (FINISH)", "d=4.0mm, stock 0.0mm", "Finishing")]),
    ("Clusters 7-10 — Planar Faces",
     [("Step 1", "face_mill (FINISH)", "Single pass, stock 0.0mm", "Face mill flat datum surface — single finish pass")]),
    ("Cluster 11 — Background", [("—", "No operations", "20 faces", "Non-functional geometry")]),
    ("Cluster 12 — Slot (d=1.6mm, depth=0.25mm) — BLOCKED",
     [("Step 1", "manual_review", "BLOCKED", "Feature type slot has no process rule — review manually")]),
]

for title, steps in proc_data:
    story.append(Paragraph(title, BOLD))
    t = make_table(
        ["Step", "Operation", "Parameters", "Reason"],
        [[s[0], s[1], s[2], s[3]] for s in steps],
        col_widths=[14*mm, 45*mm, 65*mm, 79*mm]
    )
    story.append(t)
    story.append(Spacer(1, 3))

# Setups
story.append(Paragraph("Setups — Full Detail", H3))

setup_data = [
    ("Setup 1 — Right-Side Face (+X Approach) — G54",
     "[1.0, -0.0, -0.0]", "+X approach (right side face)",
     "Right-side face setup. Rotate part 90 degrees so right face is accessible. Clamp on bottom and left faces.",
     "90 degree angle plate or tombstone fixture",
     "G54, Origin: X=+, Y=CENTER, Z=TOP",
     "[2, 3, 4, 5, 7, 8, 9, 10] — 16 total operations",
     [("1", "Cluster 4 (boss)", "contour_mill RF -> FINISH"),
      ("2", "Cluster 5 (boss, large)", "contour_mill RF -> FINISH"),
      ("3", "Cluster 2 (counterbore)", "spot_drill -> twist_drill -> cbore RF -> cbore FINISH"),
      ("4", "Cluster 3 (counterbore)", "spot_drill -> twist_drill -> cbore RF -> cbore FINISH"),
      ("5", "Cluster 7 (planar face)", "face_mill"),
      ("6", "Cluster 8 (planar face)", "face_mill"),
      ("7", "Cluster 9 (planar face)", "face_mill"),
      ("8", "Cluster 10 (planar face)", "face_mill")]),
    ("Setup 2 — Left-Side Face (-X Approach) — G55",
     "[-1.0, 0.0, 0.0]", "-X approach (left side face)",
     "Left-side face setup. Rotate part 90 degrees so left face is accessible. Clamp on bottom and right faces.",
     "90 degree angle plate or tombstone fixture",
     "G55, Origin: X=-, Y=CENTER, Z=TOP",
     "[1, 6] — 4 total operations",
     [("1", "Cluster 1 (through-hole)", "spot_drill -> twist_drill"),
      ("2", "Cluster 6 (boss)", "contour_mill RF -> FINISH")]),
    ("Setup 3 — Top Face (+Y Approach, Default VMC) — G56",
     "[0.0, 1.0, -0.0]", "+Y approach (top — default VMC position)",
     "Standard top-face setup. Clamp part with top face accessible to spindle.",
     "Standard vise or fixture plate, jaws on side faces",
     "G56, Origin: X=CENTER, Y=CENTER, Z=TOP",
     "[0] — 2 total operations",
     [("1", "Cluster 0 (through-hole)", "spot_drill -> twist_drill")]),
    ("Setup 4 — Bottom Face (-Y, Part Flipped) — G57",
     "[-0.0, -1.0, 0.0]", "-Y approach (bottom — part flipped upside down)",
     "Flipped setup — part inverted so bottom face is accessible. Clamp on previously machined top features.",
     "Step-jaw vise or fixture plate with bosses as datums",
     "G57, Origin: X=CENTER, Y=CENTER, Z=TOP",
     "[12] — 1 operation (UNRESOLVED)",
     [("1", "Cluster 12 (slot)", "manual_review — BLOCKED")]),
]

for s in setup_data:
    title, spindle, axis, desc, fixture, wcs, clusters, seq = s
    story.append(Paragraph(title, BOLD))
    info_t = make_table(
        ["Field", "Value"],
        [["Spindle direction", spindle], ["Axis label", axis], ["Description", desc],
         ["Fixture", fixture], ["WCS / Origin", wcs], ["Clusters", clusters]],
        col_widths=[40*mm, 163*mm]
    )
    story.append(info_t)
    story.append(Spacer(1, 2))
    seq_t = make_table(
        ["#", "Cluster / Feature", "Operations"],
        [[r[0], r[1], r[2]] for r in seq],
        col_widths=[10*mm, 70*mm, 123*mm]
    )
    story.append(seq_t)
    story.append(Spacer(1, 5))

# Machine selection matrix
story.append(Paragraph("Machine Selection Decision Matrix", H3))
t = make_table(
    ["Feature Type", "Machine Selected", "Reason"],
    [
        ["All holes (through + counterbore)", "Milling (VMC)", "Drilling is a milling-machine operation"],
        ["Bosses", "Milling (VMC)", "PREFERRED_MACHINE=milling overrides turning option"],
        ["Planar faces", "Milling (VMC)", "Only option for planar faces"],
        ["Slot (cluster 12)", "Unknown", "No process rule defined for slot type"],
    ],
    col_widths=[55*mm, 45*mm, 103*mm]
)
story.append(t)
story.append(Paragraph(
    "Note: Turning alternatives (process_sequence_turning) are computed for all boss features but not executed.", META))

# MIRROR vs Hinge improvements
story.append(Paragraph("Key Improvements: MIRROR Version vs Earlier Hinge Version", H3))
t = make_table(
    ["Aspect", "Earlier Hinge_ files", "MIRROR_2.3MCYH2_2_ files"],
    [
        ["Counterbore operations", "3 steps (spot, drill, cbore_mill)", "4 steps — added separate RF and FINISH cbore passes"],
        ["Boss operations", "1 step (contour_mill)", "2 steps — contour_mill RF + contour_mill FINISH"],
        ["Planar face operations", "1 step (face_mill)", "1 step (face_mill, FINISH — unchanged)"],
        ["Stock-to-leave values", "Not present", "Added: 0.1mm RF, 0.0mm FINISH"],
        ["WCS codes", "Not assigned", "Added: G54, G55, G56, G57"],
        ["Slot feature", "Through-hole (two shallow bores)", "Correctly identified as slot -> manual_review"],
        ["Setup count", "4", "4 (same)"],
        ["Background faces", "20 faces in cluster_13", "20 faces in cluster_11 (renumbered)"],
    ],
    col_widths=[55*mm, 70*mm, 78*mm]
)
story.append(t)

# Open issues
story.append(Paragraph("Open Issues / Action Items", H3))
issues = [
    ("1. Cluster 12 (Slot) — BLOCKED",
     "Feature type slot has no process rule. Options: slot milling with end mill "
     "(d <= 1.6mm for 0.8mm corner radius), single-pass plunge at depth=0.25mm. "
     "Confirm if this is a functional or cosmetic feature."),
    ("2. Turning tool database gap",
     "rough_turn and finish_turn for boss ODs (d=4.0mm, d=6.0142mm) return NOT_FOUND. "
     "Add to tool library if turning capability is to be added."),
    ("3. Non-standard drill sizes — substitution required",
     "1.8612mm -> 1.9mm nearest standard; 1.96mm -> 2.0mm nearest standard; "
     "1.26mm -> 1.25mm nearest standard. All flagged with SUBSTITUTION notes — "
     "verify tolerances before confirming."),
    ("4. No G-code output yet",
     "Siddhant pipeline ends at PDF program sheet. G-code generation (like Jayanth part.gcode / "
     "part.ngc) is the next layer to build."),
    ("5. No drawing parsing",
     "Thread callouts (e.g. M1.6 tap on hole-4) are not extracted in Siddhant pipeline. "
     "Jayanth pymupdf-ocr-llm-parser handles this — integration needed."),
]
for title, body in issues:
    story.append(Paragraph(title, BOLD))
    story.append(Paragraph(body, NOTE))
    story.append(Spacer(1, 3))

story.append(HRFlowable(width="100%", thickness=0.5, color=TABLE_BORDER, spaceBefore=8, spaceAfter=4))
story.append(Paragraph(
    "Report compiled: 2026-03-21  |  "
    "Sources: MIRROR_2.3MCYH2_2_processes.json, MIRROR_2.3MCYH2_2_setups.json, "
    "out1.json, Hinge_tools.json, Hinge_params.json", META))

doc.build(story)
print("PDF written to:", OUTPUT_PATH)
