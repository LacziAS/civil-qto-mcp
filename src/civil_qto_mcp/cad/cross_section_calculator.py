"""
Tool 20 implementation: calculate_cross_section_and_embankment_qto
Calculates roadside typical cross-section (mintakeresztszelvény) quantities along a road alignment:
- Unpaved / crushed-stone road shoulders (útpadka m2, compacted m3, order m3, and tonnes t)
  including outer batter-slope wedge volume,
- Embankment & cut slope inclined surface area (rézsűfelület m2) and slope topsoil spreading (humuszterítés m3),
- Trapezoidal roadside drainage & infiltration ditches (trapéz útárok / szikkasztóárok):
  excavation (m3), wetted lining/grassing/precast element area (m2), washed gravel fill (m3),
  and geotextile trench wrapping (m2).
"""

from __future__ import annotations

import math
from pathlib import Path

from civil_qto_mcp.cad.qto_measurer import measure_linear_and_area_features
from civil_qto_mcp.models.dxf_models import (
    CrossSectionAndEmbankmentReport,
    RoadCrossSectionSegmentBreakdown,
    RoadCrossSectionSegmentInput,
)


def calculate_cross_section_and_embankment_qto(
    segments: list[RoadCrossSectionSegmentInput | dict] | None = None,
    dxf_path: str | Path | None = None,
    alignment_layers: list[str] | None = None,
    unit_scale_to_meter: float = 1.0,
    shoulder_compaction_factor: float = 1.15,
    shoulder_bulk_density_t_per_m3: float = 2.05,
) -> CrossSectionAndEmbankmentReport:
    """
    Compute road shoulder, embankment/cut slope, topsoil, hydroseeding, and roadside ditch /
    infiltration trench quantities from typical cross-section parameters and alignment lengths.
    """
    warnings: list[str] = []

    parsed_segments: list[RoadCrossSectionSegmentInput] = [
        s if isinstance(s, RoadCrossSectionSegmentInput) else RoadCrossSectionSegmentInput.model_validate(s)
        for s in (segments or [])
    ]

    if dxf_path is not None and not parsed_segments:
        if not alignment_layers:
            raise ValueError("When `dxf_path` is used without `segments`, `alignment_layers` must be specified.")
        meas = measure_linear_and_area_features(
            file_path=dxf_path,
            linear_layers=alignment_layers,
            custom_scale_to_meter=unit_scale_to_meter,
        )
        for idx, lyr in enumerate(meas.linear_layers, start=1):
            if lyr.deduplicated_length_m > 0:
                parsed_segments.append(
                    RoadCrossSectionSegmentInput(
                        segment_id=f"{lyr.layer_name}-{idx:02d}",
                        length_m=lyr.deduplicated_length_m,
                    )
                )

    if not parsed_segments:
        raise ValueError("At least one RoadCrossSectionSegmentInput (or valid dxf_path + alignment_layers) is required.")

    breakdowns: list[RoadCrossSectionSegmentBreakdown] = []

    total_align_len = 0.0
    total_sh_area = 0.0
    total_sh_comp_vol = 0.0
    total_sh_ord_vol = 0.0
    total_sh_ord_t = 0.0
    total_slope_area = 0.0
    total_slope_topsoil = 0.0
    total_ditch_len = 0.0
    total_ditch_exc = 0.0
    total_ditch_lining = 0.0
    total_grass_area = 0.0
    total_inf_gravel = 0.0
    total_inf_geo = 0.0

    for seg in parsed_segments:
        length_m = seg.length_m
        t_sh_m = seg.shoulder_thickness_cm / 100.0
        m_slope = seg.slope_ratio_h_to_v

        # 1. Road Shoulders (Left + Right, including outer batter wedge 0.5 * m * t_sh)
        sh_top_w = seg.shoulder_width_left_m + seg.shoulder_width_right_m
        sh_top_area = sh_top_w * length_m

        sh_cross_area_m2 = 0.0
        for w_side in (seg.shoulder_width_left_m, seg.shoulder_width_right_m):
            if w_side > 0 and t_sh_m > 0:
                avg_w = w_side + 0.5 * m_slope * t_sh_m
                sh_cross_area_m2 += avg_w * t_sh_m

        sh_comp_vol = sh_cross_area_m2 * length_m
        sh_ord_vol = sh_comp_vol * shoulder_compaction_factor
        sh_ord_t = sh_ord_vol * shoulder_bulk_density_t_per_m3

        # 2. Embankment / Cut Slopes (Inclined surface vs. horizontal projection)
        h_sum = seg.embankment_height_left_m + seg.embankment_height_right_m
        slope_horiz_m2 = h_sum * m_slope * length_m
        inclined_factor = math.sqrt(1.0 + m_slope**2)
        slope_inclined_m2 = h_sum * inclined_factor * length_m
        slope_topsoil_m3 = slope_inclined_m2 * seg.slope_topsoil_thickness_m

        if max(seg.embankment_height_left_m, seg.embankment_height_right_m) > 2.0 and m_slope < 1.5:
            warnings.append(
                f"Segment '{seg.segment_id}': embankment height > 2.0m with steep slope 1:{m_slope} (< 1:1.5); "
                "geogrid reinforcement or slope stability check recommended."
            )

        # 3. Trapezoidal Roadside Drainage / Infiltration Ditch
        if seg.ditch_sides == "NONE":
            ditch_mult = 0
        elif seg.ditch_sides in ("LEFT", "RIGHT"):
            ditch_mult = 1
        else:
            ditch_mult = 2

        ditch_len = ditch_mult * length_m
        b_bot = seg.ditch_bottom_width_m
        d_depth = seg.ditch_depth_m
        m_in = seg.ditch_inner_slope_h_to_v
        m_out = seg.ditch_outer_slope_h_to_v

        if ditch_len > 0 and d_depth > 0:
            ditch_top_w = b_bot + d_depth * (m_in + m_out)
            ditch_cs_area = 0.5 * (b_bot + ditch_top_w) * d_depth
            ditch_exc_m3 = ditch_cs_area * ditch_len

            wetted_perim = (
                b_bot
                + d_depth * math.sqrt(1.0 + m_in**2)
                + d_depth * math.sqrt(1.0 + m_out**2)
            )
            ditch_lining_m2 = wetted_perim * ditch_len

            if seg.ditch_lining_type == "GRAVEL_INFILTRATION_TRENCH" or seg.infiltration_gravel_depth_m > 0:
                h_grav = (
                    min(d_depth, seg.infiltration_gravel_depth_m)
                    if seg.infiltration_gravel_depth_m > 0
                    else d_depth
                )
                b_grav_top = b_bot + h_grav * (m_in + m_out)
                grav_cs_area = 0.5 * (b_bot + b_grav_top) * h_grav
                inf_gravel_m3 = grav_cs_area * ditch_len

                geo_perim = (
                    b_bot
                    + b_grav_top
                    + h_grav * (math.sqrt(1.0 + m_in**2) + math.sqrt(1.0 + m_out**2))
                ) * 1.15
                inf_geo_m2 = geo_perim * ditch_len
            else:
                inf_gravel_m3 = 0.0
                inf_geo_m2 = 0.0
        else:
            ditch_top_w = 0.0
            ditch_exc_m3 = 0.0
            ditch_lining_m2 = 0.0
            inf_gravel_m3 = 0.0
            inf_geo_m2 = 0.0

        seg_grass_m2 = slope_inclined_m2 + (
            ditch_lining_m2 if seg.ditch_lining_type == "UNLINED_GRASS" else 0.0
        )

        breakdowns.append(
            RoadCrossSectionSegmentBreakdown(
                segment_id=seg.segment_id,
                length_m=round(length_m, 2),
                shoulder_top_area_m2=round(sh_top_area, 2),
                shoulder_compacted_volume_m3=round(sh_comp_vol, 3),
                shoulder_order_volume_m3=round(sh_ord_vol, 3),
                shoulder_order_tonnage_t=round(sh_ord_t, 3),
                slope_horizontal_projection_m2=round(slope_horiz_m2, 2),
                slope_inclined_surface_area_m2=round(slope_inclined_m2, 2),
                slope_topsoil_spread_volume_m3=round(slope_topsoil_m3, 3),
                ditch_total_length_m=round(ditch_len, 2),
                ditch_top_width_m=round(ditch_top_w, 3),
                ditch_excavation_volume_m3=round(ditch_exc_m3, 3),
                ditch_wetted_lining_area_m2=round(ditch_lining_m2, 2),
                infiltration_gravel_volume_m3=round(inf_gravel_m3, 3),
                infiltration_geotextile_wrap_m2=round(inf_geo_m2, 2),
            )
        )

        total_align_len += length_m
        total_sh_area += sh_top_area
        total_sh_comp_vol += sh_comp_vol
        total_sh_ord_vol += sh_ord_vol
        total_sh_ord_t += sh_ord_t
        total_slope_area += slope_inclined_m2
        total_slope_topsoil += slope_topsoil_m3
        total_ditch_len += ditch_len
        total_ditch_exc += ditch_exc_m3
        total_ditch_lining += ditch_lining_m2
        total_grass_area += seg_grass_m2
        total_inf_gravel += inf_gravel_m3
        total_inf_geo += inf_geo_m2

    return CrossSectionAndEmbankmentReport(
        segments_count=len(breakdowns),
        total_alignment_length_m=round(total_align_len, 2),
        shoulder_compaction_factor=shoulder_compaction_factor,
        shoulder_bulk_density_t_per_m3=shoulder_bulk_density_t_per_m3,
        total_shoulder_top_area_m2=round(total_sh_area, 2),
        total_shoulder_compacted_volume_m3=round(total_sh_comp_vol, 3),
        total_shoulder_order_volume_m3=round(total_sh_ord_vol, 3),
        total_shoulder_order_tonnage_t=round(total_sh_ord_t, 3),
        total_slope_inclined_area_m2=round(total_slope_area, 2),
        total_slope_topsoil_volume_m3=round(total_slope_topsoil, 3),
        total_ditch_length_m=round(total_ditch_len, 2),
        total_ditch_excavation_m3=round(total_ditch_exc, 3),
        total_ditch_lining_or_grass_m2=round(total_ditch_lining, 2),
        total_grass_seeding_area_m2=round(total_grass_area, 2),
        total_infiltration_gravel_m3=round(total_inf_gravel, 3),
        total_infiltration_geotextile_m2=round(total_inf_geo, 2),
        segment_breakdowns=breakdowns,
        warnings=warnings,
    )
