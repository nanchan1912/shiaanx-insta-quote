"""
generate_rule_docs.py — Rule sheet docs in two modes.

  MODE 1 — static HTML export (for git / email / sharing):
      python "rule_sheets/generate_rule_docs.py"
      → writes rule_sheets/RULES.html

  MODE 2 — live Streamlit dashboard (for real-time demos):
      streamlit run "rule_sheets/generate_rule_docs.py"
      → opens browser at localhost:8501, auto-refreshes every 3 s

The dashboard is the external-facing view: as the ML model adds rules to
the JSON files, the page updates live without anyone pressing anything.
"""

from __future__ import annotations
import json, sys
from pathlib import Path
from datetime import date
import pandas as pd

SHEET_DIR = Path(__file__).parent

SHEETS = [
    ("01_feature_classification.json", "01", "Feature Classification"),
    ("02_process_selection.json",      "02", "Process Selection"),
    ("03_tool_matching_policy.json",   "03", "Tool Matching Policy"),
    ("04_cutting_parameters.json",     "04", "Cutting Parameters"),
    ("05_setup_planning.json",         "05", "Setup Planning"),
    ("06_workholding.json",            "06", "Workholding / Fixtures"),
    ("07_label_taxonomy.json",         "07", "Label / Taxonomy Map"),
]

# ── shared data helpers ───────────────────────────────────────────────────────

def load(filename: str) -> dict:
    with open(SHEET_DIR / filename, encoding="utf-8") as f:
        return json.load(f)

def load_all() -> dict[str, dict]:
    return {fname: load(fname) for fname, *_ in SHEETS}

def is_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False

# ── shared data extractors (used by both modes) ───────────────────────────────

def taxonomy_df(d: dict) -> pd.DataFrame:
    rows = []
    for m in d.get("mappings", []):
        rows.append({
            "ID":                  m["mfcad_id"],
            "MFCAD++ Name":        m["mfcad_name"],
            "Internal Type":       m["internal_feature_type"],
            "Classifier emits":    "✓" if m.get("classify_features_emits") else "✗",
            "Process rule":        "✓" if m.get("process_selection_ready") else "✗",
            "Notes":               m.get("warnings") or "",
        })
    return pd.DataFrame(rows)

def thresholds_df(d: dict) -> pd.DataFrame:
    rows = []
    for key, t in d.get("thresholds_mm", {}).items():
        rows.append({"Parameter": key, "Value": t["value"], "Unit": t["unit"], "Role": t["role"]})
    return pd.DataFrame(rows)

def drill_bands_df(d: dict) -> pd.DataFrame:
    b = d.get("drill_diameter_bands_mm", {})
    pf = b.get("pilot_diameter_fraction_of_final", 0.6)
    micro = b.get("micro_drill_max_exclusive", 1.0)
    twist = b.get("twist_drill_max_inclusive", 13.0)
    core  = b.get("core_drill_max_inclusive", 32.0)
    return pd.DataFrame([
        {"Band": "Micro", "Diameter range": f"d < {micro} mm",       "Operations": "micro_drill"},
        {"Band": "Twist", "Diameter range": f"{micro}–{twist} mm",   "Operations": "spot_drill → twist_drill"},
        {"Band": "Core",  "Diameter range": f"{twist}–{core} mm",    "Operations": f"spot_drill → pilot_drill ({int(pf*100)}% D) → core_drill"},
        {"Band": "Large", "Diameter range": f"> {core} mm",          "Operations": "circular_interp or boring_bar"},
    ])

def ddr_df(d: dict) -> pd.DataFrame:
    ddr = d.get("ddr_drill_cycle", {})
    std = ddr.get("ddr_standard_max_inclusive", 3.0)
    pck = ddr.get("ddr_peck_max_inclusive", 5.0)
    return pd.DataFrame([
        {"DDR range": f"≤ {std}",    "Drill cycle": "Standard (G81)"},
        {"DDR range": f"≤ {pck}",    "Drill cycle": "Peck (G83)"},
        {"DDR range": f"> {pck}",    "Drill cycle": "Deep peck"},
    ])

def stock_to_leave_df(d: dict) -> pd.DataFrame:
    rows = []
    for mat, vals in d.get("material_stock_to_leave_mm", {}).get("per_material", {}).items():
        rows.append({"Material": mat, "XY stock (mm)": vals["xy"], "Z stock (mm)": vals["z"]})
    return pd.DataFrame(rows)

def tap_drill_df(d: dict) -> pd.DataFrame:
    rows = []
    for k, v in d.get("tap_drill_table_mm", {}).items():
        if k != "comment":
            rows.append({"Thread": f"M{k}", "Tap drill (mm)": v})
    return pd.DataFrame(rows)

def tool_policy_df(d: dict) -> pd.DataFrame:
    rows = []
    for op, policy in d.get("diameter_resolution", {}).items():
        if isinstance(policy, dict):
            rule = policy.get("rule", "")
            frac = (policy.get("target_fraction_of_bore_diameter")
                    or policy.get("target_fraction_of_boss_diameter")
                    or policy.get("default_feature_diameter_mm_if_missing"))
            rows.append({"Operation": op, "Rule": rule, "Fraction / default": frac or "—"})
        else:
            rows.append({"Operation": op, "Rule": str(policy), "Fraction / default": "—"})
    return pd.DataFrame(rows)

def peck_fractions_df(d: dict) -> pd.DataFrame:
    rows = []
    for coolant, fracs in d.get("peck_fractions_of_tool_diameter", {}).items():
        if coolant != "comment":
            rows.append({"Coolant": coolant, "Peck Q": f"{fracs['peck']} × D", "Deep peck Q": f"{fracs['deep_peck']} × D"})
    return pd.DataFrame(rows)

def workholding_df(d: dict) -> pd.DataFrame:
    rows = []
    for t in d.get("principal_spindle_templates", []):
        ftype = t.get("type") or f"{t.get('type_setup_1')} / {t.get('type_later')}"
        rows.append({
            "Approach":       t.get("label_hint", ""),
            "Fixture":        ftype,
            "Clamp faces":    str(t.get("clamp_faces", "")),
            "Rest face":      str(t.get("rest_face", "")),
            "Jaw opening":    t.get("jaw_opening_mm_from_bbox", ""),
        })
    return pd.DataFrame(rows)

# ── MODE 2: Streamlit dashboard ───────────────────────────────────────────────

def run_dashboard():
    import streamlit as st

    st.set_page_config(
        page_title="ShiaanX — Rule Sheets",
        page_icon="⚙️",
        layout="wide",
    )

    # Auto-refresh every 3 seconds so the page updates as JSON files change
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=3000, key="rule_refresh")
    except ImportError:
        st.info("Install `streamlit-autorefresh` for live updates: `pip install streamlit-autorefresh`")

    all_data = load_all()

    # ── Header ────────────────────────────────────────────────────────────────
    st.title("⚙️ ShiaanX CAPP — Rule Sheets")
    st.caption(f"Live view of rule_sheets/ · Last loaded: {date.today().isoformat()}")

    # ── Coverage summary cards ────────────────────────────────────────────────
    taxonomy = all_data.get("07_label_taxonomy.json", {})
    mappings = taxonomy.get("mappings", [])
    n_classifier = sum(1 for m in mappings if m.get("classify_features_emits"))
    n_process    = sum(1 for m in mappings if m.get("process_selection_ready"))
    total        = len(mappings)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Feature classes (MFCAD++)", total)
    c2.metric("Classifier coverage", f"{n_classifier}/{total}", delta=f"{n_classifier-total} gap" if n_classifier < total else "✓ complete")
    c3.metric("Process rules wired",  f"{n_process}/{total}",    delta=f"{n_process-total} gap"    if n_process    < total else "✓ complete")
    c4.metric("Rule sheets",          len(SHEETS))

    st.divider()

    # ── Sheet tabs ────────────────────────────────────────────────────────────
    tab_labels = [f"{num} · {name}" for _, num, name in SHEETS]
    tabs = st.tabs(tab_labels)

    # ── Sheet 01: Classification ──────────────────────────────────────────────
    with tabs[0]:
        d = all_data["01_feature_classification.json"]
        st.subheader("Feature Classification Rules")
        st.caption(d.get("description", ""))

        st.markdown("**Decision priority**")
        for i, step in enumerate(d.get("decision_priority", []), 1):
            st.markdown(f"{i}. {step}")

        st.markdown("**Thresholds**")
        st.dataframe(thresholds_df(d), use_container_width=True, hide_index=True)

        pr = d.get("pocket_rules", {})
        if pr:
            st.markdown(
                f"**Pocket detection:** min {pr['min_perpendicular_wall_count']} perpendicular walls · "
                f"high-confidence at {pr['confidence_high_min_perp_walls']} walls"
            )

        with st.expander("Warnings / notes"):
            for w in (d.get("warnings") or []):
                st.warning(w)

    # ── Sheet 02: Process selection ───────────────────────────────────────────
    with tabs[1]:
        d = all_data["02_process_selection.json"]
        st.subheader("Process Selection Rules")
        st.caption(d.get("description", ""))

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Drill diameter bands**")
            st.dataframe(drill_bands_df(d), use_container_width=True, hide_index=True)
            st.markdown("**DDR → drill cycle**")
            st.dataframe(ddr_df(d), use_container_width=True, hide_index=True)
        with col2:
            st.markdown("**ISO 68-1 tap drill table**")
            st.dataframe(tap_drill_df(d), use_container_width=True, hide_index=True)

        st.markdown("**Material stock-to-leave for roughing (mm)**")
        st.dataframe(stock_to_leave_df(d), use_container_width=True, hide_index=True)

        with st.expander("Warnings / notes"):
            for w in (d.get("warnings") or []):
                st.warning(w)

    # ── Sheet 03: Tool matching ───────────────────────────────────────────────
    with tabs[2]:
        d = all_data["03_tool_matching_policy.json"]
        st.subheader("Tool Matching Policy")
        st.caption(d.get("description", ""))

        tol = d.get("tolerances_mm", {})
        c1, c2 = st.columns(2)
        c1.metric("Drill exact match tolerance", f'{tol.get("drill_exact_match")} mm')
        c2.metric("Counterbore endmill tolerance", f'{tol.get("counterbore_endmill_exact")} mm')

        st.markdown("**Selection rules by operation**")
        st.dataframe(tool_policy_df(d), use_container_width=True, hide_index=True)

        no_tool = d.get("database_query_policy", {}).get("no_tool_steps", [])
        if no_tool:
            st.info(f"No-tool steps: {', '.join(no_tool)}")

        with st.expander("Warnings / notes"):
            for w in (d.get("warnings") or []):
                st.warning(w)

    # ── Sheet 04: Cutting params ──────────────────────────────────────────────
    with tabs[3]:
        d = all_data["04_cutting_parameters.json"]
        st.subheader("Cutting Parameter Rules")
        st.caption(d.get("description", ""))

        md = d.get("machine_defaults", {})
        c1, c2, c3 = st.columns(3)
        c1.metric("Max spindle RPM", md.get("max_spindle_rpm"))
        c2.metric("Default coolant", md.get("coolant_default"))
        c3.metric("Allowed modes", len(md.get("coolant_allowed", [])))

        st.markdown("**Peck depth fractions (G83)**")
        st.dataframe(peck_fractions_df(d), use_container_width=True, hide_index=True)

        tsc = d.get("tsc_small_drill_boost", {})
        st.info(
            f"TSC speed boost: drills < {tsc.get('max_tool_diameter_mm_exclusive')} mm "
            f"with through-spindle coolant → Vc × {tsc.get('vc_multiplier')}"
        )

        st.markdown("**Formulas**")
        for name, expr in d.get("formulas", {}).items():
            st.code(f"{name}: {expr}", language=None)

        with st.expander("Warnings / notes"):
            for w in (d.get("warnings") or []):
                st.warning(w)

    # ── Sheet 05: Setup planning ──────────────────────────────────────────────
    with tabs[4]:
        d = all_data["05_setup_planning.json"]
        st.subheader("Setup Planning Rules")
        st.caption(d.get("description", ""))

        wcs = d.get("wcs", {})
        st.markdown(f"**WCS sequence:** {' → '.join(wcs.get('sequence', []))}")

        czh = d.get("wcs_origin_corner_heuristic", {})
        st.markdown(
            f"**Corner-zero heuristic (AD-006):** {czh.get('fraction_of_dimension', 0)*100:.0f}% tolerance — "
            f"{czh.get('rule', '')}"
        )

        st.markdown("**Grouping algorithm**")
        for i, step in enumerate(d.get("grouping_algorithm", []), 1):
            st.markdown(f"{i}. {step}")

        ss = d.get("stock_state", {})
        st.markdown(
            f"**Stock carryover:** Setup 1 = `{ss.get('setup_1_type')}` · "
            f"Later = `{ss.get('later_setups')}` · {ss.get('machined_face_accumulation', '')}"
        )

        with st.expander("Warnings / notes"):
            for w in (d.get("warnings") or []):
                st.warning(w)

    # ── Sheet 06: Workholding ─────────────────────────────────────────────────
    with tabs[5]:
        d = all_data["06_workholding.json"]
        st.subheader("Workholding / Fixture Rules")
        st.caption(d.get("description", ""))

        dc = d.get("datum_cascade", {})
        st.markdown(f"**Datum rule:** {dc.get('datum_from_setup', '')}")

        st.markdown("**Principal spindle templates**")
        st.dataframe(workholding_df(d), use_container_width=True, hide_index=True)

        ang = d.get("angled_setup", {}).get("template", {})
        if ang:
            st.markdown(
                f"**Angled setup:** `{ang.get('type')}` · "
                f"Clamp: {ang.get('clamp_faces')} · Rest: {ang.get('rest_face')}"
            )

        fb = d.get("fallback", {})
        st.warning(f"Fallback: `{fb.get('type')}` — {fb.get('notes', '')}")

        with st.expander("Warnings / notes"):
            for w in (d.get("warnings") or []):
                st.warning(w)

    # ── Sheet 07: Taxonomy ────────────────────────────────────────────────────
    with tabs[6]:
        d = all_data["07_label_taxonomy.json"]
        st.subheader("Label / Taxonomy Map — MFCAD++ bridge")
        st.caption(d.get("description", ""))

        df = taxonomy_df(d)
        def colour_check(val):
            if val == "✓": return "color: green; font-weight: bold"
            if val == "✗": return "color: #cc0000"
            return ""
        styled = df.style.applymap(colour_check, subset=["Classifier emits", "Process rule"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

        with st.expander("Warnings / notes from rule sheet"):
            for w in (d.get("warnings") or []):
                st.warning(w)


# ── MODE 1: static HTML export ────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       font-size: 14px; color: #1a1a2e; background: #f8f9fa; line-height: 1.5; }
header { background: #1a1a2e; color: #fff; padding: 28px 40px 20px; }
header h1 { font-size: 22px; font-weight: 700; }
header p  { font-size: 12px; color: #aab4c8; margin-top: 4px; }
nav { background: #fff; border-bottom: 1px solid #e0e0e0;
      padding: 10px 40px; display: flex; flex-wrap: wrap; gap: 8px; }
nav a { color: #2563eb; text-decoration: none; font-size: 13px; font-weight: 500;
        padding: 4px 10px; border-radius: 4px; background: #eff6ff; }
nav a:hover { background: #dbeafe; }
main { max-width: 1100px; margin: 0 auto; padding: 32px 40px; }
section { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
          margin-bottom: 32px; padding: 28px 32px; }
section h2 { font-size: 17px; font-weight: 700; border-bottom: 2px solid #2563eb;
             padding-bottom: 8px; margin-bottom: 16px; }
h3 { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
     color: #374151; margin: 20px 0 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 12px; }
th { background: #f1f5f9; font-weight: 700; text-align: left; color: #374151;
     padding: 8px 12px; border: 1px solid #e5e7eb; }
td { padding: 7px 12px; border: 1px solid #e5e7eb; vertical-align: top; }
tr:nth-child(even) td { background: #f9fafb; }
code { background: #f1f5f9; color: #1d4ed8; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.yes { color: #16a34a; font-weight: 700; }
.no  { color: #dc2626; font-weight: 700; }
.note { color: #6b7280; font-size: 12px; }
.pill-blue  { background: #dbeafe; color: #1d4ed8; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.pill-green { background: #dcfce7; color: #15803d; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.warn-box { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 6px;
            padding: 12px 16px; margin-top: 16px; }
.warn-box ul { padding-left: 18px; }
.warn-box li { color: #78350f; font-size: 12px; }
footer { text-align: center; color: #9ca3af; font-size: 11px; padding: 24px; }
"""

def _esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def _th(*c): return "<tr>"+"".join(f"<th>{x}</th>" for x in c)+"</tr>"
def _tr(*c): return "<tr>"+"".join(f"<td>{x}</td>" for x in c)+"</tr>"
def _tbl(header, rows): return f"<table>{header}{''.join(rows)}</table>"
def _sec(id_, title, body): return f'<section id="{id_}"><h2>{title}</h2>{body}</section>'
def _warn(ws):
    if not ws: return ""
    items = "".join(f"<li>{_esc(w)}</li>" for w in (ws if isinstance(ws,list) else [ws]) if w)
    return f'<div class="warn-box"><strong>⚠ Notes</strong><ul>{items}</ul></div>' if items else ""
def _bi(v): return ('<span class="yes">✓</span>' if v is True else '<span class="no">✗</span>' if v is False else "—")

def _html_taxonomy(d):
    mappings = d.get("mappings", [])
    nc = sum(1 for m in mappings if m.get("classify_features_emits"))
    np = sum(1 for m in mappings if m.get("process_selection_ready"))
    badges = f'<span class="pill-blue">{nc}/25 classifier</span> &nbsp; <span class="pill-green">{np}/25 process rules</span>'
    header = _th("ID","MFCAD++ Name","Internal Type","Classifier","Process rule","Notes")
    rows = [_tr(m["mfcad_id"], _esc(m["mfcad_name"]), f'<code>{_esc(m["internal_feature_type"])}</code>',
                _bi(m.get("classify_features_emits")), _bi(m.get("process_selection_ready")),
                f'<span class="note">{_esc(m.get("warnings") or "")}</span>') for m in mappings]
    return _sec("s07","Sheet 07 — Label / Taxonomy Map (MFCAD++ bridge)",
                f'<p class="note">{_esc(d.get("description",""))}</p><p style="margin:10px 0">{badges}</p>'
                + _tbl(header, rows) + _warn(d.get("warnings")))

def _html_classification(d):
    steps = "".join(f"<li>{_esc(s)}</li>" for s in d.get("decision_priority",[]))
    t_header = _th("Parameter","Value","Unit","Role")
    t_rows = [_tr(f'<code>{_esc(k)}</code>',f'<strong>{t["value"]}</strong>',_esc(t["unit"]),_esc(t["role"]))
              for k,t in d.get("thresholds_mm",{}).items()]
    pr = d.get("pocket_rules",{})
    pr_html = f'<p>Min perpendicular walls: <strong>{pr.get("min_perpendicular_wall_count")}</strong> &nbsp;|&nbsp; High-confidence: <strong>{pr.get("confidence_high_min_perp_walls")}</strong></p>' if pr else ""
    return _sec("s01","Sheet 01 — Feature Classification Rules",
                f'<h3>Decision Priority</h3><ol>{steps}</ol>'
                f'<h3>Thresholds</h3>' + _tbl(t_header, t_rows) + pr_html + _warn(d.get("warnings")))

def _html_process(d):
    b = d.get("drill_diameter_bands_mm",{})
    pf = b.get("pilot_diameter_fraction_of_final",0.6)
    mi,tw,co = b.get("micro_drill_max_exclusive",1), b.get("twist_drill_max_inclusive",13), b.get("core_drill_max_inclusive",32)
    band_h = _th("Band","Diameter range","Operations")
    band_r = [_tr("Micro",f"d &lt; {mi} mm","micro_drill"), _tr("Twist",f"{mi}–{tw} mm","spot_drill → twist_drill"),
              _tr("Core",f"{tw}–{co} mm",f"spot_drill → pilot_drill ({int(pf*100)}% D) → core_drill"), _tr("Large",f"&gt; {co} mm","circular_interp or boring_bar")]
    ddr = d.get("ddr_drill_cycle",{}); std=ddr.get("ddr_standard_max_inclusive",3); pk=ddr.get("ddr_peck_max_inclusive",5)
    ddr_h = _th("DDR range","Cycle")
    ddr_r = [_tr(f"≤ {std}","Standard (G81)"), _tr(f"≤ {pk}","Peck (G83)"), _tr(f"&gt; {pk}","Deep peck")]
    tap_h = _th("Thread","Tap drill (mm)")
    tap_r = [_tr(f"M{k}",v) for k,v in d.get("tap_drill_table_mm",{}).items() if k!="comment"]
    stl = d.get("material_stock_to_leave_mm",{})
    stl_h = _th("Material","XY stock (mm)","Z stock (mm)")
    stl_r = [_tr(_esc(m),v["xy"],v["z"]) for m,v in stl.get("per_material",{}).items()]
    return _sec("s02","Sheet 02 — Process Selection Rules",
                "<h3>Drill Diameter Bands</h3>" + _tbl(band_h,band_r)
                + "<h3>DDR → Drill Cycle</h3>" + _tbl(ddr_h,ddr_r)
                + "<h3>Tap Drill Table (ISO 68-1)</h3>" + _tbl(tap_h,tap_r)
                + "<h3>Material Stock-to-Leave</h3>" + _tbl(stl_h,stl_r)
                + _warn(d.get("warnings")))

def _html_tool_policy(d):
    tol = d.get("tolerances_mm",{})
    tol_h = _th("Setting","Value (mm)")
    tol_r = [_tr(f'<code>{_esc(k)}</code>',v) for k,v in tol.items()]
    op_h = _th("Operation","Rule","Fraction / default")
    op_r = []
    for op, pol in d.get("diameter_resolution",{}).items():
        if isinstance(pol,dict):
            frac = pol.get("target_fraction_of_bore_diameter") or pol.get("target_fraction_of_boss_diameter") or pol.get("default_feature_diameter_mm_if_missing") or "—"
            op_r.append(_tr(f'<code>{_esc(op)}</code>',_esc(pol.get("rule","")),frac))
        else:
            op_r.append(_tr(f'<code>{_esc(op)}</code>',_esc(str(pol)),"—"))
    return _sec("s03","Sheet 03 — Tool Matching Policy",
                "<h3>Tolerances</h3>" + _tbl(tol_h,tol_r)
                + "<h3>Selection Rules by Operation</h3>" + _tbl(op_h,op_r)
                + _warn(d.get("warnings")))

def _html_cutting(d):
    md = d.get("machine_defaults",{})
    peck = d.get("peck_fractions_of_tool_diameter",{})
    p_h = _th("Coolant","Peck (G83)","Deep peck")
    p_r = [_tr(f'<code>{_esc(c)}</code>',f'{f["peck"]} × D',f'{f["deep_peck"]} × D') for c,f in peck.items() if c!="comment"]
    tsc = d.get("tsc_small_drill_boost",{})
    form_rows = "".join(f'<li><strong>{_esc(n)}:</strong> <code>{_esc(e)}</code></li>' for n,e in d.get("formulas",{}).items())
    return _sec("s04","Sheet 04 — Cutting Parameter Rules",
                f'<p>Max RPM: <strong>{md.get("max_spindle_rpm")}</strong> &nbsp;|&nbsp; Coolant: <code>{_esc(md.get("coolant_default",""))}</code></p>'
                + "<h3>Peck Fractions</h3>" + _tbl(p_h,p_r)
                + f'<h3>TSC Boost</h3><p>Drills &lt; {tsc.get("max_tool_diameter_mm_exclusive")} mm → Vc × <strong>{tsc.get("vc_multiplier")}</strong></p>'
                + f"<h3>Formulas</h3><ul>{form_rows}</ul>"
                + _warn(d.get("warnings")))

def _html_setup(d):
    wcs = d.get("wcs",{})
    czh = d.get("wcs_origin_corner_heuristic",{})
    ss = d.get("stock_state",{})
    grp = "".join(f"<li>{_esc(s)}</li>" for s in d.get("grouping_algorithm",[]))
    return _sec("s05","Sheet 05 — Setup Planning Rules",
                f'<p>WCS: {" → ".join(f"<code>{g}</code>" for g in wcs.get("sequence",[]))}</p>'
                f'<p>Corner-zero tolerance: <strong>{czh.get("fraction_of_dimension",0)*100:.0f}%</strong> — {_esc(czh.get("rule",""))}</p>'
                f"<h3>Grouping Algorithm</h3><ol>{grp}</ol>"
                f'<p>Stock: Setup 1 = <code>{_esc(ss.get("setup_1_type",""))}</code>, later = <code>{_esc(ss.get("later_setups",""))}</code></p>'
                + _warn(d.get("warnings")))

def _html_workholding(d):
    h = _th("Approach","Fixture","Clamp faces","Rest face","Jaw opening")
    rows = []
    for t in d.get("principal_spindle_templates",[]):
        ftype = t.get("type") or f'{t.get("type_setup_1")}/{t.get("type_later")}'
        rows.append(_tr(_esc(t.get("label_hint","")), f'<code>{_esc(ftype)}</code>',
                        _esc(str(t.get("clamp_faces",""))), _esc(str(t.get("rest_face",""))),
                        _esc(t.get("jaw_opening_mm_from_bbox",""))))
    fb = d.get("fallback",{})
    return _sec("s06","Sheet 06 — Workholding / Fixture Rules",
                "<h3>Principal Spindle Templates</h3>" + _tbl(h,rows)
                + f'<p><strong>Fallback:</strong> <code>{_esc(fb.get("type",""))}</code> — {_esc(fb.get("notes",""))}</p>'
                + _warn(d.get("warnings")))

def generate_html():
    all_data = load_all()
    renderers = [
        ("01_feature_classification.json", _html_classification),
        ("02_process_selection.json",      _html_process),
        ("03_tool_matching_policy.json",   _html_tool_policy),
        ("04_cutting_parameters.json",     _html_cutting),
        ("05_setup_planning.json",         _html_setup),
        ("06_workholding.json",            _html_workholding),
        ("07_label_taxonomy.json",         _html_taxonomy),
    ]
    nav = "".join(f'<a href="#s{num}">{num} · {name}</a>' for _, num, name in SHEETS)
    today = date.today().isoformat()
    body  = "\n".join(renderer(all_data[fname]) for fname, renderer in renderers)
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShiaanX CAPP — Rule Sheets</title>
<style>{CSS}</style></head><body>
<header><h1>⚙️ ShiaanX CAPP — Rule Sheets Reference</h1>
<p>Auto-generated {today} · Edit JSON rule sheets, re-run generate_rule_docs.py to update</p></header>
<nav>{nav}</nav>
<main>{body}</main>
<footer>ShiaanX · {today}</footer>
</body></html>"""
    out = SHEET_DIR / "RULES.html"
    out.write_text(html, encoding="utf-8")
    print(f"Written: {out}  ({len(html):,} bytes)")
    print(f"Open:    file:///{out.as_posix()}")

    docs_out = SHEET_DIR.parent.parent / "docs" / "RULES.html"
    docs_out.parent.mkdir(exist_ok=True)
    docs_out.write_text(html, encoding="utf-8")
    print(f"Written: {docs_out}  (GitHub Pages copy)")

    import subprocess, os
    repo_root = SHEET_DIR.parent.parent
    files = [
        str(out.relative_to(repo_root)),
        str(docs_out.relative_to(repo_root)),
    ]
    subprocess.run(["git", "add"] + files, cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", f"Update RULES.html [{date.today().isoformat()}]"], cwd=repo_root, check=True)
    subprocess.run(["git", "push"], cwd=repo_root, check=True)
    print("Pushed to GitHub Pages.")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if is_streamlit():
        run_dashboard()
    else:
        generate_html()
