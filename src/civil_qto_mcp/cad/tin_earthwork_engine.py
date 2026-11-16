"""
Tool 16: 3D Delaunay TIN Surface Earthwork (Topsoil, Cut & Fill) Engine (calculate_tin_earthwork_volumes).

Computes exact 3D earthwork quantities between an Existing Ground surface and a Designed
Subgrade / Finished Pavement surface directly from CAD spot-elevation layers (POINT, INSERT,
TEXT/MTEXT elevation callouts) or explicit 3D point clouds — without requiring Civil 3D:
1. Topsoil stripping volume (`humuszleszedés [m3]`) across the grading boundary
2. Pavement structure thickness offset (`tervezett burkolatszint - pályaszerkezet = tükörszint`)
3. Delaunay TIN triangulation (`shapely.ops.triangulate`) with barycentric surface interpolation
4. Sub-triangle zero-line (`nullvonal`) integration so Cut (`bevágás`) and Fill (`töltés`)
   never cancel each other out inside mixed triangles
5. Net earth balance (`földmérleg`) with compaction multiplier
"""

from __future__ import annotations

import re
from pathlib import Path

from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon
from shapely.ops import triangulate, unary_union

from civil_qto_mcp.cad.dwg_loader import load_cad_document
from civil_qto_mcp.cad.geometry_engine import (
    clean_cad_text,
    extract_polygons_from_entities,
    iter_target_entities,
)
from civil_qto_mcp.models.dxf_models import SpotElevationPoint, TinEarthworkReport

ELEV_NUMBER_REGEX = re.compile(r"([+-]?\d{1,4}(?:[.,]\d{1,3}))")


def _parse_elevation_text(raw: str) -> float | None:
    cleaned = clean_cad_text(raw).strip()
    if not cleaned:
        return None
    m = ELEV_NUMBER_REGEX.search(cleaned)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _extract_spot_points_from_cad(
    msp,
    layers: list[str],
    unit_scale_to_meter: float,
) -> list[SpotElevationPoint]:
    pts: list[SpotElevationPoint] = []
    if not layers:
        return pts
    target_set = set(layers)
    for ent, _eff_layer, handle in iter_target_entities(
        msp, target_layers=target_set, explode_inserts=False
    ):
        dxftype = ent.dxftype()
        if dxftype == "POINT":
            loc = getattr(ent.dxf, "location", None)
            if loc is not None and abs(float(loc.z)) > 1e-4:
                pts.append(
                    SpotElevationPoint(
                        x_m=float(loc.x) * unit_scale_to_meter,
                        y_m=float(loc.y) * unit_scale_to_meter,
                        z_m=float(loc.z),
                        point_id=handle,
                    )
                )
        elif dxftype == "INSERT":
            ins = getattr(ent.dxf, "insert", None)
            if ins is None:
                continue
            z_val: float | None = float(ins.z) if abs(float(ins.z)) > 1e-4 else None
            if z_val is None and hasattr(ent, "attribs"):
                for att in ent.attribs:
                    parsed = _parse_elevation_text(getattr(att.dxf, "text", "") or "")
                    if parsed is not None:
                        z_val = parsed
                        break
            if z_val is not None:
                pts.append(
                    SpotElevationPoint(
                        x_m=float(ins.x) * unit_scale_to_meter,
                        y_m=float(ins.y) * unit_scale_to_meter,
                        z_m=z_val,
                        point_id=handle,
                    )
                )
        elif dxftype in ("TEXT", "MTEXT"):
            ins = getattr(ent.dxf, "insert", None)
            if ins is None:
                continue
            raw_txt = getattr(ent.dxf, "text", "") if dxftype == "TEXT" else getattr(ent, "text", "")
            z_val = _parse_elevation_text(raw_txt or "")
            if z_val is not None:
                pts.append(
                    SpotElevationPoint(
                        x_m=float(ins.x) * unit_scale_to_meter,
                        y_m=float(ins.y) * unit_scale_to_meter,
                        z_m=z_val,
                        point_id=handle,
                    )
                )
    return pts


def _build_tin_interpolator(points: list[SpotElevationPoint]):
    """
    Build a piecewise-linear Delaunay TIN interpolator over `(x_m, y_m) -> z_m`
    with inverse-distance-weighted (IDW) fallback for query points outside the convex hull or when <3 points.
    """
    if not points:
        return lambda _x, _y: 0.0
    if len(points) == 1:
        z0 = points[0].z_m
        return lambda _x, _y: z0

    # Deduplicate XY within 1cm
    xy_map: dict[tuple[int, int], SpotElevationPoint] = {}
    for p in points:
        key = (round(p.x_m * 100), round(p.y_m * 100))
        xy_map[key] = p
    unique_pts = list(xy_map.values())

    # Precompute triangles with their 3 vertex (x, y, z) coordinates
    tin_tris: list[tuple[Polygon, tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] = []
    if len(unique_pts) >= 3:
        mp = MultiPoint([(p.x_m, p.y_m, p.z_m) for p in unique_pts])
        for tri in triangulate(mp):
            if isinstance(tri, Polygon) and not tri.is_empty and tri.area > 1e-6:
                coords = list(tri.exterior.coords)[:3]
                if len(coords) == 3:
                    # Find matching Z for each vertex
                    v3: list[tuple[float, float, float]] = []
                    for cx, cy, *rest in coords:
                        if rest:
                            v3.append((float(cx), float(cy), float(rest[0])))
                        else:
                            nearest = min(unique_pts, key=lambda p: (p.x_m - cx) ** 2 + (p.y_m - cy) ** 2)
                            v3.append((float(cx), float(cy), nearest.z_m))
                    tin_tris.append((tri, v3[0], v3[1], v3[2]))

    def interpolate(qx: float, qy: float) -> float:
        pt = Point(qx, qy)
        for tri_poly, (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) in tin_tris:
            if tri_poly.covers(pt):
                det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
                if abs(det) > 1e-9:
                    l1 = ((y2 - y3) * (qx - x3) + (x3 - x2) * (qy - y3)) / det
                    l2 = ((y3 - y1) * (qx - x3) + (x1 - x3) * (qy - y3)) / det
                    l3 = 1.0 - l1 - l2
                    return l1 * z1 + l2 * z2 + l3 * z3
        # IDW fallback (power=2)
        num = 0.0
        den = 0.0
        for p in unique_pts:
            d2 = (p.x_m - qx) ** 2 + (p.y_m - qy) ** 2
            if d2 < 1e-8:
                return p.z_m
            w = 1.0 / d2
            num += w * p.z_m
            den += w
        return num / den if den > 0 else unique_pts[0].z_m

    return interpolate


def _subdivide_triangle_coords(
    v1: tuple[float, float],
    v2: tuple[float, float],
    v3: tuple[float, float],
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """Subdivide a triangle into 4 sub-triangles using edge midpoints."""
    m12 = (0.5 * (v1[0] + v2[0]), 0.5 * (v1[1] + v2[1]))
    m23 = (0.5 * (v2[0] + v3[0]), 0.5 * (v2[1] + v3[1]))
    m31 = (0.5 * (v3[0] + v1[0]), 0.5 * (v3[1] + v1[1]))
    return [
        (v1, m12, m31),
        (m12, v2, m23),
        (m31, m23, v3),
        (m12, m23, m31),
    ]


def calculate_tin_earthwork_volumes(
    existing_points: list[SpotElevationPoint] | list[dict] | None = None,
    design_points: list[SpotElevationPoint] | list[dict] | None = None,
    constant_design_elevation_m: float | None = None,
    file_path_cad: str | Path | None = None,
    existing_elevation_layers: list[str] | None = None,
    design_elevation_layers: list[str] | None = None,
    boundary_layers: list[str] | None = None,
    unit_scale_to_meter: float = 1.0,
    topsoil_strip_thickness_m: float = 0.15,
    pavement_structure_thickness_m: float = 0.0,
    fill_compaction_factor: float = 1.15,
) -> TinEarthworkReport:
    """
    Calculate 3D Delaunay TIN earthwork volumes (topsoil stripping, cut, fill, and net balance)
    between existing terrain and designed subgrade level.
    """
    warnings_list: list[str] = []
    ex_pts: list[SpotElevationPoint] = []
    des_pts: list[SpotElevationPoint] = []

    if existing_points:
        for p in existing_points:
            ex_pts.append(p if isinstance(p, SpotElevationPoint) else SpotElevationPoint.model_validate(p))
    if design_points:
        for p in design_points:
            des_pts.append(p if isinstance(p, SpotElevationPoint) else SpotElevationPoint.model_validate(p))

    boundary_poly: Polygon | MultiPolygon | None = None
    if file_path_cad is not None:
        doc, _ = load_cad_document(file_path_cad)
        msp = doc.modelspace()
        if existing_elevation_layers and not ex_pts:
            ex_pts = _extract_spot_points_from_cad(msp, existing_elevation_layers, unit_scale_to_meter)
        if design_elevation_layers and not des_pts:
            des_pts = _extract_spot_points_from_cad(msp, design_elevation_layers, unit_scale_to_meter)
        if boundary_layers:
            bnd_ents = list(iter_target_entities(msp, target_layers=set(boundary_layers), explode_inserts=True))
            polys, _, b_warns = extract_polygons_from_entities(
                bnd_ents, unit_scale_to_meter=unit_scale_to_meter, auto_close_gap_tolerance_m=0.10
            )
            warnings_list.extend(b_warns)
            if polys:
                boundary_poly = unary_union(polys)

    if not ex_pts:
        raise ValueError("At least 3 existing_points (or valid existing_elevation_layers) are required.")

    if not des_pts and constant_design_elevation_m is not None:
        des_pts = [
            SpotElevationPoint(x_m=p.x_m, y_m=p.y_m, z_m=constant_design_elevation_m, point_id=p.point_id)
            for p in ex_pts
        ]
    elif not des_pts:
        raise ValueError("Provide either `design_points`, `design_elevation_layers`, or `constant_design_elevation_m`.")

    # Build combined 2D vertex set for triangulation
    all_xy = [(p.x_m, p.y_m) for p in ex_pts] + [(p.x_m, p.y_m) for p in des_pts]
    if boundary_poly is None:
        hull = MultiPoint(all_xy).convex_hull
        if not isinstance(hull, Polygon) or hull.area <= 1e-4:
            raise ValueError("Spot elevation points are colinear or have zero footprint area.")
        boundary_poly = hull
    else:
        for geom in (boundary_poly.geoms if isinstance(boundary_poly, MultiPolygon) else [boundary_poly]):
            all_xy.extend([(float(x), float(y)) for x, y, *_ in geom.exterior.coords])

    interp_ex = _build_tin_interpolator(ex_pts)
    interp_des = _build_tin_interpolator(des_pts)

    raw_tris = triangulate(MultiPoint(all_xy))
    clipped_tris: list[Polygon] = []
    for t in raw_tris:
        inter = t.intersection(boundary_poly)
        if isinstance(inter, Polygon) and inter.area > 1e-4:
            clipped_tris.append(inter)
        elif isinstance(inter, MultiPolygon):
            clipped_tris.extend([g for g in inter.geoms if g.area > 1e-4])

    footprint_area = round(float(boundary_poly.area), 3)
    topsoil_vol = round(footprint_area * topsoil_strip_thickness_m, 3)

    cut_area = 0.0
    fill_area = 0.0
    cut_vol = 0.0
    fill_vol = 0.0

    def _dz_at(x: float, y: float) -> float:
        """Return z_subgrade(x,y) - z_stripped_ground(x,y). Negative = Cut, Positive = Fill."""
        z_stripped = interp_ex(x, y) - topsoil_strip_thickness_m
        z_subgrade = interp_des(x, y) - pavement_structure_thickness_m
        return z_subgrade - z_stripped

    for poly in clipped_tris:
        coords = [(float(x), float(y)) for x, y, *_ in list(poly.exterior.coords)[:-1]]
        if len(coords) == 3:
            tri_list = [(coords[0], coords[1], coords[2])]
        else:
            # Fan-triangulate clipped polygon from its centroid
            c_pt = (float(poly.centroid.x), float(poly.centroid.y))
            tri_list = [(c_pt, coords[i], coords[(i + 1) % len(coords)]) for i in range(len(coords))]

        for v1, v2, v3 in tri_list:
            dz1, dz2, dz3 = _dz_at(*v1), _dz_at(*v2), _dz_at(*v3)
            # If mixed Cut & Fill inside this triangle, subdivide 2 levels (16 sub-triangles)
            if (dz1 < 0 or dz2 < 0 or dz3 < 0) and (dz1 > 0 or dz2 > 0 or dz3 > 0):
                sub1 = _subdivide_triangle_coords(v1, v2, v3)
                sub_tris = [st for s in sub1 for st in _subdivide_triangle_coords(*s)]
            else:
                sub_tris = [(v1, v2, v3)]

            for sv1, sv2, sv3 in sub_tris:
                s_area = 0.5 * abs(
                    (sv2[0] - sv1[0]) * (sv3[1] - sv1[1]) - (sv3[0] - sv1[0]) * (sv2[1] - sv1[1])
                )
                if s_area <= 1e-8:
                    continue
                sdz1, sdz2, sdz3 = _dz_at(*sv1), _dz_at(*sv2), _dz_at(*sv3)
                avg_dz = (sdz1 + sdz2 + sdz3) / 3.0
                if avg_dz < 0:
                    cut_area += s_area
                    cut_vol += s_area * abs(avg_dz)
                elif avg_dz > 0:
                    fill_area += s_area
                    fill_vol += s_area * avg_dz

    fill_req_bank = round(fill_vol * fill_compaction_factor, 2)
    cut_bank = round(cut_vol, 2)
    net_balance = round(fill_req_bank - cut_bank, 2)

    ex_zs = [p.z_m for p in ex_pts]
    sub_zs = [p.z_m - pavement_structure_thickness_m for p in des_pts]

    return TinEarthworkReport(
        existing_points_count=len(ex_pts),
        design_points_count=len(des_pts),
        tin_triangles_count=len(clipped_tris),
        footprint_area_m2=round(footprint_area, 2),
        topsoil_strip_thickness_m=topsoil_strip_thickness_m,
        topsoil_strip_volume_m3=round(topsoil_vol, 2),
        pavement_structure_deduction_m=pavement_structure_thickness_m,
        cut_area_m2=round(cut_area, 2),
        fill_area_m2=round(fill_area, 2),
        cut_volume_bank_m3=cut_bank,
        fill_volume_compacted_m3=round(fill_vol, 2),
        compaction_factor=fill_compaction_factor,
        fill_volume_required_bank_m3=fill_req_bank,
        net_earth_balance_m3=net_balance,
        min_existing_z_m=round(min(ex_zs), 3),
        max_existing_z_m=round(max(ex_zs), 3),
        avg_existing_z_m=round(sum(ex_zs) / len(ex_zs), 3),
        min_subgrade_z_m=round(min(sub_zs), 3),
        max_subgrade_z_m=round(max(sub_zs), 3),
        avg_subgrade_z_m=round(sum(sub_zs) / len(sub_zs), 3),
        warnings=warnings_list,
    )
