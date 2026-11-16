"""
Tool 12: Visual QTO Audit Overlay Exporter (export_qto_visual_overlay).

Generates an interactive standalone HTML + SVG visual verification overlay of all
measured polygons (road/pavement surfaces), deducted islands, linear features (curbs, pipes),
and point features (manholes, bollards), labeled with their exact IDs and measured
quantities (m2 / m). Supports English ('en'), German ('de'), and Hungarian ('hu') locales.
"""

from __future__ import annotations

import html
import tempfile
from pathlib import Path
from typing import Literal

from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from civil_qto_mcp.cad.dwg_loader import load_cad_document
from civil_qto_mcp.cad.geometry_engine import (
    extract_linear_segment,
    extract_polygons_from_entities,
    iter_target_entities,
)
from civil_qto_mcp.models.dxf_models import VisualOverlayReport

OVERLAY_I18N: dict[str, dict[str, str]] = {
    "en": {
        "title": "Civil QTO Visual Audit Overlay",
        "source": "Source File",
        "net_area": "Net Surface Area",
        "linear_len": "Linear Length",
        "points": "Point / Node Count",
        "table_title": "Measured Features Breakdown",
        "col_id": "ID",
        "col_type": "Category / Layer",
        "col_qty": "Quantity",
        "col_unit": "Unit",
        "type_area": "Surface (Net)",
        "type_deduct": "Deduction / Island",
        "type_line": "Linear",
    },
    "de": {
        "title": "Civil QTO Visueller Prüfplan (Aufmaß-Overlay)",
        "source": "Quelldatei",
        "net_area": "Nettofläche",
        "linear_len": "Linienlänge",
        "points": "Punkte / Schächte",
        "table_title": "Aufmaß-Einzelverzeichnis",
        "col_id": "Nr.",
        "col_type": "Kategorie / Layer",
        "col_qty": "Menge",
        "col_unit": "Einheit",
        "type_area": "Fläche (Netto)",
        "type_deduct": "Abzugsfläche",
        "type_line": "Linie",
    },
    "hu": {
        "title": "QTO Vizuális Mennyiségi Ellenőrző Térkép",
        "source": "Forrásfájl",
        "net_area": "Nettó felület",
        "linear_len": "Vonalas hossz",
        "points": "Pont / Akna / Poller",
        "table_title": "Lemért Elemek Tételes Jegyzéke",
        "col_id": "Azon.",
        "col_type": "Típus / Fólia",
        "col_qty": "Mennyiség",
        "col_unit": "Egység",
        "type_area": "Felület (Nettó)",
        "type_deduct": "Levonás",
        "type_line": "Vonal",
    },
}


def _poly_to_svg_path(poly: Polygon, tx, ty) -> str:
    parts: list[str] = []
    ext_coords = list(poly.exterior.coords)
    if len(ext_coords) >= 3:
        pts = [f"{tx(x):.2f},{ty(y):.2f}" for x, y in ext_coords]
        parts.append("M " + " L ".join(pts) + " Z")
    for interior in poly.interiors:
        int_coords = list(interior.coords)
        if len(int_coords) >= 3:
            pts = [f"{tx(x):.2f},{ty(y):.2f}" for x, y in int_coords]
            parts.append("M " + " L ".join(pts) + " Z")
    return " ".join(parts)


def export_qto_visual_overlay(
    file_path: str | Path,
    unit_scale_to_meter: float = 1.0,
    boundary_layers: list[str] | None = None,
    island_hole_layers: list[str] | None = None,
    linear_layers: list[str] | None = None,
    point_layers: list[str] | None = None,
    output_dir: str | Path | None = None,
    locale: Literal["en", "de", "hu"] = "en",
) -> VisualOverlayReport:
    """
    Export an interactive HTML + SVG visual audit map showing all measured polygons,
    deducted islands, linear features, and point nodes with their exact m2 / m quantities.
    """
    doc, path_obj = load_cad_document(file_path)
    msp = doc.modelspace()
    warnings_list: list[str] = []
    tr = OVERLAY_I18N.get(locale, OVERLAY_I18N["en"])

    if output_dir is not None:
        out_folder = Path(output_dir).resolve()
    else:
        out_folder = Path(tempfile.gettempdir()) / "civil_qto_overlays"
    out_folder.mkdir(parents=True, exist_ok=True)

    svg_path = out_folder / f"{path_obj.stem}_qto_overlay.svg"
    html_path = out_folder / f"{path_obj.stem}_qto_overlay.html"

    bnd_layers = set(boundary_layers or [])
    hole_layers = set(island_hole_layers or [])
    lin_layers = set(linear_layers or [])
    pt_layers = set(point_layers or [])

    # 1. Extract Polygons
    net_polygons: list[Polygon] = []
    hole_polygons: list[Polygon] = []
    if bnd_layers:
        bnd_ents = list(iter_target_entities(msp, target_layers=bnd_layers, explode_inserts=True))
        raw_polys, _, p_warns = extract_polygons_from_entities(
            bnd_ents,
            unit_scale_to_meter=unit_scale_to_meter,
            auto_close_gap_tolerance_m=0.10,
            include_hatches=True,
        )
        warnings_list.extend(p_warns)
        if raw_polys:
            merged_outer = unary_union(raw_polys)
            if hole_layers:
                hole_ents = list(iter_target_entities(msp, target_layers=hole_layers, explode_inserts=True))
                h_polys, _, _ = extract_polygons_from_entities(
                    hole_ents,
                    unit_scale_to_meter=unit_scale_to_meter,
                    auto_close_gap_tolerance_m=0.10,
                    include_hatches=True,
                )
                hole_polygons.extend(h_polys)
                if h_polys:
                    merged_outer = merged_outer.difference(unary_union(h_polys))
            if isinstance(merged_outer, Polygon) and not merged_outer.is_empty:
                net_polygons.append(merged_outer)
            elif isinstance(merged_outer, MultiPolygon):
                net_polygons.extend([p for p in merged_outer.geoms if not p.is_empty])

    # 2. Extract Linear segments
    lin_records: list[tuple[str, LineString, float]] = []
    if lin_layers:
        for ent, eff_layer, handle in iter_target_entities(
            msp, target_layers=lin_layers, explode_inserts=True
        ):
            seg = extract_linear_segment(
                ent, effective_layer=eff_layer, handle=handle, unit_scale_to_meter=unit_scale_to_meter
            )
            if seg is not None and not seg.geometry_2d.is_empty:
                lin_records.append((eff_layer, seg.geometry_2d, seg.straight_length_m + seg.curved_length_m))

    # 3. Extract Point / Circle / Insert nodes
    pt_records: list[tuple[str, float, float]] = []
    if pt_layers:
        for ent, eff_layer, _ in iter_target_entities(
            msp, target_layers=pt_layers, explode_inserts=False
        ):
            dxftype = ent.dxftype()
            if dxftype == "CIRCLE":
                c = ent.dxf.center
                pt_records.append((eff_layer, float(c.x) * unit_scale_to_meter, float(c.y) * unit_scale_to_meter))
            elif dxftype in ("INSERT", "POINT"):
                ins = getattr(ent.dxf, "insert", getattr(ent.dxf, "location", None))
                if ins is not None:
                    pt_records.append(
                        (eff_layer, float(ins.x) * unit_scale_to_meter, float(ins.y) * unit_scale_to_meter)
                    )

    # Compute overall bounding box in meters
    all_x: list[float] = []
    all_y: list[float] = []
    for p in net_polygons + hole_polygons:
        minx, miny, maxx, maxy = p.bounds
        all_x.extend([minx, maxx])
        all_y.extend([miny, maxy])
    for _, ls, _ in lin_records:
        minx, miny, maxx, maxy = ls.bounds
        all_x.extend([minx, maxx])
        all_y.extend([miny, maxy])
    for _, px, py in pt_records:
        all_x.append(px)
        all_y.append(py)

    if not all_x or not all_y:
        min_x, max_x, min_y, max_y = 0.0, 100.0, 0.0, 100.0
    else:
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)

    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    pad = max(span_x, span_y) * 0.06
    min_x -= pad
    max_x += pad
    min_y -= pad
    max_y += pad
    span_x = max_x - min_x
    span_y = max_y - min_y

    svg_w = 1200.0
    svg_h = max(round(svg_w * (span_y / span_x), 1), 450.0)

    def tx(x: float) -> float:
        return ((x - min_x) / span_x) * svg_w

    def ty(y: float) -> float:
        return svg_h - ((y - min_y) / span_y) * svg_h

    svg_elements: list[str] = []
    table_rows: list[str] = []

    tot_area = 0.0
    for i, poly in enumerate(net_polygons, start=1):
        area_m2 = round(poly.area, 2)
        tot_area += area_m2
        d_str = _poly_to_svg_path(poly, tx, ty)
        cx, cy = poly.representative_point().x, poly.representative_point().y
        pid = f"A-{i:02d}"
        svg_elements.append(
            f'<path id="{pid}" d="{d_str}" fill="#2563eb" fill-opacity="0.32" '
            f'stroke="#1d4ed8" stroke-width="2" fill-rule="evenodd">'
            f"<title>{pid}: {area_m2:.2f} m2</title></path>"
        )
        svg_elements.append(
            f'<text x="{tx(cx):.1f}" y="{ty(cy):.1f}" fill="#1e3a8a" font-size="12" '
            f'font-weight="bold" text-anchor="middle">{pid} ({area_m2:.1f} m²)</text>'
        )
        table_rows.append(
            f"<tr><td><b>{pid}</b></td><td>{tr['type_area']}</td><td>{area_m2:.2f}</td><td>m²</td></tr>"
        )

    for i, hpoly in enumerate(hole_polygons, start=1):
        h_area = round(hpoly.area, 2)
        d_str = _poly_to_svg_path(hpoly, tx, ty)
        hid = f"D-{i:02d}"
        svg_elements.append(
            f'<path id="{hid}" d="{d_str}" fill="#ef4444" fill-opacity="0.35" '
            f'stroke="#b91c1c" stroke-width="1.5" stroke-dasharray="4,3">'
            f"<title>{hid} ({tr['type_deduct']}): -{h_area:.2f} m2</title></path>"
        )

    tot_len = 0.0
    for i, (lyr, ls, l_m) in enumerate(lin_records, start=1):
        len_m = round(l_m, 2)
        tot_len += len_m
        pts = " ".join(f"{tx(x):.2f},{ty(y):.2f}" for x, y in ls.coords)
        lid = f"L-{i:02d}"
        mid_pt = ls.interpolate(0.5, normalized=True)
        svg_elements.append(
            f'<polyline id="{lid}" points="{pts}" fill="none" stroke="#d97706" stroke-width="2.5">'
            f"<title>{lid} ({html.escape(lyr)}): {len_m:.2f} m</title></polyline>"
        )
        svg_elements.append(
            f'<text x="{tx(mid_pt.x):.1f}" y="{ty(mid_pt.y) - 4:.1f}" fill="#92400e" '
            f'font-size="10" text-anchor="middle">{lid}: {len_m:.1f}m</text>'
        )
        table_rows.append(
            f"<tr><td><b>{lid}</b></td><td>{tr['type_line']} ({html.escape(lyr)})</td><td>{len_m:.2f}</td><td>m</td></tr>"
        )

    for i, (lyr, px, py) in enumerate(pt_records, start=1):
        nid = f"P-{i:02d}"
        svg_elements.append(
            f'<circle id="{nid}" cx="{tx(px):.2f}" cy="{ty(py):.2f}" r="5" '
            f'fill="#10b981" stroke="#065f46" stroke-width="1.5">'
            f"<title>{nid} ({html.escape(lyr)})</title></circle>"
        )

    svg_content = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w:.1f} {svg_h:.1f}" '
        f'width="100%" height="100%" style="background:#f8fafc; border:1px solid #cbd5e1; border-radius:8px;">\n'
        + "\n".join(svg_elements)
        + "\n</svg>"
    )
    svg_path.write_text(svg_content, encoding="utf-8")

    html_content = f"""<!DOCTYPE html>
<html lang="{locale}">
<head>
<meta charset="utf-8">
<title>{tr['title']} – {html.escape(path_obj.name)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 20px; background: #f1f5f9; color: #0f172a; }}
  .header {{ background: #fff; padding: 16px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 16px; }}
  .grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }}
  .card {{ background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: auto; max-height: 82vh; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ border-bottom: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; }}
  th {{ background: #f8fafc; position: sticky; top: 0; }}
</style>
</head>
<body>
  <div class="header">
    <h2 style="margin:0 0 6px 0;">civil-qto-mcp – {tr['title']}</h2>
    <div>{tr['source']}: <code>{html.escape(str(path_obj))}</code> | {tr['net_area']}: <b>{tot_area:.2f} m²</b> ({len(net_polygons)}) | {tr['linear_len']}: <b>{tot_len:.2f} m</b> ({len(lin_records)}) | {tr['points']}: <b>{len(pt_records)}</b></div>
  </div>
  <div class="grid">
    <div class="card">{svg_content}</div>
    <div class="card">
      <h3 style="margin-top:0;">{tr['table_title']}</h3>
      <table>
        <thead><tr><th>{tr['col_id']}</th><th>{tr['col_type']}</th><th>{tr['col_qty']}</th><th>{tr['col_unit']}</th></tr></thead>
        <tbody>
          {"".join(table_rows)}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
    html_path.write_text(html_content, encoding="utf-8")

    return VisualOverlayReport(
        source_dxf_path=str(path_obj),
        output_html_path=str(html_path),
        output_svg_path=str(svg_path),
        unit_scale_to_meter=unit_scale_to_meter,
        rendered_polygons_count=len(net_polygons),
        rendered_lines_count=len(lin_records),
        rendered_points_count=len(pt_records),
        total_net_area_m2=round(tot_area, 2),
        total_linear_length_m=round(tot_len, 2),
        warnings=warnings_list,
    )
