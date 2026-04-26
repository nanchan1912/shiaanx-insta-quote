# ShiaanX Product Context

This file provides permanent context about ShiaanX for Claude Code. Read this before working on anything in this project.


## What ShiaanX Is

ShiaanX is an AI-driven intelligent manufacturing company building software to automate the end-to-end journey from CAD file to finished precision part. The vision is a single platform where a STEP file comes in and the system handles everything downstream — process planning, CAM, execution tracking, inspection, shipping — with humans in the loop for exceptions or supervision only.

Think Hadrian, Daedalus, Isembard, built for India, starting with software-defined process intelligence before owning machines.

**Company Details:**
- Stage: MVP / Early product
- Target sectors: Energy, Defense, Aerospace, Drones (starting point)
- Geography: India-first, with international clients in scope from Phase 1
- Revenue model: Manufacturing-led (not software-led); partner-based operations in Phase 1, moving to hybrid ownership

**What ShiaanX is NOT:**
- Not a CAM software (we sit above it)
- Not a job portal or marketplace for machining
- Not competing on price — competing on reliability, traceability, and engineering depth


## The Problem Being Solved

Precision manufacturing for advanced industries (aerospace, drones, defence) is bottlenecked by manual, knowledge-intensive process planning. Getting from a CAD file to a correct first part requires experienced engineers making hundreds of decisions — machine selection, tooling, setup sequence, feeds and speeds, fixturing — most of which live in people's heads, not systems. This causes slow quotes, long lead times, high scrap rates, and inconsistent quality. ShiaanX is creating a closed loop to learn from every cut and automate many of these decisions.


## Product — The Pipeline

We will be building many core technical systems like automating process planning, building a factory OS, building closed loop system, instant quotation, Scheduling, Digital Twin of factory and supply chain integration.  
In this project, the system we are building is an automated CAD-to-part process planning pipeline.

- Target materials (priority): Aluminium 6061
- Target machine type (initial): 3-axis VMC

All pipeline scripts live in: `Claude output for program sheet/`
Python environment: conda env named `occ` (create from `environment.yml`)
Run example (PowerShell): `conda run -n occ python "10. run_pipeline.py" "<path-to.step>"`
Git repo: `https://github.com/siddhantg2311/shiaanx-CAPP` (branch: main)

**Important:** Script filenames contain spaces and numbers (e.g. `4. process_selection.py`).
Import them with `importlib.util.spec_from_file_location`, not normal imports.

---

### Pipeline Stage Map

| # | Script | Input | Output | Status |
|---|--------|-------|--------|--------|
| 1 | `1. extract_features.py` | `.step` file | `*_features.json` | Done |
| 2 | `2. cluster_features.py` | `*_features.json` | `*_clustered.json` | Done |
| 3 | `3. classify_features.py` | `*_clustered.json` | `*_classified.json` | Done |
| 4 | `4. process_selection.py` | `*_classified.json` | `*_processes.json` | Done |
| 5 | `5. setup_planning.py` | `*_processes.json` | `*_setups.json` | Done |
| 6 | `7. tool_selection.py` | `*_setups.json` | `*_tools.json` | Done |
| 7 | `8. parameter_calculation.py` | `*_tools.json` | `*_params.json` | Done |
| 8 | `9. program_sheet.py` | `*_params.json` | `*_program_sheet.pdf` | Done |
| — | `10. run_pipeline.py` | `.step` file | PDF (end-to-end runner) | Done |

Helper modules: `coord_system.py`, `feature_graph.py`, `geometry_utils.py`
Tool database: `7a. tool_database.json` (v2.0, 42 tools)

---

### What Each Module Does (Current Capabilities)

**`1. extract_features.py`**
Parses a STEP file with PythonOCC. Extracts every face with its surface type (Plane, Cylinder, Cone, Torus, BSpline), geometry parameters, area, normal/axis, and adjacency graph. Outputs `bounding_box`, `mass_properties`, `topology_counts`.

**`2. cluster_features.py`**
Groups adjacent faces into feature clusters using the adjacency graph. Passes through `bounding_box`, `mass_properties`, `topology_counts` so downstream stages have full part geometry.

**`3. classify_features.py`**
Assigns a feature type to each cluster from 25 MFCAD++ classes (through_hole, blind_hole, chamfer, pocket, boss, counterbore, large_bore, planar_face, etc.) using geometry heuristics. Currently rule-based — candidate for ML replacement.

**`4. process_selection.py`**
Maps each feature type + dimensions to an ordered operation sequence. Rules sourced from Machinery's Handbook (29th ed.) and Chang & Wysk.
- Holes (d < 1mm): micro_drill
- Holes (1–13mm): spot_drill → twist_drill
- Holes (13–32mm): spot_drill → pilot_drill → core_drill
- Holes (> 32mm): circular_interp or boring_bar
- DDR ≤ 3: standard cycle / 3–5: peck / > 5: deep peck
- Tapped holes: spot_drill → twist_drill (ISO 68-1 pilot size) → tap_rh
- Chamfer: single chamfer_mill pass
- Boss: contour_mill (RF + FINISH)
- Pocket/slot: pocket_mill (RF + FINISH + optional CORNER_R)
- Face: face_mill (single pass or RF + FINISH if depth > max_ap)
- Material stock-to-leave table for RF passes (aluminium, steel, titanium, brass)

**`5. setup_planning.py`**
Groups clusters into machine setups by feature axis direction (principal axis heuristic). For each setup produces:
- Spindle direction, axis label, WCS assignment (G54–G59)
- WCS origin: CORNER zero (when part at CAD origin) or CENTER zero
- Workholding config: type (vise/step_jaw_vise/angle_plate/sine_plate/fixture_plate), clamp_faces, rest_face, clearance_faces, jaw_opening_mm, datum_from_setup
- Stock state: raw_billet (setup 1) or previous_setup with remaining_faces tracking
- Rotation info for angled setups

**`7. tool_selection.py`**
Assigns a tool from `7a. tool_database.json` to every operation step.
- Spot/center drill: smallest center drill that covers the hole diameter
- Twist/pilot/core drill: nearest standard metric size with substitution warning
- End mills: smallest tool >= required diameter
- Face mills: smallest face mill >= feature diameter
- Chamfer mills: smallest chamfer mill >= feature diameter
- Slot mills: smallest slot mill >= slot width
- Taps: exact match by thread diameter
- Boring bar: exact diameter match
- All lookups: material-specific Vc and fz from tool_database

**`8. parameter_calculation.py`**
Computes RPM, Vf (feed rate mm/min), ap (axial depth), ae (radial depth) for every operation. Caps RPM at machine max (default 10,000). Applies ramp/plunge feed reductions from tool ramp_plunge data.

**`9. program_sheet.py`**
Generates a PDF program sheet. Includes:
- Job header (part name, material, date, programmer)
- Per-setup pages with WCS, workholding, stock state, tool list
- Operation sequence table with toolpath names in format: `[MATERIAL] [DIA] [TOOL TYPE] [FEATURE] [PASS TYPE]`
  e.g. `ALU 6 ENDMILL OUTER PROFILE RF`
- Warnings for substituted drill sizes, RPM caps, manual review items

**`10. run_pipeline.py`**
End-to-end runner: takes a STEP file path, runs all 8 stages in sequence, writes timestamped log to `logs/pipeline_YYYYMMDD_HHMMSS.log` with per-stage timing.

**`geometry_utils.py`**
Geometric helpers (face adjacency, cylinder depth, radius extraction). Also contains:
- `compress_step_bytes(path)` → zlib bytes for API upload
- `compress_step_file()` / `decompress_step_file()` for disk-based compression
- CLI: `python geometry_utils.py compress <file.step>`

---

### Tool Database (`7a. tool_database.json`) — v2.0

42 tools across these types:

| Type | Operation key | Count | Notes |
|---|---|---|---|
| End mills (2-flute, carbide) | `contour_mill` / `pocket_mill` | 15 | 1–25mm, with ramp_plunge data |
| Face mills (indexable) | `face_mill` | 3 | 50/63/80mm |
| Spot / center drills | `center_drill` | 3 | 90°, 4/6/10mm shank |
| Jobber drills (carbide) | `twist_drill` | 12 | 0.5–20mm standard sizes |
| Chamfer mills | `chamfer_mill` | 3 | 90°, 6/8/10mm |
| Slot mills | `slot_mill` | 3 | 4/6/8mm |
| Taps (rigid, RH) | `tap_rh` | 5 | M2–M6, feed_per_rev = pitch |

All tools: metric, Sandvik Coromant / Kennametal sourced, aluminium material_params with Vc_rough, Vc_finish, fz_rough, fz_finish.
Material aliases: `aluminium_6061/6063/6082/7075/7050` → `aluminium`.

---

### Dataset

MFCAD++ dataset: `Claude output for program sheet/Dataset/MFCAD_dataset/MFCAD++_dataset/`
- 8,949 STEP files in `step/test/`
- 25 feature classes defined in `feature_labels.txt`
- Excluded from git (too large — see AD-008)
- Test parts processed so far: `step/test/21/`, `step/test/25/`


## Competitors to Be Aware Of

Hadrian, Daedalus, Isembard, CloudNC (CAM Assist), Forge Automation, Jeh Aerospace, Limitless CNC


## Architectural Decisions

Document every major decision here so future sessions don't relitigate them.

---

### AD-001 — Tool database does not store machine-specific post-process fields
**Date:** 2026-04-11
**Decision:** `tool_number`, `length_offset`, `diameter_offset`, `turret_position` are NOT stored in `tool_database.json`.
**Reason:** These are machine-specific assignments (a 6mm end mill is T03 on one machine, T07 on another). They belong in a job setup sheet generated at runtime, not in the tool definition.

---

### AD-002 — Toolpath.ai inch-based tool library will not be imported
**Date:** 2026-04-11
**Decision:** No translation layer will be built to import Toolpath's `toolpath_generic_tools.json` into our schema.
**Reason:** Their library is inch-based (US tooling, US machines). Geometry converts cleanly but feeds/speeds do not — values are calibrated to different machine characteristics. Risk of incorrect cutting parameters on shop floor is too high for aerospace parts. We populate the database directly from Sandvik/Kennametal metric catalogues where every value is traceable.

---

### AD-003 — Tool database is metric-first, Sandvik/Kennametal sourced
**Date:** 2026-04-11
**Decision:** All tool parameters derived from published Sandvik Coromant and Kennametal catalogues for aluminium alloys (6061, 7075). Values are conservative mid-range recommendations.
**Reason:** Traceable, verified source. India machining shops use metric tooling. Catalogue values are a safe starting point before shop-specific tuning.

---

### AD-004 — Ramp/plunge stored as percentages, not absolute feed rates
**Date:** 2026-04-11
**Decision:** `ramp_plunge` in tool entries stores `vf_ramp_pct_of_feed` and `vf_plunge_pct_of_feed` as percentages of the normal cutting feed, not absolute mm/min values.
**Reason:** Absolute values would need to be different per material (aluminium vs steel). Percentages let `parameter_calculation.py` compute the actual feed rate at runtime after the material and cutting feed are known.

---

### AD-005 — Bounding box and metadata must flow through the full pipeline
**Date:** 2026-04-11
**Decision:** `cluster_features.py` explicitly passes through `bounding_box`, `mass_properties`, `file`, and `topology_counts` from the features JSON into its output.
**Reason:** Downstream steps (setup_planning, workholding, WCS origin) need part geometry. Originally these were dropped at the clustering stage, causing `jaw_opening_mm` and `wcs_origin_mm` to always be null.

---

### AD-006 — WCS origin uses CORNER zero when part is placed at CAD origin
**Date:** 2026-04-11
**Decision:** If `xmin ≈ 0` (within 2% of part dimension), the WCS origin is set to the CAD-origin corner rather than part centre.
**Reason:** Most MFCAD parts and manufactured parts place the bounding box min at (0,0,0). Corner zero keeps all G-code coordinates positive, which is easier for the machinist to verify and reduces sign errors. Centre zero is used for parts not aligned to the CAD origin.

---

### AD-007 — shiaanx-backend is a separate independent git repo
**Date:** 2026-04-11
**Decision:** `shiaanx-backend/` (another developer's work) is NOT included in the `shiaanx-CAPP` repo. It lives at `github.com/siddhantg2311/shiaanx-backend` independently.
**Reason:** It has its own `.git` folder and remote. The two codebases serve different purposes and have different owners. Git treats it as a nested repo and skips it automatically.

---

### AD-008 — Dataset STEP files excluded from git
**Date:** 2026-04-11
**Decision:** `Claude output for program sheet/Dataset/` is in `.gitignore` and not committed.
**Reason:** 8,949 STEP files are too large for a git repo. The MFCAD++ dataset is a standard public dataset that can be re-downloaded. Only intermediate pipeline outputs for specific tested parts (e.g. `Basic Design/`, `Botlabs Hub/`, `Botlabs Hinge/`) are committed.
