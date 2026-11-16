"""
Tool 17: CAD Drawing & BoQ Revision / As-Built (Megvalósulási) Comparator (compare_drawing_and_boq_revisions).

Performs deterministic comparison between:
1. Two CAD drawings (e.g., Design R0 vs. Revised R1, or Design vs. As-Built survey DXF/DWG):
   - Polygon area layers: computes Old area, New area, net delta, AND exact spatial boolean
     `spatially_added_area_m2` (`New - Old`) and `spatially_removed_area_m2` (`Old - New`)!
   - Linear layers: compares deduplicated net lengths (m) and delta.
   - Point/node layers: compares structure/manhole/bollard counts (pcs) and delta.
2. Two BoQ Excel workbooks (Old vs. New):
   - Row-level diff (`ADDED_ITEM`, `REMOVED_ITEM`, `QUANTITY_CHANGED`, `PRICE_CHANGED`, `QUANTITY_AND_PRICE_CHANGED`, `TEXT_MODIFIED`),
   - Generates ready-to-apply `BoQSupplementalItemInput` variation order payloads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from shapely.geometry import Polygon
from shapely.ops import unary_union

from civil_qto_mcp.boq.excel_auditor import inspect_and_audit_boq_xlsx
from civil_qto_mcp.cad.dwg_loader import load_cad_document
from civil_qto_mcp.cad.geometry_engine import extract_polygons_from_entities, iter_target_entities
from civil_qto_mcp.cad.qto_measurer import measure_linear_and_area_features
from civil_qto_mcp.models.boq_models import (
    BoQItem,
    BoQRevisionDiffItem,
    BoQSupplementalItemInput,
    CadLayerDiffItem,
    RevisionComparisonReport,
)


def _extract_union_polygon(msp, layer: str, scale: float):
    ents = list(iter_target_entities(msp, target_layers={layer}, explode_inserts=True))
    polys, _, _ = extract_polygons_from_entities(
        ents, unit_scale_to_meter=scale, auto_close_gap_tolerance_m=0.10, include_hatches=True
    )
    if not polys:
        return Polygon()
    return unary_union(polys)


def _count_points_on_layer(msp, layer: str) -> int:
    cnt = 0
    for ent, _, _ in iter_target_entities(msp, target_layers={layer}, explode_inserts=False):
        if ent.dxftype() in ("POINT", "CIRCLE", "INSERT"):
            cnt += 1
    return cnt


def compare_drawing_and_boq_revisions(
    old_cad_path: str | Path | None = None,
    new_cad_path: str | Path | None = None,
    boundary_layers: list[str] | None = None,
    linear_layers: list[str] | None = None,
    point_layers: list[str] | None = None,
    unit_scale_to_meter: float = 1.0,
    old_boq_xlsx_path: str | Path | None = None,
    new_boq_xlsx_path: str | Path | None = None,
    sheet_names: list[str] | None = None,
    locale: Literal["en", "de", "hu"] = "hu",
) -> RevisionComparisonReport:
    """
    Compare Old vs. New (or Design vs. As-Built) CAD drawings and/or BoQ Excel workbooks,
    reporting spatial area additions/removals, linear/point deltas, BoQ item diffs,
    and ready-to-insert variation order (`kiv. észrevétel` / `K-tétel`) payloads.
    """
    cad_diffs: list[CadLayerDiffItem] = []
    boq_diffs: list[BoQRevisionDiffItem] = []
    ready_vars: list[BoQSupplementalItemInput] = []
    warnings_list: list[str] = []

    # -------------------------------------------------------------------------
    # 1. CAD DRAWING REVISION / AS-BUILT DIFF
    # -------------------------------------------------------------------------
    if old_cad_path is not None and new_cad_path is not None:
        doc_old, _ = load_cad_document(old_cad_path)
        doc_new, _ = load_cad_document(new_cad_path)
        msp_old = doc_old.modelspace()
        msp_new = doc_new.modelspace()

        for lyr in boundary_layers or []:
            u_old = _extract_union_polygon(msp_old, lyr, unit_scale_to_meter)
            u_new = _extract_union_polygon(msp_new, lyr, unit_scale_to_meter)
            a_old = round(float(u_old.area), 2)
            a_new = round(float(u_new.area), 2)
            added_a = round(float(u_new.difference(u_old).area), 2)
            removed_a = round(float(u_old.difference(u_new).area), 2)
            delta = round(a_new - a_old, 2)
            pct = round((delta / a_old) * 100.0, 2) if a_old > 0 else None
            cad_diffs.append(
                CadLayerDiffItem(
                    layer_name=lyr,
                    feature_kind="AREA_POLYGON",
                    unit="m2",
                    old_quantity=a_old,
                    new_quantity=a_new,
                    net_delta=delta,
                    delta_percent=pct,
                    spatially_added_area_m2=added_a,
                    spatially_removed_area_m2=removed_a,
                )
            )

        for lyr in linear_layers or []:
            m_old = measure_linear_and_area_features(
                old_cad_path, unit_scale_to_meter=unit_scale_to_meter, linear_layers=[lyr]
            )
            m_new = measure_linear_and_area_features(
                new_cad_path, unit_scale_to_meter=unit_scale_to_meter, linear_layers=[lyr]
            )
            l_old = round(m_old.linear_summary.net_length_m if m_old.linear_summary else 0.0, 2)
            l_new = round(m_new.linear_summary.net_length_m if m_new.linear_summary else 0.0, 2)
            delta = round(l_new - l_old, 2)
            pct = round((delta / l_old) * 100.0, 2) if l_old > 0 else None
            cad_diffs.append(
                CadLayerDiffItem(
                    layer_name=lyr,
                    feature_kind="LINEAR",
                    unit="m",
                    old_quantity=l_old,
                    new_quantity=l_new,
                    net_delta=delta,
                    delta_percent=pct,
                )
            )

        for lyr in point_layers or []:
            c_old = float(_count_points_on_layer(msp_old, lyr))
            c_new = float(_count_points_on_layer(msp_new, lyr))
            delta = c_new - c_old
            pct = round((delta / c_old) * 100.0, 2) if c_old > 0 else None
            cad_diffs.append(
                CadLayerDiffItem(
                    layer_name=lyr,
                    feature_kind="POINT_COUNT",
                    unit="pcs",
                    old_quantity=c_old,
                    new_quantity=c_new,
                    net_delta=delta,
                    delta_percent=pct,
                )
            )

    # -------------------------------------------------------------------------
    # 2. BOQ EXCEL REVISION DIFF
    # -------------------------------------------------------------------------
    added_cnt = 0
    removed_cnt = 0
    mod_cnt = 0
    old_grand = 0.0
    new_grand = 0.0

    if old_boq_xlsx_path is not None and new_boq_xlsx_path is not None:
        rep_old = inspect_and_audit_boq_xlsx(old_boq_xlsx_path, sheet_names=sheet_names, limit=5000)
        rep_new = inspect_and_audit_boq_xlsx(new_boq_xlsx_path, sheet_names=sheet_names, limit=5000)

        def _make_key(it: BoQItem, idx: int) -> str:
            if it.item_number:
                return f"{it.sheet_name}::ITEM::{it.item_number}"
            if it.code:
                return f"{it.sheet_name}::CODE::{it.code}::{it.description[:35].lower()}"
            return f"{it.sheet_name}::DESC::{it.description[:50].lower()}::{idx}"

        old_map: dict[str, BoQItem] = {}
        for i, it in enumerate(rep_old.items):
            old_map[_make_key(it, i)] = it
            old_grand += it.total_price or 0.0

        new_map: dict[str, BoQItem] = {}
        for i, it in enumerate(rep_new.items):
            new_map[_make_key(it, i)] = it
            new_grand += it.total_price or 0.0

        all_keys = list(dict.fromkeys(list(old_map.keys()) + list(new_map.keys())))
        for k in all_keys:
            o_it = old_map.get(k)
            n_it = new_map.get(k)

            if o_it is None and n_it is not None:
                added_cnt += 1
                n_up = n_it.combined_unit_price or 0.0
                n_tot = n_it.total_price or round(n_it.quantity * n_up, 2)
                boq_diffs.append(
                    BoQRevisionDiffItem(
                        sheet_name=n_it.sheet_name,
                        item_key=k,
                        change_type="ADDED_ITEM",
                        new_row_number=n_it.row_number,
                        description=n_it.description,
                        unit=n_it.raw_unit,
                        old_quantity=0.0,
                        new_quantity=n_it.quantity,
                        quantity_delta=n_it.quantity,
                        old_unit_price=0.0,
                        new_unit_price=n_up,
                        old_total_price=0.0,
                        new_total_price=n_tot,
                        total_price_delta=n_tot,
                    )
                )
                ready_vars.append(
                    BoQSupplementalItemInput(
                        sheet_name=n_it.sheet_name,
                        item_mode="K_TETEL" if locale == "hu" else "SUPPLEMENTAL_ITEM",
                        item_number=n_it.item_number or f"K-{added_cnt}",
                        code=n_it.code or ("K-tétel" if locale == "hu" else "SUPP"),
                        description=n_it.description,
                        quantity=n_it.quantity,
                        unit=n_it.raw_unit,
                        material_unit_price=n_it.material_unit_price or n_up,
                        labor_unit_price=n_it.labor_unit_price or 0.0,
                        justification_note="Új revíziós / pótmunka tétel" if locale == "hu" else "Added in new revision",
                    )
                )
            elif o_it is not None and n_it is None:
                removed_cnt += 1
                o_up = o_it.combined_unit_price or 0.0
                o_tot = o_it.total_price or round(o_it.quantity * o_up, 2)
                boq_diffs.append(
                    BoQRevisionDiffItem(
                        sheet_name=o_it.sheet_name,
                        item_key=k,
                        change_type="REMOVED_ITEM",
                        old_row_number=o_it.row_number,
                        description=o_it.description,
                        unit=o_it.raw_unit,
                        old_quantity=o_it.quantity,
                        new_quantity=0.0,
                        quantity_delta=-o_it.quantity,
                        old_unit_price=o_up,
                        new_unit_price=0.0,
                        old_total_price=o_tot,
                        new_total_price=0.0,
                        total_price_delta=-o_tot,
                    )
                )
            elif o_it is not None and n_it is not None:
                q_diff = round(n_it.quantity - o_it.quantity, 4)
                o_up = o_it.combined_unit_price or 0.0
                n_up = n_it.combined_unit_price or 0.0
                p_diff = round(n_up - o_up, 2)
                txt_changed = o_it.description.strip() != n_it.description.strip()

                if abs(q_diff) <= 1e-4 and abs(p_diff) <= 0.01 and not txt_changed:
                    continue

                mod_cnt += 1
                if abs(q_diff) > 1e-4 and abs(p_diff) > 0.01:
                    c_type: Literal[
                        "QUANTITY_CHANGED",
                        "PRICE_CHANGED",
                        "QUANTITY_AND_PRICE_CHANGED",
                        "TEXT_MODIFIED",
                    ] = "QUANTITY_AND_PRICE_CHANGED"
                elif abs(q_diff) > 1e-4:
                    c_type = "QUANTITY_CHANGED"
                elif abs(p_diff) > 0.01:
                    c_type = "PRICE_CHANGED"
                else:
                    c_type = "TEXT_MODIFIED"

                o_tot = o_it.total_price or round(o_it.quantity * o_up, 2)
                n_tot = n_it.total_price or round(n_it.quantity * n_up, 2)
                boq_diffs.append(
                    BoQRevisionDiffItem(
                        sheet_name=n_it.sheet_name,
                        item_key=k,
                        change_type=c_type,
                        old_row_number=o_it.row_number,
                        new_row_number=n_it.row_number,
                        description=n_it.description,
                        unit=n_it.raw_unit,
                        old_quantity=o_it.quantity,
                        new_quantity=n_it.quantity,
                        quantity_delta=q_diff,
                        old_unit_price=o_up,
                        new_unit_price=n_up,
                        old_total_price=o_tot,
                        new_total_price=n_tot,
                        total_price_delta=round(n_tot - o_tot, 2),
                    )
                )
                ready_vars.append(
                    BoQSupplementalItemInput(
                        sheet_name=o_it.sheet_name,
                        item_mode="KIV_ESZREVETEL_PAIR" if locale == "hu" else "VARIATION_PAIR",
                        original_row_number=o_it.row_number,
                        description=n_it.description,
                        quantity=n_it.quantity,
                        unit=n_it.raw_unit,
                        material_unit_price=n_it.material_unit_price or n_up,
                        labor_unit_price=n_it.labor_unit_price or 0.0,
                        justification_note=(
                            f"Revíziós eltérés: mennyiség {o_it.quantity} -> {n_it.quantity} {n_it.raw_unit}"
                            if locale == "hu"
                            else f"Revision delta: quantity {o_it.quantity} -> {n_it.quantity} {n_it.raw_unit}"
                        ),
                    )
                )

    return RevisionComparisonReport(
        old_cad_path=str(Path(old_cad_path).resolve()) if old_cad_path else None,
        new_cad_path=str(Path(new_cad_path).resolve()) if new_cad_path else None,
        old_boq_path=str(Path(old_boq_xlsx_path).resolve()) if old_boq_xlsx_path else None,
        new_boq_path=str(Path(new_boq_xlsx_path).resolve()) if new_boq_xlsx_path else None,
        cad_layer_diffs=cad_diffs,
        boq_item_diffs=boq_diffs,
        boq_added_count=added_cnt,
        boq_removed_count=removed_cnt,
        boq_modified_count=mod_cnt,
        old_boq_grand_total=round(old_grand, 2),
        new_boq_grand_total=round(new_grand, 2),
        boq_net_total_delta=round(new_grand - old_grand, 2),
        ready_variation_items=ready_vars,
        warnings=warnings_list,
    )
