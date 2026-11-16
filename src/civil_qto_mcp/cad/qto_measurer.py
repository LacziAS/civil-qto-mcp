"""
Tool 2 implementation: measure_linear_and_area_features
Deterministic linear (m) and area (m2) quantity take-off with:
- 2D horizontal projection (Z=0) and Flying-Z anomaly reporting
- Exact analytical straight vs. curved length breakdown
- 1D Shapely topological overlap deduplication (partial & full colinear overlaps)
- 2D Shapely boolean difference for net pavement/surface area with island deduction
  (guaranteed zero double-deduction even when HATCHes already contain interior hole loops)
- Configurable VOB/C minimum island area deduction threshold (min_island_deduction_m2)
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from civil_qto_mcp.cad.dwg_loader import load_cad_document
from civil_qto_mcp.cad.geometry_engine import (
    ExtractedLinearSegment,
    check_entity_z_anomaly,
    deduplicate_linear_geometries,
    extract_linear_segment,
    extract_polygons_from_entities,
    iter_target_entities,
)
from civil_qto_mcp.models.dxf_models import (
    AreaMeasurementSummary,
    ElevationAnomaly,
    LayerLinearBreakdown,
    LinearMeasurementSummary,
    MeasureFeaturesReport,
)


def measure_linear_and_area_features(
    file_path: str | Path,
    unit_scale_to_meter: float = 1.0,
    linear_layers: list[str] | None = None,
    boundary_layers: list[str] | None = None,
    island_hole_layers: list[str] | None = None,
    include_hatches: bool = True,
    deduplicate_linear_overlaps: bool = True,
    snap_tolerance_m: float = 0.01,
    auto_close_gap_tolerance_m: float = 0.10,
    min_island_deduction_m2: float = 0.0,
    explode_inserts: bool = True,
    max_sagitta_error_m: float = 0.002,
) -> MeasureFeaturesReport:
    """
    Deterministically measure linear lengths (m) and net surface areas (m2) on specified DXF layers.
    """
    if unit_scale_to_meter <= 0:
        raise ValueError(f"unit_scale_to_meter must be positive, got {unit_scale_to_meter}")

    doc, path_obj = load_cad_document(file_path)
    msp = doc.modelspace()

    linear_layer_list = [l for l in (linear_layers or []) if l]
    boundary_layer_list = [l for l in (boundary_layers or []) if l]
    island_layer_list = [l for l in (island_hole_layers or []) if l]

    elevation_anomalies: list[ElevationAnomaly] = []
    seen_anomaly_handles: set[str] = set()
    warnings: list[str] = []

    # -------------------------------------------------------------------------
    # 1. LINEAR MEASUREMENT PIPELINE
    # -------------------------------------------------------------------------
    linear_summary: LinearMeasurementSummary | None = None
    if linear_layer_list:
        target_lin_set = set(linear_layer_list)
        segments_by_layer: dict[str, list[ExtractedLinearSegment]] = defaultdict(list)
        all_linear_segments: list[ExtractedLinearSegment] = []

        for ent, eff_layer, handle in iter_target_entities(
            msp, target_layers=target_lin_set, explode_inserts=explode_inserts
        ):
            if handle not in seen_anomaly_handles:
                anom = check_entity_z_anomaly(ent, eff_layer, handle, unit_scale_to_meter)
                if anom is not None:
                    elevation_anomalies.append(anom)
                    seen_anomaly_handles.add(handle)

            seg = extract_linear_segment(
                ent,
                effective_layer=eff_layer,
                handle=handle,
                unit_scale_to_meter=unit_scale_to_meter,
                max_sagitta_error_m=max_sagitta_error_m,
            )
            if seg is not None:
                segments_by_layer[eff_layer].append(seg)
                all_linear_segments.append(seg)

        by_layer_breakdowns: list[LayerLinearBreakdown] = []
        for lyr in linear_layer_list:
            lyr_segs = segments_by_layer.get(lyr, [])
            s_gross = sum(s.straight_length_m for s in lyr_segs)
            c_gross = sum(s.curved_length_m for s in lyr_segs)
            if deduplicate_linear_overlaps:
                gross_m, ded_m, net_m = deduplicate_linear_geometries(
                    lyr_segs, snap_tolerance_m=snap_tolerance_m
                )
            else:
                gross_m = s_gross + c_gross
                ded_m = 0.0
                net_m = gross_m

            by_layer_breakdowns.append(
                LayerLinearBreakdown(
                    layer_name=lyr,
                    entity_count=len(lyr_segs),
                    straight_gross_length_m=round(s_gross, 3),
                    curved_gross_length_m=round(c_gross, 3),
                    gross_length_m=round(gross_m, 3),
                    deducted_overlap_m=round(ded_m, 3),
                    net_length_m=round(net_m, 3),
                )
            )
            if ded_m > 0.01:
                warnings.append(
                    f"Layer '{lyr}': deducted {ded_m:.2f}m of duplicate/overlapping linear segments."
                )

        total_s_gross = sum(s.straight_length_m for s in all_linear_segments)
        total_c_gross = sum(s.curved_length_m for s in all_linear_segments)
        if deduplicate_linear_overlaps:
            tot_gross, tot_ded, tot_net = deduplicate_linear_geometries(
                all_linear_segments, snap_tolerance_m=snap_tolerance_m
            )
        else:
            tot_gross = total_s_gross + total_c_gross
            tot_ded = 0.0
            tot_net = tot_gross

        linear_summary = LinearMeasurementSummary(
            measured_layers=linear_layer_list,
            unit_scale_to_meter=unit_scale_to_meter,
            snap_tolerance_m=snap_tolerance_m,
            total_entities_processed=len(all_linear_segments),
            straight_gross_length_m=round(total_s_gross, 3),
            curved_gross_length_m=round(total_c_gross, 3),
            gross_length_m=round(tot_gross, 3),
            deducted_overlap_m=round(tot_ded, 3),
            net_length_m=round(tot_net, 3),
            by_layer=by_layer_breakdowns,
        )

    # -------------------------------------------------------------------------
    # 2. AREA MEASUREMENT PIPELINE (Topological Polygon Union & Island Subtraction)
    # -------------------------------------------------------------------------
    area_summary: AreaMeasurementSummary | None = None
    if boundary_layer_list:
        target_bnd_set = set(boundary_layer_list)
        bnd_entities = list(
            iter_target_entities(msp, target_layers=target_bnd_set, explode_inserts=explode_inserts)
        )
        for ent, eff_layer, handle in bnd_entities:
            if handle not in seen_anomaly_handles:
                anom = check_entity_z_anomaly(ent, eff_layer, handle, unit_scale_to_meter)
                if anom is not None:
                    elevation_anomalies.append(anom)
                    seen_anomaly_handles.add(handle)

        outer_polys, auto_closed_outer, outer_warns = extract_polygons_from_entities(
            bnd_entities,
            unit_scale_to_meter=unit_scale_to_meter,
            auto_close_gap_tolerance_m=auto_close_gap_tolerance_m,
            include_hatches=include_hatches,
            snap_tolerance_m=snap_tolerance_m,
            max_sagitta_error_m=max_sagitta_error_m,
        )
        warnings.extend(outer_warns)

        island_polys: list[Polygon] = []
        auto_closed_islands = []
        if island_layer_list:
            target_isl_set = set(island_layer_list)
            isl_entities = list(
                iter_target_entities(
                    msp, target_layers=target_isl_set, explode_inserts=explode_inserts
                )
            )
            island_polys, auto_closed_islands, isl_warns = extract_polygons_from_entities(
                isl_entities,
                unit_scale_to_meter=unit_scale_to_meter,
                auto_close_gap_tolerance_m=auto_close_gap_tolerance_m,
                include_hatches=include_hatches,
                snap_tolerance_m=snap_tolerance_m,
                max_sagitta_error_m=max_sagitta_error_m,
            )
            warnings.extend(isl_warns)

        all_auto_closed = auto_closed_outer + auto_closed_islands

        if outer_polys:
            outer_union = unary_union(outer_polys)
            gross_area_m2 = float(outer_union.area)
            outer_perimeter_m = float(outer_union.exterior.length) if isinstance(outer_union, Polygon) else sum(float(p.exterior.length) for p in outer_union.geoms if isinstance(p, Polygon))

            # Filter island polygons against outer_union and min_island_deduction_m2 (VOB/C rule)
            qualified_islands: list[Polygon] = []
            skipped_islands_area = 0.0
            skipped_islands_count = 0

            # Also inspect if outer_union already had interior holes (e.g. from a HATCH with holes)
            # If min_island_deduction_m2 > 0, fill back any pre-existing HATCH hole smaller than threshold!
            if min_island_deduction_m2 > 0:
                rebuilt_shells: list[Polygon] = []
                poly_parts = [outer_union] if isinstance(outer_union, Polygon) else list(outer_union.geoms)
                for p in poly_parts:
                    if not isinstance(p, Polygon):
                        continue
                    kept_holes = []
                    for interior in p.interiors:
                        hole_poly = Polygon(interior)
                        if hole_poly.area >= min_island_deduction_m2:
                            kept_holes.append(interior)
                        else:
                            skipped_islands_area += hole_poly.area
                            skipped_islands_count += 1
                    rebuilt_shells.append(Polygon(p.exterior, kept_holes))
                outer_union = unary_union(rebuilt_shells)
                gross_area_m2 = sum(Polygon(p.exterior).area for p in poly_parts if isinstance(p, Polygon))

            # Union explicit island polygons first so adjacent island segments form complete islands
            if island_polys:
                merged_islands = unary_union(island_polys)
                island_parts = (
                    [merged_islands]
                    if isinstance(merged_islands, Polygon)
                    else list(merged_islands.geoms)
                    if isinstance(merged_islands, MultiPolygon)
                    else []
                )
                for isl in island_parts:
                    if not isinstance(isl, Polygon):
                        continue
                    effective_isl = outer_union.intersection(isl)
                    if effective_isl.is_empty:
                        continue
                    if effective_isl.area >= min_island_deduction_m2:
                        qualified_islands.append(isl)
                    else:
                        skipped_islands_area += float(effective_isl.area)
                        skipped_islands_count += 1

            if qualified_islands:
                islands_union = unary_union(qualified_islands)
                # Boolean difference is idempotent: if HATCH already had a hole at the same location,
                # outer_union.difference(islands_union) will NOT double-subtract!
                net_geom = outer_union.difference(islands_union)
                # Also compute true gross area (outer shells filled) if outer_union came from a perforated HATCH
                poly_parts_for_gross = (
                    [outer_union]
                    if isinstance(outer_union, Polygon)
                    else list(outer_union.geoms)
                    if isinstance(outer_union, MultiPolygon)
                    else []
                )
                true_gross_geom = unary_union(
                    [Polygon(p.exterior) for p in poly_parts_for_gross if isinstance(p, Polygon)]
                )
                gross_area_m2 = float(true_gross_geom.area)
                net_area_m2 = float(net_geom.area)
                deducted_area_m2 = max(0.0, gross_area_m2 - net_area_m2)
                effective_deducted_geom = true_gross_geom.difference(net_geom)
                deducted_perim_m = float(effective_deducted_geom.length)
            else:
                # Even with no separate island_hole_layers, a HATCH might have internal holes
                poly_parts_for_gross = (
                    [outer_union]
                    if isinstance(outer_union, Polygon)
                    else list(outer_union.geoms)
                    if isinstance(outer_union, MultiPolygon)
                    else []
                )
                true_gross_geom = unary_union(
                    [Polygon(p.exterior) for p in poly_parts_for_gross if isinstance(p, Polygon)]
                )
                gross_area_m2 = float(true_gross_geom.area)
                net_area_m2 = float(outer_union.area)
                deducted_area_m2 = max(0.0, gross_area_m2 - net_area_m2)
                effective_deducted_geom = true_gross_geom.difference(outer_union)
                deducted_perim_m = float(effective_deducted_geom.length)

            area_summary = AreaMeasurementSummary(
                boundary_layers=boundary_layer_list,
                island_hole_layers=island_layer_list,
                min_island_deduction_m2=min_island_deduction_m2,
                auto_close_gap_tolerance_m=auto_close_gap_tolerance_m,
                outer_polygon_count=len(outer_polys),
                island_polygon_count_deducted=len(qualified_islands),
                island_polygon_count_skipped_below_threshold=skipped_islands_count,
                gross_area_m2=round(gross_area_m2, 3),
                deducted_islands_area_m2=round(deducted_area_m2, 3),
                skipped_small_islands_area_m2=round(skipped_islands_area, 3),
                net_area_m2=round(net_area_m2, 3),
                outer_perimeter_m=round(outer_perimeter_m, 3),
                deducted_islands_perimeter_m=round(deducted_perim_m, 3),
                auto_closed_polygons=all_auto_closed,
            )
        else:
            area_summary = AreaMeasurementSummary(
                boundary_layers=boundary_layer_list,
                island_hole_layers=island_layer_list,
                min_island_deduction_m2=min_island_deduction_m2,
                auto_close_gap_tolerance_m=auto_close_gap_tolerance_m,
            )
            warnings.append(
                f"No valid closed polygons or hatches found on boundary_layers={boundary_layer_list}."
            )

    return MeasureFeaturesReport(
        file_path=str(path_obj),
        unit_scale_to_meter=unit_scale_to_meter,
        linear_summary=linear_summary,
        area_summary=area_summary,
        elevation_anomalies=elevation_anomalies,
        warnings=warnings,
    )
