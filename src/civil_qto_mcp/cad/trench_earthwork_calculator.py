"""
Tool 18 implementation: calculate_trench_earthwork_and_bedding
Comprehensive EN 1610 / DIN 4124 / MSZ EN 1610 (TERC/ÉNGY) utility trench earthwork,
depth-banded excavation (0-1.5m, 1.5-3.0m, >3.0m), machine vs. hand excavation split,
lower bedding, pipe zone protective cover, pipe & manhole volume displacement,
main backfill, excess spoil haul-off, two-sided trench shoring, warning tape, and geotextile wrap.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from civil_qto_mcp.cad.inspector import inspect_civil_dxf
from civil_qto_mcp.cad.utility_extractor import extract_utility_network_and_levels
from civil_qto_mcp.models.dxf_models import (
    TrenchEarthworkAndBeddingReport,
    TrenchManholePitBreakdown,
    TrenchManholePitInput,
    TrenchPipeSegmentInput,
    TrenchSegmentBreakdown,
)


def _estimate_pipe_outer_diameter_m(pipe_material: str, dn_mm: int, explicit_od_mm: float | None) -> float:
    """Estimate outer pipe diameter (OD) in meters based on material and nominal diameter."""
    if explicit_od_mm is not None and explicit_od_mm > 0:
        return round(explicit_od_mm / 1000.0, 4)

    mat = (pipe_material or "KG-PVC").upper()
    dn_m = dn_mm / 1000.0
    if any(k in mat for k in ("KPE", "PE", "HDPE")):
        # PE pressure pipes are rated by outer diameter De
        return round(dn_m * 1.00, 4)
    if any(k in mat for k in ("PP", "PRAGMA", "CORRUGATED", "KORRUGÁLT")):
        # Corrugated double-wall PP pipes have ~14% larger OD than ID
        return round(dn_m * 1.14, 4)
    if any(k in mat for k in ("CONCRETE", "VB", "VASBETON", "BETON")):
        # Reinforced concrete pipes have thick walls (~22-24%)
        return round(dn_m * 1.24, 4)
    if any(k in mat for k in ("DUCTILE", "GÖMBGRAFIT", "IRON", "ACÉL", "STEEL")):
        return round(dn_m * 1.08, 4)
    # Default KG-PVC / PVC-U (including socket/collar allowance)
    return round(dn_m * 1.04, 4)


def _en1610_trench_bottom_width_m(dn_mm: int, od_m: float, total_depth_m: float) -> float:
    """
    Compute minimum trench bottom width (m) per MSZ EN 1610 / DIN 4124:
    Max of (OD + 2 * working space) and minimum width required by trench depth.
    """
    if dn_mm <= 225:
        width_by_dn = od_m + 0.40
    elif dn_mm <= 350:
        width_by_dn = od_m + 0.50
    elif dn_mm <= 700:
        width_by_dn = od_m + 0.70
    elif dn_mm <= 1200:
        width_by_dn = od_m + 0.85
    else:
        width_by_dn = od_m + 1.00

    if total_depth_m < 1.00:
        width_by_depth = 0.60
    elif total_depth_m <= 1.75:
        width_by_depth = 0.80
    elif total_depth_m <= 4.00:
        width_by_depth = 0.90
    else:
        width_by_depth = 1.00

    return round(max(width_by_dn, width_by_depth), 3)


def _cross_section_area_in_band(
    bottom_width_m: float,
    total_depth_m: float,
    slope_h_to_v: float,
    z_top_band: float,
    z_bot_band: float,
) -> float:
    """
    Calculate cross-sectional trench area (m2) within depth interval [z_top_band, z_bot_band]
    measured downward from the top of the excavated trench (0 to total_depth_m).
    For a sloped trench of total depth H and bottom width b at y=H,
    width at depth y (measured from top 0 down to H) is:
      w(y) = b + 2 * slope_h_to_v * (H - y)
    """
    y1 = max(0.0, min(total_depth_m, z_top_band))
    y2 = max(0.0, min(total_depth_m, z_bot_band))
    if y2 <= y1:
        return 0.0

    h_band = y2 - y1
    w1 = bottom_width_m + 2.0 * slope_h_to_v * (total_depth_m - y1)
    w2 = bottom_width_m + 2.0 * slope_h_to_v * (total_depth_m - y2)
    return 0.5 * (w1 + w2) * h_band


def calculate_trench_earthwork_and_bedding(
    pipe_segments: list[TrenchPipeSegmentInput | dict] | None = None,
    manhole_pits: list[TrenchManholePitInput | dict] | None = None,
    dxf_path: str | Path | None = None,
    pipe_layers: list[str] | None = None,
    node_layers: list[str] | None = None,
    unit_scale_to_meter: float = 1.0,
    standard_profile: Literal["EN_1610_DIN_4124", "HU_MSZ_EN_1610_TERC"] = "HU_MSZ_EN_1610_TERC",
    shoring_depth_threshold_m: float = 1.25,
    hand_excavation_ratio: float = 0.15,
    crossing_hand_zone_length_m: float = 2.0,
    bedding_compaction_factor: float = 1.15,
    bedding_bulk_density_t_per_m3: float = 1.75,
    default_pavement_box_deduction_m: float = 0.0,
) -> TrenchEarthworkAndBeddingReport:
    """
    Calculate detailed utility trench excavation (split by depth bands 0-1.5m, 1.5-3.0m, >3.0m
    and machine vs. hand excavation), lower bedding, pipe-zone protective cover, pipe & manhole
    volume displacement, native soil backfill, excess spoil haul-off, trench shoring (m2),
    warning tape (m), and geotextile wrapping (m2).
    """
    warnings: list[str] = []

    parsed_pipes: list[TrenchPipeSegmentInput] = [
        p if isinstance(p, TrenchPipeSegmentInput) else TrenchPipeSegmentInput.model_validate(p)
        for p in (pipe_segments or [])
    ]
    parsed_pits: list[TrenchManholePitInput] = [
        m if isinstance(m, TrenchManholePitInput) else TrenchManholePitInput.model_validate(m)
        for m in (manhole_pits or [])
    ]

    # Optional automatic extraction from DXF if no explicit pipe segments were supplied
    if dxf_path is not None and not parsed_pipes:
        eff_pipe_layers = pipe_layers
        if not eff_pipe_layers:
            insp = inspect_civil_dxf(dxf_path)
            pipe_kw = ("CSATORNA", "GERINC", "PIPE", "VEZETEK", "SEWER", "DRAIN", "VIZ", "KANAL", "KPE", "PVC")
            eff_pipe_layers = [
                lyr.layer_name
                for lyr in insp.layers
                if any(k in lyr.layer_name.upper() for k in pipe_kw)
            ]
            if not eff_pipe_layers:
                eff_pipe_layers = [
                    lyr.layer_name
                    for lyr in insp.layers
                    if lyr.entity_type_counts.get("LINE", 0) + lyr.entity_type_counts.get("LWPOLYLINE", 0) > 0
                ]

        net_report = extract_utility_network_and_levels(
            file_path=dxf_path,
            unit_scale_to_meter=unit_scale_to_meter,
            node_layers=node_layers,
            pipe_layers=eff_pipe_layers,
            fallback_trench_depth_m=1.60,
        )
        node_depth_map: dict[str, float] = {}
        for node in net_report.nodes:
            depth_m = node.depth_h1_m if node.depth_h1_m and node.depth_h1_m > 0 else 1.60
            node_depth_map[node.node_id] = depth_m
            parsed_pits.append(
                TrenchManholePitInput(
                    node_id=node.node_id,
                    nominal_diameter_mm=node.nominal_diameter_mm or 1000,
                    depth_to_invert_m=depth_m,
                    pavement_box_deduction_m=default_pavement_box_deduction_m,
                )
            )
        for idx, seg in enumerate(net_report.pipe_segments, start=1):
            d_start = node_depth_map.get(seg.start_node_id or "", 1.60)
            d_end = node_depth_map.get(seg.end_node_id or "", d_start)
            parsed_pipes.append(
                TrenchPipeSegmentInput(
                    segment_id=f"PIPE-{idx:02d}",
                    pipe_material=seg.material or "KG-PVC",
                    nominal_diameter_dn_mm=seg.nominal_diameter_dn_mm or 200,
                    length_m=max(0.1, seg.length_2d_m),
                    start_depth_to_invert_m=d_start,
                    end_depth_to_invert_m=d_end,
                    pavement_box_deduction_m=default_pavement_box_deduction_m,
                )
            )

    if not parsed_pipes and not parsed_pits:
        raise ValueError("At least one pipe segment, manhole pit, or valid utility dxf_path must be provided.")

    pipe_breakdowns: list[TrenchSegmentBreakdown] = []
    pit_breakdowns: list[TrenchManholePitBreakdown] = []

    total_pipe_length_m = 0.0
    total_exc_m3 = 0.0
    exc_0_15_m3 = 0.0
    exc_15_30_m3 = 0.0
    exc_over_30_m3 = 0.0
    total_machine_m3 = 0.0
    total_hand_m3 = 0.0
    total_lower_bed_m3 = 0.0
    total_zone_cov_m3 = 0.0
    total_mh_bed_m3 = 0.0
    total_displacement_m3 = 0.0
    total_backfill_m3 = 0.0
    total_spoil_m3 = 0.0
    total_shoring_m2 = 0.0
    total_tape_m = 0.0
    total_geotextile_m2 = 0.0

    num_slices = 20

    for seg in parsed_pipes:
        od_m = _estimate_pipe_outer_diameter_m(
            seg.pipe_material, seg.nominal_diameter_dn_mm, seg.outer_diameter_mm
        )
        d_start_inv = seg.start_depth_to_invert_m
        d_end_inv = seg.end_depth_to_invert_m if seg.end_depth_to_invert_m is not None else d_start_inv

        # Total depth from original surface to trench bottom (invert + lower bedding)
        raw_h_start = d_start_inv + seg.lower_bedding_thickness_m
        raw_h_end = d_end_inv + seg.lower_bedding_thickness_m
        avg_raw_h = 0.5 * (raw_h_start + raw_h_end)

        bottom_w = (
            seg.custom_bottom_width_m
            if seg.custom_bottom_width_m is not None
            else _en1610_trench_bottom_width_m(seg.nominal_diameter_dn_mm, od_m, avg_raw_h)
        )

        slope_hv = seg.slope_h_to_v if seg.trench_wall_mode == "SLOPED_UNSHORED" else 0.0

        # Effective trench depth below pavement box deduction
        eff_h_start = max(0.0, raw_h_start - seg.pavement_box_deduction_m)
        eff_h_end = max(0.0, raw_h_end - seg.pavement_box_deduction_m)
        avg_eff_h = 0.5 * (eff_h_start + eff_h_end)

        # Check shallow cover warning
        min_crown_cover = min(d_start_inv, d_end_inv) - od_m
        if min_crown_cover < 0.80:
            warnings.append(
                f"Segment '{seg.segment_id}' (DN{seg.nominal_diameter_dn_mm}): minimum crown cover is "
                f"{min_crown_cover:.2f}m (< 0.80m frost/traffic threshold); concrete protection slab or C20/25 encasement recommended."
            )

        # Slice integration along span length L for exact depth-band volumes & shoring
        dl = seg.length_m / num_slices
        seg_exc_m3 = 0.0
        seg_0_15 = 0.0
        seg_15_30 = 0.0
        seg_over_30 = 0.0
        seg_shoring_m2 = 0.0

        for i in range(num_slices):
            t_mid = (i + 0.5) / num_slices
            h_slice = eff_h_start + t_mid * (eff_h_end - eff_h_start)
            if h_slice <= 0.0:
                continue

            a_0_15 = _cross_section_area_in_band(bottom_w, h_slice, slope_hv, 0.0, 1.50)
            a_15_30 = _cross_section_area_in_band(bottom_w, h_slice, slope_hv, 1.50, 3.00)
            a_over_30 = _cross_section_area_in_band(bottom_w, h_slice, slope_hv, 3.00, 100.0)

            seg_0_15 += a_0_15 * dl
            seg_15_30 += a_15_30 * dl
            seg_over_30 += a_over_30 * dl
            seg_exc_m3 += (a_0_15 + a_15_30 + a_over_30) * dl

            if seg.trench_wall_mode == "VERTICAL_SHORED" and h_slice > shoring_depth_threshold_m:
                # Two-sided vertical shoring area over full trench wall height where depth exceeds threshold
                seg_shoring_m2 += 2.0 * h_slice * dl

        # Lower bedding volume (bottom of trench)
        h_bed = min(seg.lower_bedding_thickness_m, avg_eff_h)
        w_bed_mid = bottom_w + slope_hv * h_bed
        lower_bed_m3 = w_bed_mid * h_bed * seg.length_m

        # Pipe outer displacement volume
        pipe_disp_m3 = (math.pi / 4.0) * (od_m**2) * seg.length_m

        # Pipe zone & protective cover above pipe crown (excluding lower bedding and pipe displacement)
        target_zone_h = od_m + seg.protective_cover_above_crown_m
        avail_zone_h = max(0.0, avg_eff_h - h_bed)
        eff_zone_h = min(target_zone_h, avail_zone_h)
        w_zone_mid = bottom_w + slope_hv * (2.0 * h_bed + eff_zone_h)
        gross_zone_m3 = w_zone_mid * eff_zone_h * seg.length_m
        pipe_zone_net_m3 = max(0.0, gross_zone_m3 - pipe_disp_m3)

        total_bed_and_zone_m3 = lower_bed_m3 + pipe_zone_net_m3

        # Main backfill from native excavated soil & excess displaced spoil
        backfill_m3 = max(0.0, seg_exc_m3 - total_bed_and_zone_m3 - pipe_disp_m3)
        excess_spoil_m3 = max(0.0, seg_exc_m3 - backfill_m3)

        # Machine vs. Hand excavation split (base ratio + crossing protection zones)
        crossing_hand_len = min(seg.length_m, seg.crossings_count * crossing_hand_zone_length_m)
        crossing_len_fraction = crossing_hand_len / seg.length_m if seg.length_m > 0 else 0.0
        effective_hand_ratio = min(1.0, hand_excavation_ratio + (1.0 - hand_excavation_ratio) * crossing_len_fraction)
        seg_hand_m3 = seg_exc_m3 * effective_hand_ratio
        seg_machine_m3 = max(0.0, seg_exc_m3 - seg_hand_m3)

        # Warning tape & optional geotextile trench wrap
        tape_m = seg.length_m
        geotextile_m2 = 0.0
        if seg.wrap_pipe_zone_in_geotextile:
            # Perimeter around bedding + pipe zone (bottom + top + 2 vertical sides) with 15% overlap
            wrap_perimeter_m = 2.0 * (bottom_w + h_bed + eff_zone_h)
            geotextile_m2 = wrap_perimeter_m * seg.length_m * 1.15

        pipe_breakdowns.append(
            TrenchSegmentBreakdown(
                segment_id=seg.segment_id,
                utility_type=seg.utility_type,
                pipe_material=seg.pipe_material,
                nominal_diameter_dn_mm=seg.nominal_diameter_dn_mm,
                outer_diameter_m=round(od_m, 3),
                length_m=round(seg.length_m, 2),
                trench_bottom_width_m=round(bottom_w, 3),
                avg_total_excavation_depth_m=round(avg_eff_h, 3),
                total_excavation_m3=round(seg_exc_m3, 3),
                excavation_0_to_1_5m_m3=round(seg_0_15, 3),
                excavation_1_5_to_3_0m_m3=round(seg_15_30, 3),
                excavation_over_3_0m_m3=round(seg_over_30, 3),
                machine_excavation_m3=round(seg_machine_m3, 3),
                hand_excavation_m3=round(seg_hand_m3, 3),
                lower_bedding_net_m3=round(lower_bed_m3, 3),
                pipe_zone_cover_net_m3=round(pipe_zone_net_m3, 3),
                total_bedding_and_pipe_zone_net_m3=round(total_bed_and_zone_m3, 3),
                pipe_displacement_volume_m3=round(pipe_disp_m3, 3),
                main_backfill_from_excavated_m3=round(backfill_m3, 3),
                excess_displaced_spoil_m3=round(excess_spoil_m3, 3),
                shoring_area_m2=round(seg_shoring_m2, 2),
                warning_tape_length_m=round(tape_m, 2),
                geotextile_wrap_area_m2=round(geotextile_m2, 2),
            )
        )

        total_pipe_length_m += seg.length_m
        total_exc_m3 += seg_exc_m3
        exc_0_15_m3 += seg_0_15
        exc_15_30_m3 += seg_15_30
        exc_over_30_m3 += seg_over_30
        total_machine_m3 += seg_machine_m3
        total_hand_m3 += seg_hand_m3
        total_lower_bed_m3 += lower_bed_m3
        total_zone_cov_m3 += pipe_zone_net_m3
        total_displacement_m3 += pipe_disp_m3
        total_backfill_m3 += backfill_m3
        total_spoil_m3 += excess_spoil_m3
        total_shoring_m2 += seg_shoring_m2
        total_tape_m += tape_m
        total_geotextile_m2 += geotextile_m2

    # Process Manhole & Road Gully Pits
    for mh in parsed_pits:
        if mh.outer_diameter_m is not None:
            mh_od_m = mh.outer_diameter_m
        elif mh.nominal_diameter_mm <= 600:
            mh_od_m = (mh.nominal_diameter_mm / 1000.0) + 0.20
        else:
            mh_od_m = (mh.nominal_diameter_mm / 1000.0) + 0.24

        pit_w = mh_od_m + 2.0 * mh.working_space_m
        total_mh_depth = max(
            0.0,
            mh.depth_to_invert_m + mh.base_slab_thickness_m + mh.under_base_bedding_m - mh.pavement_box_deduction_m,
        )
        pit_area_m2 = pit_w * pit_w  # Square excavation pit around circular/square shaft
        pit_exc_m3 = pit_area_m2 * total_mh_depth

        # Depth band allocation for manhole pit
        h_0_15 = min(total_mh_depth, 1.50)
        h_15_30 = max(0.0, min(total_mh_depth, 3.00) - 1.50)
        h_over_30 = max(0.0, total_mh_depth - 3.00)
        pit_0_15 = pit_area_m2 * h_0_15
        pit_15_30 = pit_area_m2 * h_15_30
        pit_over_30 = pit_area_m2 * h_over_30

        # Under-base bedding
        mh_bed_m3 = pit_area_m2 * min(mh.under_base_bedding_m, total_mh_depth)

        # Manhole structure volume displacement
        struct_h = max(0.0, total_mh_depth - mh.under_base_bedding_m)
        if mh.nominal_diameter_mm <= 600:
            struct_disp_m3 = (mh_od_m**2) * struct_h
        else:
            struct_disp_m3 = (math.pi / 4.0) * (mh_od_m**2) * struct_h

        pit_backfill_m3 = max(0.0, pit_exc_m3 - mh_bed_m3 - struct_disp_m3)
        pit_spoil_m3 = max(0.0, pit_exc_m3 - pit_backfill_m3)

        pit_shoring_m2 = 0.0
        if total_mh_depth > shoring_depth_threshold_m:
            pit_shoring_m2 = 4.0 * pit_w * total_mh_depth

        pit_hand_m3 = pit_exc_m3 * hand_excavation_ratio
        pit_machine_m3 = max(0.0, pit_exc_m3 - pit_hand_m3)

        pit_breakdowns.append(
            TrenchManholePitBreakdown(
                node_id=mh.node_id,
                nominal_diameter_mm=mh.nominal_diameter_mm,
                outer_diameter_m=round(mh_od_m, 3),
                pit_width_m=round(pit_w, 3),
                total_excavation_depth_m=round(total_mh_depth, 3),
                pit_excavation_m3=round(pit_exc_m3, 3),
                under_base_bedding_m3=round(mh_bed_m3, 3),
                structure_displacement_m3=round(struct_disp_m3, 3),
                pit_backfill_m3=round(pit_backfill_m3, 3),
                pit_excess_spoil_m3=round(pit_spoil_m3, 3),
                pit_shoring_area_m2=round(pit_shoring_m2, 2),
            )
        )

        total_exc_m3 += pit_exc_m3
        exc_0_15_m3 += pit_0_15
        exc_15_30_m3 += pit_15_30
        exc_over_30_m3 += pit_over_30
        total_machine_m3 += pit_machine_m3
        total_hand_m3 += pit_hand_m3
        total_mh_bed_m3 += mh_bed_m3
        total_displacement_m3 += struct_disp_m3
        total_backfill_m3 += pit_backfill_m3
        total_spoil_m3 += pit_spoil_m3
        total_shoring_m2 += pit_shoring_m2

    total_bed_and_zone_net = total_lower_bed_m3 + total_zone_cov_m3 + total_mh_bed_m3
    order_bed_vol_m3 = total_bed_and_zone_net * bedding_compaction_factor
    order_bed_tonnage_t = order_bed_vol_m3 * bedding_bulk_density_t_per_m3

    return TrenchEarthworkAndBeddingReport(
        standard_profile=standard_profile,
        total_pipe_length_m=round(total_pipe_length_m, 2),
        manhole_pits_count=len(pit_breakdowns),
        shoring_depth_threshold_m=shoring_depth_threshold_m,
        hand_excavation_ratio_applied=hand_excavation_ratio,
        bedding_compaction_and_loss_factor=bedding_compaction_factor,
        bedding_bulk_density_t_per_m3=bedding_bulk_density_t_per_m3,
        total_excavation_m3=round(total_exc_m3, 3),
        excavation_0_to_1_5m_m3=round(exc_0_15_m3, 3),
        excavation_1_5_to_3_0m_m3=round(exc_15_30_m3, 3),
        excavation_over_3_0m_m3=round(exc_over_30_m3, 3),
        total_machine_excavation_m3=round(total_machine_m3, 3),
        total_hand_excavation_m3=round(total_hand_m3, 3),
        total_lower_bedding_net_m3=round(total_lower_bed_m3, 3),
        total_pipe_zone_cover_net_m3=round(total_zone_cov_m3, 3),
        total_manhole_under_bedding_net_m3=round(total_mh_bed_m3, 3),
        total_bedding_and_zone_net_m3=round(total_bed_and_zone_net, 3),
        total_bedding_order_volume_m3=round(order_bed_vol_m3, 3),
        total_bedding_order_tonnage_t=round(order_bed_tonnage_t, 3),
        total_pipe_and_manhole_displacement_m3=round(total_displacement_m3, 3),
        total_main_backfill_m3=round(total_backfill_m3, 3),
        total_excess_spoil_haul_off_m3=round(total_spoil_m3, 3),
        total_shoring_area_m2=round(total_shoring_m2, 2),
        total_warning_tape_m=round(total_tape_m, 2),
        total_geotextile_wrap_m2=round(total_geotextile_m2, 2),
        pipe_breakdowns=pipe_breakdowns,
        manhole_pit_breakdowns=pit_breakdowns,
        warnings=warnings,
    )
