"""
quote_estimation.py
-------------------
Spreadsheet-style instant quote engine for the CNC pipeline.

This sits on top of the existing feature/process/setup/tool/parameter flow and
turns the engineering outputs into commercial line items that resemble the
manual quote sheets used by the manufacturing team.

Inputs
------
1) params JSON from parameter_calculation.py (required)
2) features JSON from extract_features.py (recommended)
3) RFQ JSON with customer/commercial inputs (optional)
4) quote_rules.json (heuristics, mappings, defaults)
5) quote_price_book.json (volatile prices and rate cards)

Output
------
JSON quote with:
  - derived part / stock context
  - line items by cost bucket
  - totals by category
  - batch and unit pricing
  - confidence and assumptions

Usage
-----
python quote_estimation.py <params_json> [output_json]
python quote_estimation.py Hub_params.json Hub_quote.json --features Hub_features_output.json --qty 10
python quote_estimation.py Hub_params.json Hub_quote.json --features Hub_features_output.json --rfq example_rfq.json
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES_PATH = os.path.join(SCRIPT_DIR, "quote_rules.json")
DEFAULT_PRICE_BOOK_PATH = os.path.join(SCRIPT_DIR, "quote_price_book.json")


DEFAULT_RULES = {
    "currency": "INR",
    "defaults": {
        "material": "mild_steel",
        "inspection_level": "manual",
        "packaging": "standard_box",
        "shipping": "standard_ground",
        "surface_finishes": [],
        "secondary_operations": [],
        "minimum_charge": 0.0,
        "minimum_unit_price": 0.0,
        "include_overhead_percent": 0.0,
        "include_profit_percent": 0.0,
        "include_contingency_percent": 0.0,
    },
    "feature_type_complexity": {
        "through_hole": 1.0,
        "blind_hole": 1.2,
        "counterbore": 1.4,
        "large_bore": 1.3,
        "boss": 1.1,
        "planar_face": 0.8,
        "default": 1.0,
    },
    "stock_rules": {
        "size_classes": [
            {"name": "small", "max_stock_mass_kg": 5.0},
            {"name": "medium", "max_stock_mass_kg": 15.0},
            {"name": "large", "max_stock_mass_kg": 75.0},
            {"name": "heavy", "max_stock_mass_kg": None},
        ],
        "block": {
            "pad_mm": {"x": 6.0, "y": 6.0, "z": 4.0}
        },
        "round": {
            "diameter_pad_mm": 6.0,
            "length_pad_mm": 6.0
        },
    },
    "process_mapping": {
        "machine_to_family": {
            "milling": "milling_3axis",
            "turning": "turning_cnc",
            "both": "milling_3axis",
        },
        "operation_to_family": {
            "spot_drill": "milling_3axis",
            "twist_drill": "milling_3axis",
            "pilot_drill": "milling_3axis",
            "micro_drill": "milling_3axis",
            "core_drill": "milling_3axis",
            "face_mill": "milling_3axis",
            "contour_mill": "milling_3axis",
            "pocket_mill": "milling_3axis",
            "counterbore_mill": "milling_3axis",
            "circular_interp": "milling_3axis",
            "boring_bar": "turning_cnc",
            "turning_rough": "turning_cnc",
            "turning_finish": "turning_cnc",
            "od_turn": "turning_cnc",
            "id_turn": "turning_cnc",
            "thread_turn": "turning_cnc",
            "grind": "grinding",
        },
    },
    "auto_rules": {
        "choose_round_stock_if_turning_share_ge": 0.35,
        "cmm_if_setups_ge": 4,
        "cmm_if_feature_count_ge": 18,
    },
}


def _load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _save_json(path: str, data: Dict) -> None:
    with open(path, "w", encoding="utf-8-sig") as f:
        json.dump(data, f, indent=2)


def _deep_update(base: Dict, override: Dict) -> Dict:
    out = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_update(out[k], v)
        else:
            out[k] = v
    return out


def _round_money(val: float) -> float:
    return round(float(val), 2)


def _to_hours(minutes: float) -> float:
    return max(minutes, 0.0) / 60.0


def _slugify(text: str) -> str:
    keep = []
    for ch in (text or "").strip().lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in (" ", "-", "/", "*", ".", "_"):
            keep.append("_")
    slug = "".join(keep)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _derive_features_path(params_path: str) -> str:
    base = os.path.basename(params_path)
    if not base.endswith("_params.json"):
        return ""
    cand = os.path.join(os.path.dirname(params_path), base.replace("_params.json", "_features.json"))
    return cand if os.path.exists(cand) else ""


def _fallback_step_time_s(step: Dict) -> float:
    op = (step.get("operation") or "").lower()
    vf = float(step.get("vf_mmpm") or 0.0)
    depth = abs(float(step.get("depth_mm") or step.get("ap_mm") or 0.0))
    dia = float(step.get("tool_diameter_mm") or step.get("diameter_mm") or 0.0)

    if vf <= 0.0:
        return 0.0

    drill_ops = {"spot_drill", "twist_drill", "micro_drill", "pilot_drill", "core_drill", "boring_bar"}
    mill_ops = {"face_mill", "contour_mill", "pocket_mill", "counterbore_mill", "circular_interp"}

    if op in drill_ops:
        travel_mm = max(depth, dia * 0.5)
        peck_penalty = 1.35 if step.get("peck_mm") else 1.15
        return (travel_mm / vf) * 60.0 * peck_penalty + 1.0

    if op in mill_ops:
        ap = abs(float(step.get("ap_mm") or 1.0))
        passes = max(1.0, depth / ap) if ap > 0 else 1.0
        path_mm = max(math.pi * max(dia, 1.0) * 1.4 * passes, 8.0)
        return (path_mm / vf) * 60.0 + 2.0

    return 0.0


def _resolve_material(material: str, price_book: Dict) -> str:
    material = (material or "").strip().lower()
    mats = price_book.get("materials", {})
    if material in mats:
        return material
    for canonical, meta in mats.items():
        aliases = [a.lower() for a in meta.get("aliases", [])]
        if material in aliases:
            return canonical
    return material or "mild_steel"


def _metric_value(metric: str, ctx: Dict) -> float:
    if metric == "stock_mass_kg":
        return float(ctx["stock"]["stock_mass_kg"])
    if metric == "part_mass_kg":
        return float(ctx["stock"]["part_mass_kg"])
    if metric == "max_dimension_mm":
        return float(ctx["part"]["max_dimension_mm"])
    if metric == "surface_area_proxy_mm2":
        return float(ctx["part"]["surface_area_proxy_mm2"])
    return 0.0


def _pick_size_class(stock_mass_kg: float, rules: Dict) -> str:
    for row in rules.get("stock_rules", {}).get("size_classes", []):
        limit = row.get("max_stock_mass_kg")
        if limit is None or stock_mass_kg <= float(limit):
            return row["name"]
    return "heavy"


def _part_and_feature_stats(params_data: Dict, features_data: Dict) -> Dict:
    bbox = (features_data or {}).get("bounding_box", {})
    mp = (features_data or {}).get("mass_properties", {})
    lx = float(bbox.get("length_x") or 0.0)
    ly = float(bbox.get("length_y") or 0.0)
    lz = float(bbox.get("length_z") or 0.0)
    feature_clusters = [
        c for c in params_data.get("clusters", [])
        if c.get("feature_type") != "background"
    ]
    surface_area_proxy = 2.0 * ((lx * ly) + (lx * lz) + (ly * lz))
    return {
        "bbox_mm": {"x": lx, "y": ly, "z": lz},
        "part_volume_mm3": max(0.0, float(mp.get("volume") or 0.0)),
        "surface_area_proxy_mm2": max(0.0, surface_area_proxy),
        "feature_count": len(feature_clusters),
        "max_dimension_mm": max(lx, ly, lz),
    }


def _analyze_processes(params_data: Dict, rules: Dict) -> Dict:
    mapping = rules.get("process_mapping", {})
    op_map = mapping.get("operation_to_family", {})
    machine_map = mapping.get("machine_to_family", {})

    processes = {}
    step_signals = {
        "total_steps": 0,
        "substitutions": 0,
        "rpm_capped": 0,
        "tool_not_found": 0,
        "deep_peck_ops": 0,
        "warnings": 0,
    }

    for cluster in params_data.get("clusters", []):
        if cluster.get("feature_type") == "background":
            continue
        setup_id = cluster.get("setup_id")
        feature_type = cluster.get("feature_type") or "default"
        for step in cluster.get("process_sequence", []):
            op = (step.get("operation") or "").lower()
            if op in ("fixture_rotation", "manual_review"):
                continue

            family = (
                op_map.get(op)
                or machine_map.get((step.get("machine") or "").lower())
                or machine_map.get((cluster.get("machine_selected") or "").lower())
                or "milling_3axis"
            )
            proc = processes.setdefault(family, {
                "family": family,
                "cycle_time_min": 0.0,
                "step_count": 0,
                "setup_ids": set(),
                "operations": defaultdict(int),
                "feature_types": defaultdict(int),
            })

            t = float(step.get("estimated_time_s") or 0.0)
            if t <= 0.0:
                t = _fallback_step_time_s(step)
            proc["cycle_time_min"] += t / 60.0
            proc["step_count"] += 1
            if setup_id is not None:
                proc["setup_ids"].add(setup_id)
            proc["operations"][op] += 1
            proc["feature_types"][feature_type] += 1

            tool_notes = (step.get("tool_notes") or "").upper()
            param_notes = (step.get("param_notes") or "").upper()
            step_signals["total_steps"] += 1
            if "SUBSTITUTION" in tool_notes:
                step_signals["substitutions"] += 1
            if step.get("rpm_capped"):
                step_signals["rpm_capped"] += 1
            if step.get("tool_id") == "NOT_FOUND" or "TOOL NOT FOUND" in param_notes:
                step_signals["tool_not_found"] += 1
            if "DEEP_PECK" in param_notes:
                step_signals["deep_peck_ops"] += 1
            if "WARNING" in tool_notes or "WARNING" in param_notes:
                step_signals["warnings"] += 1

    for proc in processes.values():
        proc["setup_count"] = len(proc["setup_ids"])
        proc["setup_ids"] = sorted(proc["setup_ids"])
        proc["operations"] = dict(proc["operations"])
        proc["feature_types"] = dict(proc["feature_types"])
        proc["cycle_time_min"] = round(proc["cycle_time_min"], 3)

    return {
        "processes": processes,
        "signals": step_signals,
    }


def _infer_stock_shape(rfq: Dict, process_info: Dict, rules: Dict) -> str:
    rfq_shape = (
        rfq.get("material", {}).get("shape")
        or rfq.get("stock", {}).get("shape")
        or ""
    ).strip().lower()
    if rfq_shape in ("block", "plate", "round", "rod", "bar"):
        return "round" if rfq_shape in ("round", "rod", "bar") else "block"

    total = 0.0
    turning = 0.0
    for family, proc in process_info["processes"].items():
        total += proc["cycle_time_min"]
        if family == "turning_cnc":
            turning += proc["cycle_time_min"]
    share = (turning / total) if total > 0.0 else 0.0
    threshold = float(rules.get("auto_rules", {}).get("choose_round_stock_if_turning_share_ge", 0.35))
    return "round" if share >= threshold else "block"


def _derive_stock_context(
    params_data: Dict,
    features_data: Dict,
    rfq: Dict,
    rules: Dict,
    price_book: Dict,
    qty: int,
) -> Dict:
    part = _part_and_feature_stats(params_data, features_data)
    bbox = part["bbox_mm"]
    material_name = _resolve_material(
        rfq.get("material", {}).get("name") or params_data.get("material") or rules["defaults"]["material"],
        price_book,
    )
    material_meta = price_book.get("materials", {}).get(material_name, {})
    density = float(material_meta.get("density_g_cm3") or 7.85)
    price_per_kg = float(material_meta.get("price_per_kg") or 0.0)

    process_info = _analyze_processes(params_data, rules)
    shape = _infer_stock_shape(rfq, process_info, rules)
    raw_override = rfq.get("stock", {}).get("raw_size_mm") or {}
    stock_rules = rules.get("stock_rules", {})

    if shape == "round":
        dims = sorted([bbox["x"], bbox["y"], bbox["z"]], reverse=True)
        length_dim = dims[0]
        diameter_dim = dims[1] if len(dims) > 1 else dims[0]
        stock_diameter = float(raw_override.get("diameter") or (diameter_dim + float(stock_rules.get("round", {}).get("diameter_pad_mm", 6.0))))
        stock_length = float(raw_override.get("length") or (length_dim + float(stock_rules.get("round", {}).get("length_pad_mm", 6.0))))
        stock_volume_mm3 = math.pi * (stock_diameter ** 2) * 0.25 * stock_length
        stock_dims = {"diameter": round(stock_diameter, 3), "length": round(stock_length, 3)}
    else:
        pads = stock_rules.get("block", {}).get("pad_mm", {})
        stock_x = float(raw_override.get("x") or (bbox["x"] + float(pads.get("x", 6.0))))
        stock_y = float(raw_override.get("y") or (bbox["y"] + float(pads.get("y", 6.0))))
        stock_z = float(raw_override.get("z") or (bbox["z"] + float(pads.get("z", 4.0))))
        stock_volume_mm3 = stock_x * stock_y * stock_z
        stock_dims = {"x": round(stock_x, 3), "y": round(stock_y, 3), "z": round(stock_z, 3)}

    part_mass_kg = part["part_volume_mm3"] * density / 1_000_000.0
    stock_mass_kg = stock_volume_mm3 * density / 1_000_000.0
    stock_mass_kg = max(stock_mass_kg, part_mass_kg)
    total_stock_mass_kg = stock_mass_kg * qty
    total_material_cost = total_stock_mass_kg * price_per_kg
    size_class = _pick_size_class(stock_mass_kg, rules)

    return {
        "material": material_name,
        "material_meta": material_meta,
        "shape": shape,
        "size_class": size_class,
        "stock_dims_mm": stock_dims,
        "density_g_cm3": density,
        "price_per_kg": price_per_kg,
        "part_volume_mm3": round(part["part_volume_mm3"], 3),
        "stock_volume_mm3": round(stock_volume_mm3, 3),
        "part_mass_kg": round(part_mass_kg, 4),
        "stock_mass_kg": round(stock_mass_kg, 4),
        "removed_mass_kg": round(max(0.0, stock_mass_kg - part_mass_kg), 4),
        "total_stock_mass_kg": round(total_stock_mass_kg, 4),
        "total_material_cost": _round_money(total_material_cost),
        "bbox_mm": bbox,
        "feature_count": part["feature_count"],
        "max_dimension_mm": part["max_dimension_mm"],
        "surface_area_proxy_mm2": part["surface_area_proxy_mm2"],
        "process_info": process_info,
    }


def _pricing_lookup(rate_table: Dict, size_class: str, default_key: str, fallback: float = 0.0) -> float:
    if not isinstance(rate_table, dict):
        return fallback
    if size_class in rate_table:
        return float(rate_table[size_class])
    if default_key in rate_table:
        return float(rate_table[default_key])
    return fallback


def _metric_tier_cost(rule: Dict, ctx: Dict, qty: int) -> Tuple[float, Dict]:
    metric = rule.get("metric", "stock_mass_kg")
    metric_value = _metric_value(metric, ctx)
    tiers = rule.get("tiers", [])
    selected = None
    for tier in tiers:
        limit = tier.get("max")
        if limit is None or metric_value <= float(limit):
            selected = tier
            break
    selected = selected or {"cost": 0.0, "name": "default"}
    basis = rule.get("charge_basis", "per_part")
    cost = float(selected.get("cost") or 0.0)
    if basis == "per_part":
        total = cost * qty
    else:
        total = cost
    return total, {
        "metric": metric,
        "metric_value": round(metric_value, 3),
        "tier": selected.get("name"),
        "charge_basis": basis,
    }


def _make_line_item(
    category: str,
    code: str,
    description: str,
    cost: float,
    details: Optional[Dict] = None,
    quantity: Optional[float] = None,
    unit: str = "",
    unit_price: Optional[float] = None,
    basis: str = "",
    source: str = "",
) -> Dict:
    item = {
        "category": category,
        "code": code,
        "description": description,
        "cost": _round_money(cost),
    }
    if quantity is not None:
        item["quantity"] = quantity
    if unit:
        item["unit"] = unit
    if unit_price is not None:
        item["unit_price"] = _round_money(unit_price)
    if basis:
        item["basis"] = basis
    if source:
        item["source"] = source
    if details:
        item["details"] = details
    return item


def _material_line_item(ctx: Dict, qty: int) -> Dict:
    return _make_line_item(
        category="material",
        code="raw_material",
        description=f"Raw material ({ctx['stock']['material']}, {ctx['stock']['shape']} stock)",
        cost=ctx["stock"]["total_material_cost"],
        quantity=ctx["stock"]["total_stock_mass_kg"],
        unit="kg",
        unit_price=ctx["stock"]["price_per_kg"],
        basis="per_batch",
        source=ctx["stock"]["material_meta"].get("source", "quote_price_book"),
        details={
            "size_class": ctx["stock"]["size_class"],
            "stock_dims_mm": ctx["stock"]["stock_dims_mm"],
            "part_mass_kg": ctx["stock"]["part_mass_kg"],
            "stock_mass_kg_per_part": ctx["stock"]["stock_mass_kg"],
            "removed_mass_kg_per_part": ctx["stock"]["removed_mass_kg"],
            "quantity": qty,
        },
    )


def _machining_line_items(ctx: Dict, price_book: Dict) -> List[Dict]:
    items = []
    process_rates = price_book.get("process_rates", {})
    for family, proc in ctx["stock"]["process_info"]["processes"].items():
        price_row = process_rates.get(family)
        if not price_row:
            continue
        hourly_rate = float(price_row.get("rate_per_hour") or 0.0)
        setup_cost = _pricing_lookup(price_row.get("setup_cost_per_setup", {}), ctx["stock"]["size_class"], "default", 0.0)
        cycle_cost = _to_hours(proc["cycle_time_min"]) * hourly_rate
        total_cost = cycle_cost + (proc["setup_count"] * setup_cost)
        if total_cost <= 0.0:
            continue
        items.append(_make_line_item(
            category="machining",
            code=family,
            description=price_row.get("label", family.replace("_", " ").title()),
            cost=total_cost,
            quantity=round(proc["cycle_time_min"] / 60.0, 3),
            unit="hr",
            unit_price=hourly_rate,
            basis="cycle_time + setups",
            source=price_row.get("source", "quote_price_book"),
            details={
                "setup_count": proc["setup_count"],
                "setup_cost_per_setup": setup_cost,
                "cycle_time_min": round(proc["cycle_time_min"], 3),
                "step_count": proc["step_count"],
                "operations": proc["operations"],
                "feature_types": proc["feature_types"],
            },
        ))
    return items


def _programming_line_item(ctx: Dict, rules: Dict, price_book: Dict) -> Optional[Dict]:
    row = price_book.get("engineering_rates", {}).get("cam_programming")
    if not row:
        return None
    base_min = float(row.get("base_minutes") or 0.0)
    per_setup = float(row.get("minutes_per_setup") or 0.0)
    per_tool = float(row.get("minutes_per_unique_operation") or 0.0)
    ft_weights = rules.get("feature_type_complexity", {})

    unique_ops = set()
    weighted_features = 0.0
    for proc in ctx["stock"]["process_info"]["processes"].values():
        unique_ops.update(proc["operations"].keys())
        for ft, count in proc["feature_types"].items():
            weighted_features += float(ft_weights.get(ft, ft_weights.get("default", 1.0))) * float(count)

    total_min = base_min
    total_min += per_setup * ctx["part"]["setup_count"]
    total_min += per_tool * len(unique_ops)
    total_min += weighted_features * float(row.get("minutes_per_weighted_feature", 0.0))
    if total_min <= 0.0:
        return None

    rate = float(row.get("rate_per_hour") or 0.0)
    return _make_line_item(
        category="engineering",
        code="cam_programming",
        description=row.get("label", "CAM programming"),
        cost=_to_hours(total_min) * rate,
        quantity=round(total_min / 60.0, 3),
        unit="hr",
        unit_price=rate,
        basis="complexity weighted",
        source=row.get("source", "quote_price_book"),
        details={
            "base_minutes": base_min,
            "minutes_per_setup": per_setup,
            "minutes_per_unique_operation": per_tool,
            "weighted_features": round(weighted_features, 3),
            "unique_operation_count": len(unique_ops),
            "total_minutes": round(total_min, 3),
        },
    )


def _auto_inspection_level(ctx: Dict, rfq: Dict, rules: Dict) -> str:
    explicit = (rfq.get("inspection_level") or rfq.get("quality", {}).get("inspection_level") or "").strip().lower()
    if explicit:
        return explicit
    auto = rules.get("auto_rules", {})
    if ctx["part"]["setup_count"] >= int(auto.get("cmm_if_setups_ge", 4)):
        return "cmm"
    if ctx["stock"]["feature_count"] >= int(auto.get("cmm_if_feature_count_ge", 18)):
        return "cmm"
    return rules.get("defaults", {}).get("inspection_level", "manual")


def _inspection_line_item(ctx: Dict, rfq: Dict, rules: Dict, price_book: Dict) -> Optional[Dict]:
    level = _auto_inspection_level(ctx, rfq, rules)
    row = price_book.get("inspection", {}).get(level)
    if not row:
        return None
    setup_cost = float(row.get("setup_cost") or 0.0)
    rate = float(row.get("rate_per_hour") or 0.0)
    base_hours = float(row.get("base_hours") or 0.0)
    per_setup = float(row.get("hours_per_setup") or 0.0)
    per_feature = float(row.get("hours_per_feature") or 0.0)
    hours = base_hours + (per_setup * ctx["part"]["setup_count"]) + (per_feature * ctx["stock"]["feature_count"])
    if level == "manual":
        hours = max(hours, float(row.get("minimum_hours", 0.0)))
    cost = setup_cost + (hours * rate)
    return _make_line_item(
        category="inspection",
        code=level,
        description=row.get("label", level.upper()),
        cost=cost,
        quantity=round(hours, 3),
        unit="hr",
        unit_price=rate,
        basis="per_batch",
        source=row.get("source", "quote_price_book"),
        details={
            "setup_cost": setup_cost,
            "inspection_level": level,
            "setup_count": ctx["part"]["setup_count"],
            "feature_count": ctx["stock"]["feature_count"],
        },
    )


def _find_finish_rule(name: str, price_book: Dict) -> Optional[Tuple[str, Dict]]:
    key = _slugify(name)
    finishes = price_book.get("surface_finishes", {})
    if key in finishes:
        return key, finishes[key]
    for finish_key, row in finishes.items():
        aliases = [a.lower() for a in row.get("aliases", [])]
        if name.strip().lower() in aliases:
            return finish_key, row
    return None


def _surface_finish_line_items(ctx: Dict, rfq: Dict, price_book: Dict, qty: int) -> List[Dict]:
    items = []
    finishes = rfq.get("surface_finishes") or rfq.get("post_processing", {}).get("surface_finishes") or []
    for finish in finishes:
        found = _find_finish_rule(finish, price_book)
        if not found:
            continue
        key, row = found
        cost, details = _metric_tier_cost(row, ctx, qty)
        if cost <= 0.0:
            continue
        items.append(_make_line_item(
            category="post_processing",
            code=key,
            description=row.get("label", finish),
            cost=cost,
            basis=row.get("charge_basis", "per_part"),
            source=row.get("source", "quote_price_book"),
            details=details,
        ))
    return items


def _thread_price_lookup(row: Dict, spec: str, fallback_key: str = "default") -> float:
    prices = row.get("prices", {})
    slug = _slugify(spec).upper().replace("_", "")
    if spec in prices:
        return float(prices[spec])
    if slug in prices:
        return float(prices[slug])
    upper_spec = spec.strip().upper()
    if upper_spec in prices:
        return float(prices[upper_spec])
    if fallback_key in prices:
        return float(prices[fallback_key])
    return 0.0


def _secondary_operation_line_items(rfq: Dict, price_book: Dict, qty: int) -> List[Dict]:
    items = []
    ops = rfq.get("secondary_operations") or rfq.get("post_processing", {}).get("secondary_operations") or []
    op_book = price_book.get("secondary_operations", {})
    for op in ops:
        op_type = _slugify(op.get("type", ""))
        spec = op.get("spec") or op.get("size") or op.get("name") or ""
        count = float(op.get("count") or 1.0)
        row = op_book.get(op_type)
        if not row:
            continue
        unit_cost = _thread_price_lookup(row, str(spec))
        basis = row.get("charge_basis", "per_batch")
        total = unit_cost * count
        if basis == "per_part":
            total *= qty
        items.append(_make_line_item(
            category="post_processing",
            code=op_type,
            description=f"{row.get('label', op_type.replace('_', ' ').title())} {spec}".strip(),
            cost=total,
            quantity=count,
            unit="op",
            unit_price=unit_cost,
            basis=basis,
            source=row.get("source", "quote_price_book"),
            details={
                "spec": spec,
                "count": count,
                "quantity": qty,
            },
        ))
    return items


def _packaging_and_shipping_items(ctx: Dict, rfq: Dict, rules: Dict, price_book: Dict) -> List[Dict]:
    items = []
    packaging_key = _slugify(rfq.get("packaging") or rules.get("defaults", {}).get("packaging", "standard_box"))
    shipping_key = _slugify(rfq.get("shipping") or rules.get("defaults", {}).get("shipping", "standard_ground"))

    packaging = price_book.get("packaging", {}).get(packaging_key)
    if packaging:
        cost = _pricing_lookup(packaging.get("cost_by_size_class", {}), ctx["stock"]["size_class"], "default", 0.0)
        if cost > 0.0:
            items.append(_make_line_item(
                category="logistics",
                code=packaging_key,
                description=packaging.get("label", packaging_key.replace("_", " ").title()),
                cost=cost,
                basis="per_batch",
                source=packaging.get("source", "quote_price_book"),
                details={"size_class": ctx["stock"]["size_class"]},
            ))

    shipping = price_book.get("shipping", {}).get(shipping_key)
    if shipping:
        cost = _pricing_lookup(shipping.get("cost_by_size_class", {}), ctx["stock"]["size_class"], "default", 0.0)
        if cost > 0.0:
            items.append(_make_line_item(
                category="logistics",
                code=shipping_key,
                description=shipping.get("label", shipping_key.replace("_", " ").title()),
                cost=cost,
                basis="per_batch",
                source=shipping.get("source", "quote_price_book"),
                details={"size_class": ctx["stock"]["size_class"]},
            ))
    return items


def _manual_cost_items(rfq: Dict) -> List[Dict]:
    items = []
    for row in rfq.get("manual_costs", []):
        cost = float(row.get("cost") or 0.0)
        if cost <= 0.0:
            continue
        items.append(_make_line_item(
            category=row.get("category", "manual"),
            code=_slugify(row.get("code") or row.get("name") or "manual_cost"),
            description=row.get("name") or row.get("description") or "Manual cost",
            cost=cost,
            basis=row.get("basis", "per_batch"),
            source="rfq_override",
            details={"notes": row.get("notes", "")},
        ))
    return items


def _commercial_adjustment_items(subtotal: float, rfq: Dict, rules: Dict) -> List[Dict]:
    items = []
    commercial = rfq.get("commercial", {})
    defaults = rules.get("defaults", {})
    overhead_pct = float(commercial.get("overhead_percent", defaults.get("include_overhead_percent", 0.0)))
    contingency_pct = float(commercial.get("contingency_percent", defaults.get("include_contingency_percent", 0.0)))
    profit_pct = float(commercial.get("profit_percent", defaults.get("include_profit_percent", 0.0)))

    for code, label, pct in [
        ("overhead", "Overhead", overhead_pct),
        ("contingency", "Contingency", contingency_pct),
        ("profit", "Profit / Margin", profit_pct),
    ]:
        if pct <= 0.0:
            continue
        items.append(_make_line_item(
            category="commercial",
            code=code,
            description=label,
            cost=subtotal * pct / 100.0,
            basis="percent_of_subtotal",
            source="quote_rules",
            details={"percent": pct},
        ))
    return items


def _build_risk_notes(ctx: Dict, rfq: Dict, price_book: Dict) -> Tuple[float, List[str]]:
    confidence = 0.88
    notes: List[str] = []
    signals = ctx["stock"]["process_info"]["signals"]

    if not ctx["features_supplied"]:
        confidence -= 0.10
        notes.append("No features JSON supplied; stock sizing and part geometry are less reliable.")
    if not (rfq.get("surface_finishes") or rfq.get("post_processing", {}).get("surface_finishes")):
        confidence -= 0.05
        notes.append("No surface finish specified; quote assumes no outsourced finishing.")
    if not rfq.get("inspection_level") and not rfq.get("quality", {}).get("inspection_level"):
        confidence -= 0.03
        notes.append("Inspection level not specified; auto-selected default inspection.")
    if not rfq.get("shipping"):
        confidence -= 0.02
        notes.append("Shipping method not specified; standard shipping assumption used.")
    if signals["tool_not_found"] > 0:
        confidence -= min(0.15, signals["tool_not_found"] * 0.04)
        notes.append("Some process steps are missing tool matches in the tool database.")
    if signals["substitutions"] > 0:
        confidence -= min(0.08, signals["substitutions"] * 0.01)
        notes.append("Tool substitutions were used in process planning.")
    if signals["rpm_capped"] > 0:
        confidence -= min(0.05, signals["rpm_capped"] * 0.004)
        notes.append("Multiple operations are RPM-capped; actual cycle time may drift.")
    if ctx["part"]["setup_count"] >= 4:
        confidence -= 0.03
        notes.append("High setup count increases quoting uncertainty.")

    updated_at = price_book.get("updated_at")
    if updated_at:
        try:
            updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            price_age_days = (datetime.now(timezone.utc) - updated).days
            if price_age_days > 30:
                confidence -= 0.04
                notes.append(f"Price book is {price_age_days} days old; volatile rates may need refresh.")
        except ValueError:
            pass

    confidence = max(0.35, min(0.98, confidence))
    return confidence, notes


def _category_totals(items: List[Dict]) -> Dict:
    totals = defaultdict(float)
    for item in items:
        totals[item["category"]] += float(item["cost"])
    return {k: _round_money(v) for k, v in totals.items()}


def _quote_context(params_data: Dict, features_data: Dict, rfq: Dict, rules: Dict, price_book: Dict, qty: int) -> Dict:
    stock = _derive_stock_context(params_data, features_data, rfq, rules, price_book, qty)
    return {
        "part": {
            "setup_count": len(params_data.get("setups", [])) or 1,
            "quantity": qty,
            "bbox_mm": stock["bbox_mm"],
            "max_dimension_mm": stock["max_dimension_mm"],
            "surface_area_proxy_mm2": stock["surface_area_proxy_mm2"],
        },
        "stock": stock,
        "features_supplied": bool(features_data),
    }


def generate_quote(
    params_data: Dict,
    features_data: Dict,
    rfq_data: Dict,
    rules: Dict,
    price_book: Dict,
    qty: int,
) -> Dict:
    ctx = _quote_context(params_data, features_data, rfq_data, rules, price_book, qty)
    line_items: List[Dict] = []

    line_items.append(_material_line_item(ctx, qty))
    line_items.extend(_machining_line_items(ctx, price_book))

    programming = _programming_line_item(ctx, rules, price_book)
    if programming:
        line_items.append(programming)

    inspection = _inspection_line_item(ctx, rfq_data, rules, price_book)
    if inspection:
        line_items.append(inspection)

    line_items.extend(_surface_finish_line_items(ctx, rfq_data, price_book, qty))
    line_items.extend(_secondary_operation_line_items(rfq_data, price_book, qty))
    line_items.extend(_packaging_and_shipping_items(ctx, rfq_data, rules, price_book))
    line_items.extend(_manual_cost_items(rfq_data))

    subtotal_before_commercial = sum(float(item["cost"]) for item in line_items)
    line_items.extend(_commercial_adjustment_items(subtotal_before_commercial, rfq_data, rules))

    category_totals = _category_totals(line_items)
    subtotal = sum(float(item["cost"]) for item in line_items)
    minimum_charge = float(rules.get("defaults", {}).get("minimum_charge", 0.0))
    minimum_unit_price = float(rules.get("defaults", {}).get("minimum_unit_price", 0.0))
    minimum_total = max(minimum_charge, minimum_unit_price * qty)
    adjustments = []
    if subtotal < minimum_total:
        delta = minimum_total - subtotal
        line_items.append(_make_line_item(
            category="commercial",
            code="minimum_charge_adjustment",
            description="Minimum charge adjustment",
            cost=delta,
            basis="policy",
            source="quote_rules",
        ))
        adjustments.append("Minimum charge adjustment applied.")
        subtotal = minimum_total
        category_totals = _category_totals(line_items)

    confidence, risk_notes = _build_risk_notes(ctx, rfq_data, price_book)
    risk_notes.extend(adjustments)

    return {
        "quote_version": "2.0",
        "generated_at": _now_iso(),
        "currency": rules.get("currency", "INR"),
        "quantity": qty,
        "material": ctx["stock"]["material"],
        "quote_inputs": {
            "features_supplied": bool(features_data),
            "rfq_supplied": bool(rfq_data),
            "inspection_level": _auto_inspection_level(ctx, rfq_data, rules),
            "surface_finishes": rfq_data.get("surface_finishes") or rfq_data.get("post_processing", {}).get("surface_finishes") or [],
            "packaging": rfq_data.get("packaging") or rules.get("defaults", {}).get("packaging"),
            "shipping": rfq_data.get("shipping") or rules.get("defaults", {}).get("shipping"),
        },
        "derived_context": {
            "part": ctx["part"],
            "stock": {
                "shape": ctx["stock"]["shape"],
                "size_class": ctx["stock"]["size_class"],
                "stock_dims_mm": ctx["stock"]["stock_dims_mm"],
                "part_volume_mm3": ctx["stock"]["part_volume_mm3"],
                "stock_volume_mm3": ctx["stock"]["stock_volume_mm3"],
                "part_mass_kg": ctx["stock"]["part_mass_kg"],
                "stock_mass_kg": ctx["stock"]["stock_mass_kg"],
                "removed_mass_kg": ctx["stock"]["removed_mass_kg"],
                "price_per_kg": ctx["stock"]["price_per_kg"],
            },
            "processes": ctx["stock"]["process_info"]["processes"],
            "signals": ctx["stock"]["process_info"]["signals"],
        },
        "line_items": line_items,
        "category_totals": category_totals,
        "totals": {
            "batch_total": _round_money(subtotal),
            "unit_price": _round_money(subtotal / qty),
        },
        "confidence": round(confidence, 3),
        "risk_notes": risk_notes,
        "price_book": {
            "path": DEFAULT_PRICE_BOOK_PATH,
            "version": price_book.get("version"),
            "updated_at": price_book.get("updated_at"),
        },
    }


def _load_rules(path: Optional[str]) -> Dict:
    cfg = deepcopy(DEFAULT_RULES)
    target = path or DEFAULT_RULES_PATH
    if os.path.exists(target):
        cfg = _deep_update(cfg, _load_json(target))
    return cfg


def _load_price_book(path: Optional[str]) -> Dict:
    target = path or DEFAULT_PRICE_BOOK_PATH
    if not os.path.exists(target):
        return {"version": "missing", "updated_at": _now_iso()}
    return _load_json(target)


def _load_rfq(path: Optional[str]) -> Dict:
    if not path:
        return {}
    if not os.path.exists(path):
        print(f"ERROR: RFQ file not found: {path}")
        sys.exit(1)
    return _load_json(path)


def _print_summary(quote: Dict) -> None:
    print("Quote generated:")
    print(f"  Qty: {quote['quantity']}")
    print(f"  Material: {quote['material']}")
    print(f"  Unit price: {quote['currency']} {quote['totals']['unit_price']}")
    print(f"  Batch total: {quote['currency']} {quote['totals']['batch_total']}")
    print(f"  Confidence: {quote['confidence']}")
    print("  Cost buckets:")
    for category, total in quote.get("category_totals", {}).items():
        print(f"    - {category}: {quote['currency']} {total}")


def main() -> None:
    p = argparse.ArgumentParser(description="Generate spreadsheet-style quote from params/features JSON")
    p.add_argument("params_json", help="Path to *_params.json")
    p.add_argument("output_json", nargs="?", default=None,
                   help="Path to output quote JSON (default: *_quote.json)")
    p.add_argument("--features", default=None,
                   help="Path to *_features.json (recommended)")
    p.add_argument("--rfq", default=None,
                   help="Path to RFQ JSON containing finishes, inspection, shipping, extras")
    p.add_argument("--config", default=None,
                   help="Path to quote rules JSON")
    p.add_argument("--price-book", default=None,
                   help="Path to price book JSON")
    p.add_argument("--qty", type=int, default=1,
                   help="Quantity for batch quote (default: 1)")
    args = p.parse_args()

    if args.qty < 1:
        print("ERROR: qty must be >= 1")
        sys.exit(1)

    if not os.path.exists(args.params_json):
        print(f"ERROR: params file not found: {args.params_json}")
        sys.exit(1)

    output_path = args.output_json or args.params_json.replace("_params.json", "_quote.json")
    rules = _load_rules(args.config)
    price_book = _load_price_book(args.price_book)
    rfq_data = _load_rfq(args.rfq)
    params_data = _load_json(args.params_json)

    features_data = {}
    features_path = args.features or _derive_features_path(args.params_json)
    if features_path and os.path.exists(features_path):
        features_data = _load_json(features_path)

    quote = generate_quote(
        params_data=params_data,
        features_data=features_data,
        rfq_data=rfq_data,
        rules=rules,
        price_book=price_book,
        qty=args.qty,
    )
    _save_json(output_path, quote)
    _print_summary(quote)
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()


