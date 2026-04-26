# Rule Sheet Changelog

---

## 2026-04-20 — Face milling rules from strategy sheet (Gaurav)

**Source:** `ShiaanX_Strategy_Rules_Final for face.xlsx` (Senior CAM programmer Gaurav, validated 2024-05-15, Job ID JOB-20240515-003)
**Scope:** Facing operation only. Sheet covers 22 rules across AL6061-T6 on VMC-001 (BT40, 8000 RPM max).

### Files changed

- `rule_sheets/02_process_selection.json`
- `rule_sheets/04_cutting_parameters.json`
- `4. process_selection.py`
- `8. parameter_calculation.py`

---

### Change 1 — Face subtypes added (`02_process_selection.json`)

**Before:** All `planar_face` clusters treated identically — no distinction by surface context.

**After:** Three subtypes detected from adjacency data:

| Subtype | Condition | Entry method |
|---------|-----------|--------------|
| `full_surface` | No adjacent holes/pockets/bosses | outside |
| `boss_surface` | Adjacent to a boss cluster | outside |
| `interrupted` | Adjacent to holes, pockets, or slots breaking the face | helical |

Helical entry for interrupted faces avoids shock load when the tool re-enters interrupted material.

---

### Change 2 — Width-band tool selection for face milling (`02_process_selection.json`)

**Before:** Tool selection picked the smallest face_mill ≥ feature width. No switch between end mill and face mill based on width.

**After:** Explicit width bands (full_surface and interrupted):

| Width | Tool type | Diameter | Flutes |
|-------|-----------|----------|--------|
| < 10mm | end_mill | 5mm | 3F |
| 10–150mm | end_mill | 10mm | 4F |
| 150–300mm | face_mill | 25mm | 4F |
| > 300mm | face_mill | 50mm | 6F |

Boss surface overrides (narrower — boss faces are smaller):

| Width | Tool | Diameter |
|-------|------|----------|
| < 20mm | end_mill | 10mm 4F |
| 20–100mm | end_mill | 15mm 4F |
| > 100mm | face_mill | 25mm 4F |

---

### Change 3 — Face stock-to-leave (Z only) (`02_process_selection.json`, `4. process_selection.py`)

**Before:** RF pass left 0.1mm XY stock (from general `MATERIAL_STOCK_TABLE`). Facing is axial — XY stock is irrelevant.

**After:** RF pass leaves 0.2mm in Z only. XY stock set to 0 for face_mill RF.

| Material | Z stock after RF |
|----------|-----------------|
| All aluminium grades | 0.2mm |
| Mild steel / steel | 0.15mm |
| Stainless steel | 0.2mm |
| Titanium | 0.15mm |

---

### Change 4 — Stepover (ae%) for face milling (`04_cutting_parameters.json`, `8. parameter_calculation.py`)

**Before:** Generic `ae_fraction` from tool DB (~75%), or `ae_max`.

**After:** Pass-type-specific stepover, overrides tool DB for `face_mill` operation:

| Pass | Stepover |
|------|----------|
| RF (rough) | 60% of tool diameter |
| FINISH | 40% of tool diameter |

---

### Change 5 — Axial depth of cut (DOC) for face milling (`04_cutting_parameters.json`, `8. parameter_calculation.py`)

**Before:** Used tool DB `ap_max` (up to 2mm for aluminium), no pass-type split.

**After:** Explicit DOC by pass type for `face_mill`:

| Pass | DOC |
|------|-----|
| RF (rough) | 1.0mm |
| FINISH | 0.5mm |

---

### Change 6 — Coolant per operation type (`04_cutting_parameters.json`, `8. parameter_calculation.py`)

**Before:** Single global coolant (default `through_spindle`) applied to all operations.

**After:** Each step now carries a `coolant` field with operation-level overrides:

| Operation | Coolant |
|-----------|---------|
| face_mill, contour_mill, pocket_mill, slot_mill, chamfer_mill, tap_rh | flood |
| twist_drill, micro_drill, pilot_drill, core_drill, boring_bar | through_spindle |

---

### Change 7 — Spring pass documented (`04_cutting_parameters.json`)

**What it is:** A repeat of the finish pass at zero additional DOC, recovering elastic deflection that occurred on the first finish pass.

**Status:** Not yet implemented in pipeline (no code change). Documented in `04_cutting_parameters.json → spring_pass_note` for future addition as `pass_type: SPRING`.

---

### What the excel sheet does NOT cover (to be done when Gaurav extends the sheet)

- Pockets, slots, bores, chamfers — separate rule sets needed
- Other materials (SS316L, Ti6Al4V) — Gaurav flagged these in the Data_Dictionary tab as separate parameter regimes
- Spring pass implementation as a third operation step in the pipeline
- Machine-specific RPM cap (VMC-001 = 8000 RPM BT40) — belongs in machine config, not rule sheets
