ShiaanX rule sheets (JSON)
==========================

Location (this folder):
  Claude output for program sheet/rule_sheets/

All seven canonical sheets exist as JSON. Python pipeline modules still embed the
same logic until a loader reads these files.

Files (sheet # — purpose — source module):
  07_label_taxonomy.json — Sheet 7: MFCAD++ id -> internal_feature_type
  01_feature_classification.json — Sheet 1: classify thresholds + decision order (3. classify_features.py)
  02_process_selection.json    — Sheet 2: drill bands, DDR, stock, tap table (4. process_selection.py)
  03_tool_matching_policy.json — Sheet 3: diameter resolution + DB query policy (7. tool_selection.py)
  04_cutting_parameters.json   — Sheet 4: RPM/Vf/ap/ae/peck machine rules (8. parameter_calculation.py + DB)
  05_setup_planning.json       — Sheet 5: grouping, WCS, stock state (5. setup_planning.py)
  06_workholding.json          — Sheet 6: vise/plate/sine templates (5. setup_planning.py)

Conventions:
  - Top-level keys: schema_version, ruleset_id, updated_at (optional), description
  - See SESSION_STATE.md in repo root for human-readable explanations and examples
