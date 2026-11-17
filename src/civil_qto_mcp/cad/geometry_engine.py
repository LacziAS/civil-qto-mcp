"""
Core 2D/2.5D computational geometry engine bridging ezdxf and Shapely 2.0.

Handles:
- OCS -> WCS coordinate transformations (including mirrored Z=-1 extrusion vectors)
- 2D horizontal plane projection (Z=0) with 'Flying Z' elevation anomaly detection
- Exact analytical straight vs. circular arc/bulge length calculation
- Adaptive sagitta-bounded curve discretization for SPLINE, ELLIPSE, ARC, LWPOLYLINE, and HATCH
- Topological reconstruction of HATCH outer shells and interior holes
- Virtual explosion of nested INSERT block references
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

import shapely
from ezdxf.entities import DXFEntity, Hatch, Insert, LWPolyline, Polyline
from ezdxf.lldxf.const import DXFError
from ezdxf.math import Vec3
from ezdxf.path import Path, from_hatch, make_path
from ezdxf.tools.text import plain_mtext
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from civil_qto_mcp.models.dxf_models import AutoClosedPolygonInfo, ElevationAnomaly

LINEAR_ENTITY_TYPES = frozenset(
    {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
)
AREA_ENTITY_TYPES = frozenset(
    {"LWPOLYLINE", "POLYLINE", "CIRCLE", "ELLIPSE", "SPLINE", "HATCH", "LINE", "ARC"}
)


@dataclass
class ExtractedLinearSegment:
    """Intermediate representation of a linear CAD entity projected to 2D meters."""

    handle: str
    layer: str
    entity_type: str
    straight_length_m: float
    curved_length_m: float
    geometry_2d: LineString | MultiLineString


def clean_cad_text(raw_text: str) -> str:
    """
    Strip AutoCAD MTEXT/TEXT control codes and translate special diameter/degree symbols.
    """
    if not raw_text:
        return ""
    pre_cleaned = re.sub(r"%%[cC]", "DN", raw_text)
    pre_cleaned = re.sub(r"%%[dD]", "°", pre_cleaned)
    pre_cleaned = re.sub(r"%%[pP]", "±", pre_cleaned)
    pre_cleaned = re.sub(r"%%[uU]", "", pre_cleaned)
    cleaned = plain_mtext(pre_cleaned, split=False)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return " | ".join(lines)


def extract_mleader_info(
    entity: DXFEntity, unit_scale_to_meter: float = 1.0
) -> tuple[str, tuple[float, float] | None, list[tuple[float, float]]]:
    """
    Extract (cleaned_text, text_anchor_xy_m, arrow_tip_points_xy_m) from a MULTILEADER / MLEADER entity.
    """
    if entity.dxftype() not in ("MULTILEADER", "MLEADER"):
        return "", None, []

    ctx = getattr(entity, "context", None)
    if ctx is None:
        return "", None, []

    raw_text = ""
    text_anchor: tuple[float, float] | None = None
    mtext_data = getattr(ctx, "mtext", None)
    if mtext_data is not None:
        raw_text = str(getattr(mtext_data, "default_content", "") or "")
        ins = getattr(mtext_data, "insert", None)
        if ins is not None:
            text_anchor = (
                round(float(ins.x) * unit_scale_to_meter, 3),
                round(float(ins.y) * unit_scale_to_meter, 3),
            )

    arrow_tips: list[tuple[float, float]] = []
    for ldr in getattr(ctx, "leaders", []) or []:
        for ln in getattr(ldr, "lines", []) or []:
            verts = getattr(ln, "vertices", None)
            if verts:
                v0 = verts[0]
                arrow_tips.append(
                    (
                        round(float(v0.x) * unit_scale_to_meter, 3),
                        round(float(v0.y) * unit_scale_to_meter, 3),
                    )
                )

    return clean_cad_text(raw_text), text_anchor, arrow_tips


def iter_target_entities(
    msp: Iterable[DXFEntity],
    target_layers: set[str] | None = None,
    explode_inserts: bool = True,
    max_explode_depth: int = 3,
) -> Iterable[tuple[DXFEntity, str, str]]:
    """
    Iterate over Modelspace entities matching target_layers.
    Yields (entity, effective_layer_name, root_handle).
    When explode_inserts=True, recursively yields virtual entities from INSERT blocks
    while preserving either the parent INSERT's layer (for layer '0' sub-entities) or
    the sub-entity's explicit layer.
    """

    def _walk(
        entities: Iterable[DXFEntity],
        parent_layer: str | None,
        root_handle: str | None,
        depth: int,
    ) -> Iterable[tuple[DXFEntity, str, str]]:
        for ent in entities:
            dxftype = ent.dxftype()
            raw_layer = getattr(ent.dxf, "layer", "0") or "0"
            eff_layer = (
                parent_layer
                if (raw_layer == "0" and parent_layer is not None)
                else raw_layer
            )
            eff_handle = root_handle or getattr(ent.dxf, "handle", "UNKNOWN") or "UNKNOWN"

            if dxftype == "INSERT" and explode_inserts and depth < max_explode_depth:
                assert isinstance(ent, Insert)
                # Only explode if the INSERT itself is on a target layer, or if sub-entities might be
                try:
                    virt_ents = list(ent.virtual_entities())
                except (DXFError, ValueError, TypeError, AttributeError):
                    virt_ents = []
                if virt_ents:
                    yield from _walk(virt_ents, eff_layer, eff_handle, depth + 1)
                    continue

            if target_layers is None or eff_layer in target_layers:
                yield ent, eff_layer, str(eff_handle)

    yield from _walk(msp, None, None, 0)


def check_entity_z_anomaly(
    entity: DXFEntity,
    effective_layer: str,
    handle: str,
    unit_scale_to_meter: float,
    z_jump_threshold_m: float = 0.02,
) -> ElevationAnomaly | None:
    """
    Inspect 3D WCS vertices of a linear or polygonal entity to detect 'Flying Z' jumps
    (e.g., one vertex snapped to Z=114.5m while others sit at Z=0.0m).
    """
    dxftype = entity.dxftype()
    z_values_raw: list[float] = []

    try:
        if dxftype == "LINE":
            z_values_raw.append(float(entity.dxf.start.z))
            z_values_raw.append(float(entity.dxf.end.z))
        elif dxftype == "LWPOLYLINE":
            assert isinstance(entity, LWPolyline)
            ocs = entity.ocs()
            elev = float(entity.dxf.elevation)
            for x, y, *_ in entity.get_points("xy"):
                wcs_pt = ocs.to_wcs(Vec3(x, y, elev))
                z_values_raw.append(float(wcs_pt.z))
        elif dxftype == "POLYLINE":
            assert isinstance(entity, Polyline)
            ocs = entity.ocs()
            for v in entity.vertices:
                loc = Vec3(v.dxf.location)
                wcs_pt = ocs.to_wcs(loc) if not entity.is_3d_polyline else loc
                z_values_raw.append(float(wcs_pt.z))
        elif dxftype == "SPLINE":
            for pt in entity.control_points:
                z_values_raw.append(float(pt[2]))
    except (DXFError, ValueError, TypeError, AttributeError, IndexError):
        return None

    if len(z_values_raw) < 2:
        return None

    min_z_m = min(z_values_raw) * unit_scale_to_meter
    max_z_m = max(z_values_raw) * unit_scale_to_meter
    delta_z_m = max_z_m - min_z_m

    if delta_z_m > z_jump_threshold_m:
        return ElevationAnomaly(
            handle=handle,
            layer=effective_layer,
            entity_type=dxftype,
            min_z_m=round(min_z_m, 4),
            max_z_m=round(max_z_m, 4),
            delta_z_m=round(delta_z_m, 4),
        )
    return None


def _analytical_bulge_or_chord_length_2d(
    p1: tuple[float, float], p2: tuple[float, float], bulge: float
) -> tuple[float, float]:
    """
    Compute exact 2D (straight_length, curved_length) between p1 and p2 given DXF bulge.
    bulge = tan(theta / 4), where theta is the included arc angle.
    """
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-12:
        return 0.0, 0.0
    b = abs(float(bulge))
    if b < 1e-7:
        return chord, 0.0
    theta = 4.0 * math.atan(b)
    radius = chord * (1.0 + b * b) / (4.0 * b)
    arc_length = radius * theta
    return 0.0, arc_length


def _path_to_2d_coords(
    path: Path, unit_scale_to_meter: float, max_sagitta_error_m: float = 0.002
) -> list[tuple[float, float]]:
    """
    Discretize an ezdxf Path into 2D meter coordinates using adaptive sagitta tolerance.
    """
    if len(path) == 0:
        return []
    max_sagitta_raw = max(max_sagitta_error_m / max(unit_scale_to_meter, 1e-6), 1e-5)
    coords: list[tuple[float, float]] = []
    for vec in path.flattening(distance=max_sagitta_raw):
        pt = (float(vec.x) * unit_scale_to_meter, float(vec.y) * unit_scale_to_meter)
        if not coords or math.hypot(pt[0] - coords[-1][0], pt[1] - coords[-1][1]) > 1e-7:
            coords.append(pt)
    return coords


def extract_linear_segment(
    entity: DXFEntity,
    effective_layer: str,
    handle: str,
    unit_scale_to_meter: float,
    max_sagitta_error_m: float = 0.002,
) -> ExtractedLinearSegment | None:
    """
    Convert a linear CAD entity into an ExtractedLinearSegment with exact analytical
    straight/curved length breakdown and a 2D Shapely LineString in meters.
    """
    dxftype = entity.dxftype()
    if dxftype not in LINEAR_ENTITY_TYPES:
        return None

    try:
        path = make_path(entity)
    except (DXFError, ValueError, TypeError, AttributeError, ZeroDivisionError):
        return None

    coords_2d = _path_to_2d_coords(path, unit_scale_to_meter, max_sagitta_error_m)
    if len(coords_2d) < 2:
        return None

    line_geom = LineString(coords_2d)
    if line_geom.is_empty or line_geom.length < 1e-6:
        return None

    straight_m = 0.0
    curved_m = 0.0

    # Analytical length breakdown by entity type in 2D horizontal WCS projection
    if dxftype == "LINE":
        straight_m = line_geom.length
    elif dxftype in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
        curved_m = line_geom.length
    elif dxftype == "LWPOLYLINE":
        assert isinstance(entity, LWPolyline)
        ocs = entity.ocs()
        elev = float(entity.dxf.elevation)
        raw_pts = list(entity.get_points("xyb"))
        if len(raw_pts) >= 2:
            wcs_pts_2d: list[tuple[tuple[float, float], float]] = []
            for x, y, bulge in raw_pts:
                w = ocs.to_wcs(Vec3(x, y, elev))
                wcs_pts_2d.append(
                    ((float(w.x) * unit_scale_to_meter, float(w.y) * unit_scale_to_meter), float(bulge))
                )
            seg_count = len(wcs_pts_2d) if entity.closed else len(wcs_pts_2d) - 1
            for i in range(seg_count):
                p1, b1 = wcs_pts_2d[i]
                p2, _ = wcs_pts_2d[(i + 1) % len(wcs_pts_2d)]
                s_len, c_len = _analytical_bulge_or_chord_length_2d(p1, p2, b1)
                straight_m += s_len
                curved_m += c_len
        else:
            straight_m = line_geom.length
    elif dxftype == "POLYLINE":
        assert isinstance(entity, Polyline)
        ocs = entity.ocs()
        verts = list(entity.vertices)
        if len(verts) >= 2:
            wcs_pts_2d = []
            for v in verts:
                loc = Vec3(v.dxf.location)
                w = ocs.to_wcs(loc) if not entity.is_3d_polyline else loc
                bulge = float(getattr(v.dxf, "bulge", 0.0) or 0.0)
                wcs_pts_2d.append(
                    ((float(w.x) * unit_scale_to_meter, float(w.y) * unit_scale_to_meter), bulge)
                )
            seg_count = len(wcs_pts_2d) if entity.is_closed else len(wcs_pts_2d) - 1
            for i in range(seg_count):
                p1, b1 = wcs_pts_2d[i]
                p2, _ = wcs_pts_2d[(i + 1) % len(wcs_pts_2d)]
                s_len, c_len = _analytical_bulge_or_chord_length_2d(p1, p2, b1)
                straight_m += s_len
                curved_m += c_len
        else:
            straight_m = line_geom.length

    return ExtractedLinearSegment(
        handle=handle,
        layer=effective_layer,
        entity_type=dxftype,
        straight_length_m=straight_m,
        curved_length_m=curved_m,
        geometry_2d=line_geom,
    )


def deduplicate_linear_geometries(
    segments: list[ExtractedLinearSegment], snap_tolerance_m: float = 0.01
) -> tuple[float, float, float]:
    """
    Deduplicate exact and partially overlapping 2D linear segments using Shapely
    precision grid snapping and 1D topological union.

    Returns (gross_length_m, deducted_overlap_m, net_length_m).
    """
    if not segments:
        return 0.0, 0.0, 0.0

    gross_length_m = sum(s.straight_length_m + s.curved_length_m for s in segments)
    geoms = [s.geometry_2d for s in segments if not s.geometry_2d.is_empty]
    if not geoms:
        return gross_length_m, 0.0, gross_length_m

    if snap_tolerance_m > 0:
        # Use Shapely set_precision with grid_size to snap nearly-coincident colinear lines
        snapped_geoms = [
            shapely.set_precision(g, grid_size=snap_tolerance_m, mode="pointwise")
            for g in geoms
        ]
        merged = shapely.unary_union(snapped_geoms, grid_size=snap_tolerance_m)
    else:
        merged = unary_union(geoms)

    net_geom_length = float(merged.length)
    # Note: discretized chord length can be ~0.01% shorter than analytical arc length.
    # To avoid false micro-overlap deductions on pure curves, compare against raw chord sum.
    raw_chord_sum = sum(float(g.length) for g in geoms)
    chord_overlap = max(0.0, raw_chord_sum - net_geom_length)

    # Ignore sub-millimeter discretization noise
    if chord_overlap < max(snap_tolerance_m * 0.5, 0.002):
        deducted_overlap_m = 0.0
    else:
        deducted_overlap_m = min(gross_length_m, chord_overlap)

    net_length_m = max(0.0, gross_length_m - deducted_overlap_m)
    return gross_length_m, deducted_overlap_m, net_length_m


def _clean_to_polygons(geom: BaseGeometry) -> list[Polygon]:
    """Extract valid non-empty Polygon objects from any Shapely geometry."""
    if geom is None or geom.is_empty:
        return []
    valid = shapely.make_valid(geom)
    polys: list[Polygon] = []
    if isinstance(valid, Polygon):
        if valid.area > 1e-6:
            polys.append(valid)
    elif isinstance(valid, MultiPolygon):
        for p in valid.geoms:
            if p.area > 1e-6:
                polys.append(p)
    elif hasattr(valid, "geoms"):
        for sub in valid.geoms:
            polys.extend(_clean_to_polygons(sub))
    return polys


def extract_hatch_polygons(
    hatch: Hatch, unit_scale_to_meter: float, max_sagitta_error_m: float = 0.002
) -> list[Polygon]:
    """
    Extract topological 2D polygons from an AutoCAD HATCH entity, preserving inner holes
    (islands already excluded inside the hatch) via containment depth parity.
    """
    loop_polys: list[Polygon] = []
    try:
        paths = list(from_hatch(hatch))
    except (DXFError, ValueError, TypeError, AttributeError, ZeroDivisionError):
        paths = []

    for path in paths:
        for sub_path in path.sub_paths():
            coords = _path_to_2d_coords(sub_path, unit_scale_to_meter, max_sagitta_error_m)
            if len(coords) >= 3:
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                if len(coords) >= 4:
                    poly = Polygon(coords)
                    loop_polys.extend(_clean_to_polygons(poly))

    if not loop_polys:
        return []

    if len(loop_polys) == 1:
        return loop_polys

    # Sort descending by area so outer shells come before inner hole loops
    loop_polys.sort(key=lambda p: p.area, reverse=True)

    # Determine nesting depth of each loop polygon
    # Even depth (0, 2, ...): additive shell; Odd depth (1, 3, ...): subtractive hole
    result_geom: BaseGeometry = Polygon()
    for i, candidate in enumerate(loop_polys):
        depth = 0
        rep_pt = candidate.representative_point()
        for j in range(i):
            parent = loop_polys[j]
            if parent.contains(rep_pt):
                overlap_ratio = parent.intersection(candidate).area / max(candidate.area, 1e-9)
                if overlap_ratio > 0.90:
                    depth += 1
        if depth % 2 == 0:
            result_geom = result_geom.union(candidate)
        else:
            result_geom = result_geom.difference(candidate)

    return _clean_to_polygons(result_geom)


def extract_polygons_from_entities(
    entities_with_meta: Iterable[tuple[DXFEntity, str, str]],
    unit_scale_to_meter: float,
    auto_close_gap_tolerance_m: float = 0.10,
    include_hatches: bool = True,
    snap_tolerance_m: float = 0.01,
    max_sagitta_error_m: float = 0.002,
) -> tuple[list[Polygon], list[AutoClosedPolygonInfo], list[str]]:
    """
    Convert a collection of CAD entities on boundary or island layers into valid 2D Polygons.

    Handles:
    1. Closed LWPOLYLINE, POLYLINE, CIRCLE, ELLIPSE, closed SPLINE
    2. Open LWPOLYLINE / POLYLINE / SPLINE whose endpoints are within auto_close_gap_tolerance_m
    3. HATCH entities (preserving internal holes)
    4. Exploded LINE / ARC / open LWPOLYLINE boundary networks via shapely.ops.polygonize
    """
    polygons: list[Polygon] = []
    auto_closed_infos: list[AutoClosedPolygonInfo] = []
    warnings: list[str] = []
    loose_segments_for_polygonize: list[LineString] = []

    for ent, layer, handle in entities_with_meta:
        dxftype = ent.dxftype()

        if dxftype == "HATCH":
            if include_hatches:
                assert isinstance(ent, Hatch)
                hatch_polys = extract_hatch_polygons(
                    ent, unit_scale_to_meter, max_sagitta_error_m
                )
                polygons.extend(hatch_polys)
            continue

        if dxftype not in AREA_ENTITY_TYPES:
            continue

        try:
            path = make_path(ent)
        except (DXFError, ValueError, TypeError, AttributeError, ZeroDivisionError):
            path = None
        if path is None:
            continue

        coords = _path_to_2d_coords(path, unit_scale_to_meter, max_sagitta_error_m)
        if len(coords) < 2:
            continue

        is_explicitly_closed = False
        if dxftype in ("CIRCLE", "ELLIPSE"):
            # Full circle or closed ellipse
            gap_m = math.hypot(coords[0][0] - coords[-1][0], coords[0][1] - coords[-1][1])
            is_explicitly_closed = gap_m < 1e-3
        elif dxftype == "LWPOLYLINE":
            assert isinstance(ent, LWPolyline)
            is_explicitly_closed = bool(ent.closed)
        elif dxftype == "POLYLINE":
            assert isinstance(ent, Polyline)
            is_explicitly_closed = bool(ent.is_closed)
        elif dxftype == "SPLINE":
            is_explicitly_closed = bool(getattr(ent, "closed", False))

        gap_m = math.hypot(coords[0][0] - coords[-1][0], coords[0][1] - coords[-1][1])

        if is_explicitly_closed or gap_m <= 1e-4:
            if len(coords) >= 3:
                ring = coords if coords[0] == coords[-1] else [*coords, coords[0]]
                if len(ring) >= 4:
                    polygons.extend(_clean_to_polygons(Polygon(ring)))
            continue

        # Check if it is an "almost closed" single polyline within auto_close_gap_tolerance_m
        if (
            dxftype in ("LWPOLYLINE", "POLYLINE", "SPLINE")
            and len(coords) >= 3
            and 1e-4 < gap_m <= auto_close_gap_tolerance_m
        ):
            ring = [*coords, coords[0]]
            closed_polys = _clean_to_polygons(Polygon(ring))
            total_area = sum(p.area for p in closed_polys)
            if total_area > 1e-4:
                polygons.extend(closed_polys)
                info = AutoClosedPolygonInfo(
                    handle=handle,
                    layer=layer,
                    gap_distance_m=round(gap_m, 4),
                    start_point_xy=(round(coords[0][0], 3), round(coords[0][1], 3)),
                    end_point_xy=(round(coords[-1][0], 3), round(coords[-1][1], 3)),
                    resulting_area_m2=round(total_area, 3),
                )
                auto_closed_infos.append(info)
                warnings.append(
                    f"Auto-closed open {dxftype} (handle={handle}, layer='{layer}') with gap={gap_m:.3f}m -> area={total_area:.2f}m2."
                )
                continue

        # Otherwise keep as a loose 1D segment in case multiple exploded lines/arcs form a closed loop
        loose_segments_for_polygonize.append(LineString(coords))

    if loose_segments_for_polygonize:
        if snap_tolerance_m > 0:
            snapped = [
                shapely.set_precision(seg, grid_size=snap_tolerance_m, mode="pointwise")
                for seg in loose_segments_for_polygonize
            ]
            noded = shapely.unary_union(snapped, grid_size=snap_tolerance_m)
        else:
            noded = unary_union(loose_segments_for_polygonize)
        polygonized = list(polygonize(noded))

        # If open segments have corner gaps larger than snap_tolerance_m (1cm) but <= auto_close_gap_tolerance_m (10cm),
        # bridge nearest endpoints across segments/layers and re-run polygonize!
        if auto_close_gap_tolerance_m > snap_tolerance_m and len(loose_segments_for_polygonize) >= 2:
            endpoints: list[tuple[float, float]] = []
            for seg in loose_segments_for_polygonize:
                c_list = list(seg.coords)
                if len(c_list) >= 2:
                    endpoints.append((float(c_list[0][0]), float(c_list[0][1])))
                    endpoints.append((float(c_list[-1][0]), float(c_list[-1][1])))

            bridge_lines: list[LineString] = []
            max_bridged_gap = 0.0
            for i in range(len(endpoints)):
                p_i = endpoints[i]
                for j in range(i + 1, len(endpoints)):
                    p_j = endpoints[j]
                    dist = math.hypot(p_i[0] - p_j[0], p_i[1] - p_j[1])
                    if snap_tolerance_m < dist <= auto_close_gap_tolerance_m:
                        bridge_lines.append(LineString([p_i, p_j]))
                        max_bridged_gap = max(max_bridged_gap, dist)

            if bridge_lines:
                combined_segs = [*loose_segments_for_polygonize, *bridge_lines]
                noded_bridged = unary_union(combined_segs)
                bridged_polys = list(polygonize(noded_bridged))
                old_area = sum(p.area for p in polygonized)
                new_area = sum(p.area for p in bridged_polys)
                if new_area > old_area + 1e-3:
                    polygonized = bridged_polys
                    warnings.append(
                        f"Bridged {len(bridge_lines)} cross-segment/cross-layer corner gap(s) "
                        f"(max gap={max_bridged_gap:.3f}m <= {auto_close_gap_tolerance_m}m) -> area={new_area:.2f}m2."
                    )

        for poly in polygonized:
            polygons.extend(_clean_to_polygons(poly))

    return polygons, auto_closed_infos, warnings
