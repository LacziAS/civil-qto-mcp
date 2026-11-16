"""
Tool 1 implementation: inspect_civil_dxf
Performs deterministic CAD discovery, layer statistics, sample text/block/attribute extraction,
3-factor CRS & drawing unit estimation (including Hungarian EOV HD72 detection), and CAD hygiene audit.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from ezdxf.entities import DXFEntity, Insert, LWPolyline, Polyline
from ezdxf.lldxf.const import DXFError
from ezdxf.math import Vec3

from civil_qto_mcp.cad.dwg_loader import load_cad_document
from civil_qto_mcp.cad.geometry_engine import check_entity_z_anomaly, clean_cad_text
from civil_qto_mcp.models.dxf_models import (
    BoundingBox2D,
    BoundingBox3D,
    CadHealthWarning,
    CrsAndUnitsEstimate,
    InspectCivilDxfReport,
    LayerInfo,
)

INSUNITS_MAP: dict[int, str] = {
    0: "Unitless",
    1: "Inches",
    2: "Feet",
    3: "Miles",
    4: "Millimeters",
    5: "Centimeters",
    6: "Meters",
    7: "Kilometers",
    8: "Microinches",
    9: "Mils",
    10: "Yards",
    11: "Angstroms",
    12: "Nanometers",
    13: "Microns",
    14: "Decimeters",
}


def _extract_entity_points_3d(entity: DXFEntity) -> list[tuple[float, float, float]]:
    """Fast extraction of representative 3D WCS points for bounding box calculation."""
    dxftype = entity.dxftype()
    pts: list[tuple[float, float, float]] = []
    try:
        if dxftype == "LINE":
            s = entity.dxf.start
            e = entity.dxf.end
            pts.append((float(s.x), float(s.y), float(s.z)))
            pts.append((float(e.x), float(e.y), float(e.z)))
        elif dxftype == "LWPOLYLINE":
            assert isinstance(entity, LWPolyline)
            ocs = entity.ocs()
            elev = float(entity.dxf.elevation)
            for x, y, *_ in entity.get_points("xy"):
                w = ocs.to_wcs(Vec3(x, y, elev))
                pts.append((float(w.x), float(w.y), float(w.z)))
        elif dxftype == "POLYLINE":
            assert isinstance(entity, Polyline)
            ocs = entity.ocs()
            for v in entity.vertices:
                loc = Vec3(v.dxf.location)
                w = ocs.to_wcs(loc) if not entity.is_3d_polyline else loc
                pts.append((float(w.x), float(w.y), float(w.z)))
        elif dxftype in ("CIRCLE", "ARC", "ELLIPSE"):
            ocs = entity.ocs()
            c = ocs.to_wcs(Vec3(entity.dxf.center))
            r = float(getattr(entity.dxf, "radius", 0.0) or 0.0)
            pts.append((float(c.x) - r, float(c.y) - r, float(c.z)))
            pts.append((float(c.x) + r, float(c.y) + r, float(c.z)))
        elif dxftype in ("INSERT", "TEXT", "MTEXT", "POINT"):
            ins = getattr(entity.dxf, "insert", None)
            if ins is not None:
                ocs = entity.ocs()
                w = ocs.to_wcs(Vec3(ins))
                pts.append((float(w.x), float(w.y), float(w.z)))
        elif dxftype == "SPLINE":
            for cp in entity.control_points:
                pts.append((float(cp[0]), float(cp[1]), float(cp[2])))
    except (DXFError, ValueError, TypeError, AttributeError, IndexError):
        return pts
    return pts


def _estimate_crs_and_units(
    insunits_code: int,
    bbox: BoundingBox3D | None,
    text_heights: list[float],
) -> tuple[CrsAndUnitsEstimate, CadHealthWarning | None]:
    """
    3-factor heuristic to determine CRS (e.g., Hungarian EOV HD72) and unit_scale_to_meter.
    """
    insunits_name = INSUNITS_MAP.get(insunits_code, f"Custom({insunits_code})")
    median_th = round(statistics.median(text_heights), 4) if text_heights else None
    reasons: list[str] = []
    mismatch_warning: CadHealthWarning | None = None

    detected_crs: str = "UNKNOWN"
    eov_conv: str | None = None
    inferred_unit: str = "unknown"
    scale_to_m: float = 1.0
    confidence: str = "MEDIUM"

    if bbox is not None:
        cx = 0.5 * (bbox.min_x + bbox.max_x)
        cy = 0.5 * (bbox.min_y + bbox.max_y)
        max_span = max(bbox.width, bbox.height)

        # Check Hungarian EOV (HD72 / EPSG:23700) in meters
        is_std_eov = (400_000.0 <= cx <= 950_000.0) and (40_000.0 <= cy <= 380_000.0)
        is_swapped_eov = (40_000.0 <= cx <= 380_000.0) and (400_000.0 <= cy <= 950_000.0)

        if (is_std_eov or is_swapped_eov) and max_span < 100_000.0:
            detected_crs = "HU_EOV_HD72"
            eov_conv = (
                "STANDARD (CAD_X = EOV_Y Easting [400k..950k], CAD_Y = EOV_X Northing [40k..380k])"
                if is_std_eov
                else "SWAPPED (CAD_X = EOV_X Northing [40k..380k], CAD_Y = EOV_Y Easting [400k..950k])"
            )
            inferred_unit = "m"
            scale_to_m = 1.0
            confidence = "HIGH"
            reasons.append(
                f"Modelspace center ({cx:.1f}, {cy:.1f}) falls inside Hungarian EOV (HD72 / EPSG:23700) metric coordinate bounds."
            )
            if median_th is not None:
                reasons.append(
                    f"Median text height is {median_th} units, consistent with metric (m) civil plan annotations."
                )
            if insunits_code not in (0, 6):
                mismatch_warning = CadHealthWarning(
                    severity="WARNING",
                    code="INSUNITS_HEADER_MISMATCH",
                    message=(
                        f"DXF $INSUNITS header is set to {insunits_code} ({insunits_name}), "
                        f"but geometry resides in Hungarian EOV metric coordinates (span={max_span:.1f}m). "
                        f"Recommend using unit_scale_to_meter=1.0 instead of header unit."
                    ),
                )
        elif cx > 100_000.0 or cy > 100_000.0:
            detected_crs = "PROJECTED_METRIC"
            if max_span > 50_000.0 and (median_th is not None and median_th >= 100.0):
                inferred_unit = "mm"
                scale_to_m = 0.001
                confidence = "MEDIUM"
                reasons.append(
                    f"Large coordinate span ({max_span:.1f}) and median text height ({median_th}) indicate millimeter (mm) scaling."
                )
            else:
                inferred_unit = "m"
                scale_to_m = 1.0
                confidence = "HIGH"
                reasons.append(
                    f"Projected coordinates ({cx:.1f}, {cy:.1f}) with site span {max_span:.1f} indicate meters (m)."
                )
        else:
            detected_crs = "LOCAL_CARTESIAN"
            # Combine $INSUNITS with median text height & site span
            if median_th is not None:
                if 0.05 <= median_th <= 5.0 and max_span <= 10_000.0:
                    inferred_unit = "m"
                    scale_to_m = 1.0
                    confidence = "HIGH"
                    reasons.append(
                        f"Local coordinates with median text height={median_th} and site span={max_span:.1f} indicate meters (m)."
                    )
                elif 5.0 < median_th <= 60.0 and insunits_code == 5:
                    inferred_unit = "cm"
                    scale_to_m = 0.01
                    confidence = "HIGH"
                    reasons.append(
                        f"$INSUNITS=Centimeters and median text height={median_th} indicate centimeters (cm)."
                    )
                elif median_th > 60.0 or (max_span > 5_000.0 and insunits_code == 4):
                    inferred_unit = "mm"
                    scale_to_m = 0.001
                    confidence = "HIGH"
                    reasons.append(
                        f"Median text height={median_th} and site span={max_span:.1f} indicate millimeters (mm)."
                    )
                elif 8.0 <= median_th <= 60.0 and max_span > 500.0:
                    inferred_unit = "cm"
                    scale_to_m = 0.01
                    confidence = "MEDIUM"
                    reasons.append(
                        f"Median text height={median_th} and span={max_span:.1f} suggest centimeters (cm)."
                    )
            if inferred_unit == "unknown":
                if insunits_code == 6:
                    inferred_unit, scale_to_m, confidence = "m", 1.0, "HIGH"
                    reasons.append("Determined from $INSUNITS=6 (Meters).")
                elif insunits_code == 5:
                    inferred_unit, scale_to_m, confidence = "cm", 0.01, "HIGH"
                    reasons.append("Determined from $INSUNITS=5 (Centimeters).")
                elif insunits_code == 4 and max_span > 1_000.0:
                    inferred_unit, scale_to_m, confidence = "mm", 0.001, "MEDIUM"
                    reasons.append("Determined from $INSUNITS=4 (Millimeters) and span > 1000.")
                else:
                    inferred_unit, scale_to_m, confidence = "m", 1.0, "LOW"
                    reasons.append(
                        "Defaulted to meters (1.0) based on local coordinate bounds; please confirm with user."
                    )

    estimate = CrsAndUnitsEstimate(
        dxf_insunits_code=insunits_code,
        dxf_insunits_name=insunits_name,
        detected_crs_type=detected_crs,  # type: ignore[arg-type]
        eov_axis_convention=eov_conv,
        median_text_height_raw=median_th,
        inferred_drawing_unit=inferred_unit,  # type: ignore[arg-type]
        recommended_unit_scale_to_meter=scale_to_m,
        confidence=confidence,  # type: ignore[arg-type]
        reasoning=reasons,
    )
    return estimate, mismatch_warning


def inspect_civil_dxf(
    file_path: str | Path,
    ignore_empty_layers: bool = True,
    layer_filter_regex: str | None = None,
    max_sample_texts: int = 5,
) -> InspectCivilDxfReport:
    """
    Inspect a civil engineering DXF or DWG drawing: list layers with entity types, block names,
    attribute tags, sample texts, bounding box, CRS/unit estimation, and CAD hygiene warnings.
    """
    doc, path_obj = load_cad_document(file_path)
    msp = doc.modelspace()

    # Count paperspace entities ignored (excluding VIEWPORT entities)
    paperspace_count = 0
    for layout in doc.layouts:
        if layout.name.lower() != "model":
            paperspace_count += sum(1 for e in layout if e.dxftype() != "VIEWPORT")

    layer_regex = re.compile(layer_filter_regex, re.IGNORECASE) if layer_filter_regex else None

    layer_entity_types: dict[str, Counter[str]] = defaultdict(Counter)
    layer_closed_poly: Counter[str] = Counter()
    layer_open_poly: Counter[str] = Counter()
    layer_nearly_closed_poly: Counter[str] = Counter()
    layer_hatches: Counter[str] = Counter()
    layer_blocks: dict[str, set[str]] = defaultdict(set)
    layer_attrib_tags: dict[str, set[str]] = defaultdict(set)
    layer_sample_texts: dict[str, list[str]] = defaultdict(list)
    layer_pts_xy: dict[str, list[tuple[float, float]]] = defaultdict(list)

    all_x: list[float] = []
    all_y: list[float] = []
    all_z: list[float] = []
    text_heights: list[float] = []

    proxy_layers: set[str] = set()
    proxy_handles: list[str] = []
    proxy_count = 0

    open_poly_candidates: list[tuple[str, str, float]] = []  # (layer, handle, raw_gap)
    line_signatures_by_layer: dict[
        str, dict[tuple[float, float, float, float], list[str]]
    ] = defaultdict(lambda: defaultdict(list))

    total_msp_entities = 0

    for ent in msp:
        total_msp_entities += 1
        dxftype = ent.dxftype()
        layer = getattr(ent.dxf, "layer", "0") or "0"
        handle = str(getattr(ent.dxf, "handle", "UNKNOWN") or "UNKNOWN")

        if layer_regex and not layer_regex.search(layer):
            continue

        layer_entity_types[layer][dxftype] += 1

        # Detect Civil 3D / Proxy objects
        if dxftype == "ACAD_PROXY_ENTITY" or dxftype.startswith(("AECC_", "AECS_", "ACA_")):
            proxy_count += 1
            proxy_layers.add(layer)
            if len(proxy_handles) < 10:
                proxy_handles.append(handle)

        # Bounding box points
        pts3d = _extract_entity_points_3d(ent)
        for px, py, pz in pts3d:
            all_x.append(px)
            all_y.append(py)
            all_z.append(pz)
            layer_pts_xy[layer].append((px, py))

        # Check polylines
        if dxftype == "LWPOLYLINE":
            assert isinstance(ent, LWPolyline)
            raw_pts = list(ent.get_points("xy"))
            if ent.closed or (
                len(raw_pts) >= 3
                and math.hypot(raw_pts[0][0] - raw_pts[-1][0], raw_pts[0][1] - raw_pts[-1][1])
                <= 1e-5
            ):
                layer_closed_poly[layer] += 1
            else:
                layer_open_poly[layer] += 1
                if len(raw_pts) >= 3:
                    gap = math.hypot(
                        raw_pts[0][0] - raw_pts[-1][0], raw_pts[0][1] - raw_pts[-1][1]
                    )
                    open_poly_candidates.append((layer, handle, gap))
        elif dxftype == "POLYLINE":
            assert isinstance(ent, Polyline)
            verts = list(ent.vertices)
            if ent.is_closed:
                layer_closed_poly[layer] += 1
            else:
                layer_open_poly[layer] += 1
                if len(verts) >= 3:
                    p0 = verts[0].dxf.location
                    p1 = verts[-1].dxf.location
                    gap = math.hypot(float(p0.x) - float(p1.x), float(p0.y) - float(p1.y))
                    open_poly_candidates.append((layer, handle, gap))
        elif dxftype == "HATCH":
            layer_hatches[layer] += 1
        elif dxftype == "INSERT":
            assert isinstance(ent, Insert)
            bname = getattr(ent.dxf, "name", "") or ""
            if bname and not bname.startswith("*"):
                layer_blocks[layer].add(bname)
            for attrib in ent.attribs:
                tag = clean_cad_text(getattr(attrib.dxf, "tag", ""))
                val = clean_cad_text(getattr(attrib.dxf, "text", ""))
                if tag:
                    layer_attrib_tags[layer].add(tag)
                if tag and val and len(layer_sample_texts[layer]) < max_sample_texts:
                    sample_entry = f"[{tag}={val}]"
                    if sample_entry not in layer_sample_texts[layer]:
                        layer_sample_texts[layer].append(sample_entry)
        elif dxftype in ("TEXT", "MTEXT"):
            raw_txt = ent.dxf.text if dxftype == "TEXT" else ent.text
            cleaned = clean_cad_text(raw_txt)
            if (
                cleaned
                and len(layer_sample_texts[layer]) < max_sample_texts
                and cleaned not in layer_sample_texts[layer]
            ):
                layer_sample_texts[layer].append(cleaned[:120])
            th_attr = "height" if dxftype == "TEXT" else "char_height"
            th = float(getattr(ent.dxf, th_attr, 0.0) or 0.0)
            if th > 0:
                text_heights.append(th)
        elif dxftype == "LINE" and len(pts3d) == 2:
            p_a = (round(pts3d[0][0], 3), round(pts3d[0][1], 3))
            p_b = (round(pts3d[1][0], 3), round(pts3d[1][1], 3))
            if p_a != p_b:
                sig = (*min(p_a, p_b), *max(p_a, p_b))
                line_signatures_by_layer[layer][sig].append(handle)

    msp_bbox: BoundingBox3D | None = None
    if all_x and all_y:
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        min_z = min(all_z) if all_z else 0.0
        max_z = max(all_z) if all_z else 0.0
        msp_bbox = BoundingBox3D(
            min_x=round(min_x, 4),
            min_y=round(min_y, 4),
            max_x=round(max_x, 4),
            max_y=round(max_y, 4),
            width=round(max_x - min_x, 4),
            height=round(max_y - min_y, 4),
            min_z=round(min_z, 4),
            max_z=round(max_z, 4),
        )

    insunits_code = int(doc.header.get("$INSUNITS", 0) or 0)
    crs_estimate, insunits_warning = _estimate_crs_and_units(
        insunits_code, msp_bbox, text_heights
    )
    scale_to_m = crs_estimate.recommended_unit_scale_to_meter

    warnings: list[CadHealthWarning] = []
    if insunits_warning is not None:
        warnings.append(insunits_warning)

    # Check unresolved XREFs
    xref_names: list[str] = []
    for blk in doc.blocks:
        if getattr(blk, "is_xref", False) or getattr(blk, "is_xref_overlay", False):
            xref_names.append(blk.name)
    if xref_names:
        warnings.append(
            CadHealthWarning(
                severity="CRITICAL",
                code="EXTERNAL_XREF_BLOCKS_PRESENT",
                message=(
                    f"Drawing references {len(xref_names)} external XREF block(s): "
                    f"{', '.join(xref_names[:5])}. Entities inside unbinded XREFs cannot be measured unless bound."
                ),
                affected_count=len(xref_names),
            )
        )

    if proxy_count > 0:
        warnings.append(
            CadHealthWarning(
                severity="CRITICAL",
                code="CIVIL3D_OR_PROXY_ENTITIES",
                message=(
                    f"Found {proxy_count} Civil 3D / ACAD_PROXY_ENTITY object(s). "
                    "Use EXPORTTOAUTOCAD in Civil 3D if native geometry is missing."
                ),
                affected_layers=sorted(proxy_layers),
                affected_count=proxy_count,
                sample_handles=proxy_handles,
            )
        )

    # Evaluate nearly closed polylines using estimated scale_to_m
    nearly_closed_handles: list[str] = []
    nearly_closed_layers: set[str] = set()
    for layer, handle, raw_gap in open_poly_candidates:
        gap_m = raw_gap * scale_to_m
        if 1e-4 < gap_m <= 0.25:
            layer_nearly_closed_poly[layer] += 1
            nearly_closed_layers.add(layer)
            if len(nearly_closed_handles) < 10:
                nearly_closed_handles.append(handle)

    if nearly_closed_handles:
        warnings.append(
            CadHealthWarning(
                severity="WARNING",
                code="NEARLY_CLOSED_POLYLINES",
                message=(
                    f"Detected {sum(layer_nearly_closed_poly.values())} open polyline(s) with start/end gap <= 0.25m. "
                    "These may be unclosed pavement or island polygons."
                ),
                affected_layers=sorted(nearly_closed_layers),
                affected_count=sum(layer_nearly_closed_poly.values()),
                sample_handles=nearly_closed_handles,
            )
        )

    # Check Flying-Z coordinates
    flying_z_layers: set[str] = set()
    flying_z_handles: list[str] = []
    flying_z_count = 0
    for ent in msp:
        layer = getattr(ent.dxf, "layer", "0") or "0"
        if layer_regex and not layer_regex.search(layer):
            continue
        handle = str(getattr(ent.dxf, "handle", "UNKNOWN") or "UNKNOWN")
        anom = check_entity_z_anomaly(ent, layer, handle, scale_to_m)
        if anom is not None:
            flying_z_count += 1
            flying_z_layers.add(layer)
            if len(flying_z_handles) < 10:
                flying_z_handles.append(handle)

    if flying_z_count > 0:
        warnings.append(
            CadHealthWarning(
                severity="WARNING",
                code="FLYING_Z_COORDINATES",
                message=(
                    f"Detected {flying_z_count} linear/polygonal entity(ies) with 3D Z-elevation jumps (> 0.02m). "
                    "2D horizontal projection (Z=0) will be enforced during measurement."
                ),
                affected_layers=sorted(flying_z_layers),
                affected_count=flying_z_count,
                sample_handles=flying_z_handles,
            )
        )

    # Check duplicate LINEs
    dup_layers: set[str] = set()
    dup_handles: list[str] = []
    dup_count = 0
    for layer, sig_map in line_signatures_by_layer.items():
        for handles in sig_map.values():
            if len(handles) > 1:
                dup_count += len(handles) - 1
                dup_layers.add(layer)
                if len(dup_handles) < 10:
                    dup_handles.extend(handles[1:])

    if dup_count > 0:
        warnings.append(
            CadHealthWarning(
                severity="WARNING",
                code="DUPLICATE_SEGMENTS_DETECTED",
                message=(
                    f"Found at least {dup_count} exact duplicate LINE segment(s) drawn on top of each other. "
                    "Topological overlap deduplication will remove them during QTO measurement."
                ),
                affected_layers=sorted(dup_layers),
                affected_count=dup_count,
                sample_handles=dup_handles[:10],
            )
        )

    # Build LayerInfo list
    all_layer_names = set(layer_entity_types.keys())
    if not ignore_empty_layers:
        for lyr in doc.layers:
            if not layer_regex or layer_regex.search(lyr.dxf.name):
                all_layer_names.add(lyr.dxf.name)

    layers_out: list[LayerInfo] = []
    for lname in sorted(all_layer_names):
        counts = dict(layer_entity_types.get(lname, {}))
        total_ent = sum(counts.values())
        if ignore_empty_layers and total_ent == 0:
            continue

        pts_xy = layer_pts_xy.get(lname, [])
        lbbox: BoundingBox2D | None = None
        if pts_xy:
            xs = [p[0] for p in pts_xy]
            ys = [p[1] for p in pts_xy]
            lx0, lx1 = min(xs), max(xs)
            ly0, ly1 = min(ys), max(ys)
            lbbox = BoundingBox2D(
                min_x=round(lx0, 3),
                min_y=round(ly0, 3),
                max_x=round(lx1, 3),
                max_y=round(ly1, 3),
                width=round(lx1 - lx0, 3),
                height=round(ly1 - ly0, 3),
            )

        layers_out.append(
            LayerInfo(
                layer_name=lname,
                total_entities=total_ent,
                entity_types=counts,
                closed_polyline_count=layer_closed_poly[lname],
                open_polyline_count=layer_open_poly[lname],
                nearly_closed_polyline_count=layer_nearly_closed_poly[lname],
                hatch_count=layer_hatches[lname],
                distinct_block_names=sorted(layer_blocks.get(lname, set()))[:15],
                block_attribute_tags=sorted(layer_attrib_tags.get(lname, set()))[:20],
                sample_texts=layer_sample_texts.get(lname, []),
                bbox_2d=lbbox,
            )
        )

    # Sort layers by entity count descending so the most relevant layers appear first
    layers_out.sort(key=lambda x: (-x.total_entities, x.layer_name))

    return InspectCivilDxfReport(
        file_path=str(path_obj),
        dxf_version=doc.dxfversion,
        total_modelspace_entities=total_msp_entities,
        total_paperspace_entities_ignored=paperspace_count,
        total_layers_returned=len(layers_out),
        modelspace_bbox=msp_bbox,
        crs_and_units=crs_estimate,
        cad_health_warnings=warnings,
        layers=layers_out,
    )
