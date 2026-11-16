"""
Tool 14: Foundation, Plinth & Industrial Floor Slab QTO Calculator (calculate_foundation_and_slab_qto).

Computes the full structural and earthwork quantity chain for pad footings, cup footings (kehelyalap),
strip foundations, plinth walls, raft slabs, and industrial concrete floors:
1. Structural concrete volume (m3) with cup/socket void deduction
2. Blinding/lean concrete area (m2) and volume (m3) with lateral overhang (+10 cm per side)
3. Exact lateral formwork area (m2)
4. DIN 4124 prismoidal pit excavation (m3) with working space and batter slope, backfill, and displaced spoil
5. Reinforcement steel / steel fiber tonnage (t)
6. Plinth XPS perimeter thermal insulation area (m2)
7. Industrial floor saw-cut contraction joints (m) and perimeter isolation strip (m)
"""

from __future__ import annotations

import math
from pathlib import Path

from civil_qto_mcp.cad.dwg_loader import load_cad_document
from civil_qto_mcp.cad.geometry_engine import extract_polygons_from_entities, iter_target_entities
from civil_qto_mcp.models.dxf_models import (
    FoundationAndSlabQtoReport,
    FoundationElementBreakdown,
    FoundationElementInput,
)

DEFAULT_REBAR_KG_M3: dict[str, float] = {
    "PAD_FOOTING": 95.0,
    "CUP_FOOTING": 115.0,
    "STRIP_FOUNDATION": 85.0,
    "PLINTH_WALL": 100.0,
    "RAFT_SLAB": 110.0,
    "INDUSTRIAL_FLOOR": 30.0,  # Steel fiber / mesh equivalent
}


def calculate_foundation_and_slab_qto(
    elements: list[FoundationElementInput] | list[dict] | None = None,
    file_path_cad: str | Path | None = None,
    foundation_layers: list[str] | None = None,
    default_cad_element_type: str = "PAD_FOOTING",
    default_cad_height_m: float = 0.80,
    unit_scale_to_meter: float = 1.0,
    blinding_thickness_m: float = 0.10,
    blinding_overhang_m: float = 0.10,
    working_space_m: float = 0.50,
    batter_angle_deg: float = 60.0,
) -> FoundationAndSlabQtoReport:
    """
    Calculate structural concrete, blinding concrete (+10cm overhang), formwork, rebar,
    DIN 4124 prismoidal excavation, XPS insulation, and floor joints for foundations and slabs.
    """
    parsed_elements: list[FoundationElementInput] = []
    warnings_list: list[str] = []

    if elements:
        for el in elements:
            parsed_elements.append(
                el if isinstance(el, FoundationElementInput) else FoundationElementInput.model_validate(el)
            )

    if file_path_cad is not None and foundation_layers:
        doc, _ = load_cad_document(file_path_cad)
        msp = doc.modelspace()
        ents = list(iter_target_entities(msp, target_layers=set(foundation_layers), explode_inserts=True))
        polys, _, p_warns = extract_polygons_from_entities(
            ents,
            unit_scale_to_meter=unit_scale_to_meter,
            auto_close_gap_tolerance_m=0.05,
            include_hatches=True,
        )
        warnings_list.extend(p_warns)
        for idx, poly in enumerate(polys, start=1):
            minx, miny, maxx, maxy = poly.bounds
            parsed_elements.append(
                FoundationElementInput(
                    element_id=f"CAD-FND-{idx:02d}",
                    element_type=default_cad_element_type,  # type: ignore[arg-type]
                    count=1,
                    length_m=round(maxx - minx, 3),
                    width_m=round(maxy - miny, 3),
                    height_m=default_cad_height_m,
                    plan_area_m2=round(poly.area, 3),
                    plan_perimeter_m=round(poly.length, 3),
                )
            )

    breakdowns: list[FoundationElementBreakdown] = []
    tot_struct_m3 = 0.0
    tot_blind_m3 = 0.0
    tot_form_m2 = 0.0
    tot_rebar_t = 0.0
    tot_exc_m3 = 0.0
    tot_backfill_m3 = 0.0
    tot_spoil_m3 = 0.0
    tot_xps_m2 = 0.0
    tot_joints_m = 0.0
    tot_strip_m = 0.0

    # Batter horizontal run per meter of depth
    if batter_angle_deg >= 89.5:
        cot_beta = 0.0
    else:
        cot_beta = 1.0 / math.tan(math.radians(max(batter_angle_deg, 20.0)))

    for el in parsed_elements:
        n = el.count
        h = el.height_m
        l_m = el.length_m
        w_m = el.width_m

        if el.plan_area_m2 is not None and el.plan_area_m2 > 0:
            one_area = el.plan_area_m2
            one_perim = el.plan_perimeter_m or (2.0 * (l_m + w_m) if (l_m > 0 and w_m > 0) else 4.0 * math.sqrt(one_area))
        else:
            one_area = l_m * w_m
            one_perim = 2.0 * (l_m + w_m)

        footprint_tot = round(n * one_area, 3)
        struct_vol = round(max(n * (one_area * h - el.cup_void_volume_m3), 0.0), 3)

        # Blinding concrete with overhang on all sides
        if el.include_blinding and blinding_thickness_m > 0:
            if el.plan_area_m2 is None and l_m > 0 and w_m > 0:
                one_blind_area = (l_m + 2.0 * blinding_overhang_m) * (w_m + 2.0 * blinding_overhang_m)
            else:
                one_blind_area = one_area + one_perim * blinding_overhang_m + 4.0 * (blinding_overhang_m**2)
            blind_area_tot = round(n * one_blind_area, 3)
            blind_vol_tot = round(blind_area_tot * blinding_thickness_m, 3)
        else:
            blind_area_tot = 0.0
            blind_vol_tot = 0.0

        # Lateral Formwork Area
        form_area = round(n * one_perim * h, 3)

        # Reinforcement steel / fiber tonnage
        ratio_kg = el.rebar_kg_per_m3 if el.rebar_kg_per_m3 is not None else DEFAULT_REBAR_KG_M3.get(el.element_type, 95.0)
        rebar_t = round((struct_vol * ratio_kg) / 1000.0, 4)

        # DIN 4124 Prismoidal Pit Excavation
        if el.include_excavation and el.element_type != "INDUSTRIAL_FLOOR":
            h_exc = h + (blinding_thickness_m if el.include_blinding else 0.0)
            eff_l = l_m if l_m > 0 else math.sqrt(one_area)
            eff_w = w_m if w_m > 0 else math.sqrt(one_area)
            l_bot = eff_l + 2.0 * working_space_m
            w_bot = eff_w + 2.0 * working_space_m
            b_run = h_exc * cot_beta
            l_top = l_bot + 2.0 * b_run
            w_top = w_bot + 2.0 * b_run
            l_mid = 0.5 * (l_bot + l_top)
            w_mid = 0.5 * (w_bot + w_top)

            a_bot = l_bot * w_bot
            a_top = l_top * w_top
            a_mid = l_mid * w_mid
            one_exc_vol = (h_exc / 6.0) * (a_bot + a_top + 4.0 * a_mid)
            exc_vol_tot = round(n * one_exc_vol, 3)
            displaced_tot = round(struct_vol + blind_vol_tot, 3)
            backfill_tot = round(max(exc_vol_tot - displaced_tot, 0.0), 3)
        else:
            exc_vol_tot = 0.0
            displaced_tot = round(struct_vol + blind_vol_tot, 3)
            backfill_tot = 0.0

        # Plinth XPS insulation
        if el.xps_insulation_sides > 0:
            side_len = l_m if el.element_type == "PLINTH_WALL" and l_m > 0 else one_perim
            xps_m2 = round(n * el.xps_insulation_sides * side_len * h, 3)
        else:
            xps_m2 = 0.0

        # Industrial floor contraction joints & perimeter isolation strip
        if el.element_type == "INDUSTRIAL_FLOOR":
            joints_m = round(footprint_tot * (2.0 / el.joint_grid_spacing_m), 2)
            strip_m = round(n * one_perim, 2)
        else:
            joints_m = 0.0
            strip_m = 0.0

        breakdowns.append(
            FoundationElementBreakdown(
                element_id=el.element_id,
                element_type=el.element_type,
                count=n,
                footprint_area_m2=footprint_tot,
                structural_concrete_m3=struct_vol,
                blinding_area_m2=blind_area_tot,
                blinding_concrete_m3=blind_vol_tot,
                formwork_area_m2=form_area,
                rebar_or_fiber_tonnes=rebar_t,
                excavation_volume_m3=exc_vol_tot,
                backfill_volume_m3=backfill_tot,
                displaced_spoil_m3=displaced_tot,
                xps_insulation_area_m2=xps_m2,
                saw_cut_joints_m=joints_m,
                perimeter_isolation_strip_m=strip_m,
            )
        )

        tot_struct_m3 += struct_vol
        tot_blind_m3 += blind_vol_tot
        tot_form_m2 += form_area
        tot_rebar_t += rebar_t
        tot_exc_m3 += exc_vol_tot
        tot_backfill_m3 += backfill_tot
        tot_spoil_m3 += displaced_tot
        tot_xps_m2 += xps_m2
        tot_joints_m += joints_m
        tot_strip_m += strip_m

    return FoundationAndSlabQtoReport(
        elements_processed_count=len(breakdowns),
        blinding_thickness_m=blinding_thickness_m,
        blinding_overhang_m=blinding_overhang_m,
        working_space_m=working_space_m,
        batter_angle_deg=batter_angle_deg,
        total_structural_concrete_m3=round(tot_struct_m3, 2),
        total_blinding_concrete_m3=round(tot_blind_m3, 2),
        total_formwork_area_m2=round(tot_form_m2, 2),
        total_rebar_and_fiber_tonnes=round(tot_rebar_t, 3),
        total_excavation_m3=round(tot_exc_m3, 2),
        total_backfill_m3=round(tot_backfill_m3, 2),
        total_displaced_spoil_m3=round(tot_spoil_m3, 2),
        total_xps_insulation_m2=round(tot_xps_m2, 2),
        total_saw_cut_joints_m=round(tot_joints_m, 2),
        total_perimeter_strip_m=round(tot_strip_m, 2),
        breakdowns=breakdowns,
        warnings=warnings_list,
    )
