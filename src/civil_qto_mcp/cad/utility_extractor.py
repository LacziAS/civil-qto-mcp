"""
Tool 3 implementation: extract_utility_network_and_levels
Extracts manholes/chambers/gullies (INSERT blocks with ATTRIBs, CIRCLEs, small box polylines)
and utility pipe segments, performs Leader-aware and STRtree spatial text matching
(supporting HU, DE, and EN callout conventions and decimal comma normalization),
verifies hydraulic level plausibility, and calculates DIN 4124 / MSZ EN 1610 trench earthworks.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from ezdxf.entities import Insert, LWPolyline
from ezdxf.lldxf.const import DXFError
from ezdxf.math import Vec3
from ezdxf.path import make_path
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from civil_qto_mcp.cad.dwg_loader import load_cad_document
from civil_qto_mcp.cad.geometry_engine import clean_cad_text, extract_linear_segment
from civil_qto_mcp.models.dxf_models import (
    PipeSegmentInfo,
    TrenchEarthworkSummary,
    UtilityNetworkReport,
    UtilityNodeInfo,
)

# Multi-language (HU / DE / EN) regex patterns for utility callouts
COVER_LEVEL_REGEX = re.compile(
    r"(?:^|[\s|;,(])(?:F|Fedlap(?:szint)?|Terep(?:szint)?|Bf|OK|OKD|Deckel|CL|Rim|Top)\s*[:=]?\s*([+-]?\d{1,4}[.,]\d{1,3})",
    re.IGNORECASE,
)
INVERT_LEVEL_REGEX = re.compile(
    r"(?:^|[\s|;,(])(?:Ff|Fb|Fen[eé]k(?:szint)?|Foly[aá]sfen[eé]k|TK|Sohle|S|IL|Inv(?:ert)?|Bot(?:tom)?)\s*[:=]?\s*([+-]?\d{1,4}[.,]\d{1,3})",
    re.IGNORECASE,
)
DN_REGEX = re.compile(
    r"(?:DN|D|Ø|⌀|ø)\s*(\d{2,4})\b",
    re.IGNORECASE,
)
BOX_SIZE_REGEX = re.compile(r"\b(\d{2,3})\s*[x×*]\s*(\d{2,3})\b")
NODE_ID_REGEX = re.compile(
    r"\b((?:CS|AK|SZ|V|VA|MH|SCH|A|K|N)[-_.]?\d{1,4}[a-zA-Z]?)\b",
    re.IGNORECASE,
)
SLOPE_REGEX = re.compile(
    r"(?:i|s|lejt[eé]s|slope)\s*[:=]?\s*(\d{1,3}[.,]\d{1,3})\s*(%|‰)",
    re.IGNORECASE,
)
MATERIAL_REGEX = re.compile(
    r"\b(KG-?PVC|PVC(?:-U)?|PP(?:\s*Pragma)?|PE(?:-?100|-?80)?|KPE|HDPE|VB|Vasbeton|Beton|Hobas|GRP|G[öo]mbgrafit(?:os)?)\b",
    re.IGNORECASE,
)

COVER_TAG_NAMES = frozenset(
    {"F", "FEDLAP", "FEDLAPSZINT", "TEREP", "TEREPSZINT", "BF", "OK", "DECKEL", "CL", "RIM", "TOP", "Z_TOP"}
)
INVERT_TAG_NAMES = frozenset(
    {"FF", "FB", "FENEK", "FOLYASFENEK", "TK", "SOHLE", "S", "IL", "INV", "INVERT", "BOT", "Z_BOT"}
)
ID_TAG_NAMES = frozenset(
    {"ID", "AKNA", "AKNA_ID", "AKNASZAM", "NAME", "NEV", "SZAM", "NO", "NR", "MH", "SCHACHT", "NODE"}
)
DN_TAG_NAMES = frozenset({"DN", "D", "ATMERO", "DIAMETER", "MERET", "SIZE"})


def _parse_float(val_str: str) -> float | None:
    """Normalize comma/dot decimal separator and parse float."""
    if not val_str:
        return None
    cleaned = val_str.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    cleaned = re.sub(r"[^0-9.+-]", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_dn_from_string(text: str) -> int | None:
    """Extract nominal diameter in mm from string or block name."""
    if not text:
        return None
    m = DN_REGEX.search(text)
    if m:
        val = int(m.group(1))
        # Handle Hungarian D100 (=100cm = DN1000) or D120 (=120cm = DN1200) shorthand
        if val in (80, 100, 120, 150) and re.search(r"\bD(?:80|100|120|150)\b", text, re.IGNORECASE):
            return val * 10
        if 50 <= val <= 4000:
            return val
    m_box = BOX_SIZE_REGEX.search(text)
    if m_box:
        a = int(m_box.group(1))
        if 30 <= a <= 100:
            return a * 10  # e.g., 50x50 cm -> 500 mm
        if 300 <= a <= 2000:
            return a
    return None


def _apply_text_to_node(node: UtilityNodeInfo, text: str) -> bool:
    """
    Parse cover level, invert level, DN, and node ID from a cleaned text string
    and populate missing fields on UtilityNodeInfo. Returns True if any field matched.
    """
    matched_any = False

    if node.cover_level_m is None:
        m_cov = COVER_LEVEL_REGEX.search(text)
        if m_cov:
            val = _parse_float(m_cov.group(1))
            if val is not None:
                node.cover_level_m = round(val, 3)
                matched_any = True

    if node.invert_level_m is None:
        m_inv = INVERT_LEVEL_REGEX.search(text)
        if m_inv:
            val = _parse_float(m_inv.group(1))
            if val is not None:
                node.invert_level_m = round(val, 3)
                matched_any = True

    if node.nominal_diameter_mm is None:
        dn = _parse_dn_from_string(text)
        if dn is not None:
            node.nominal_diameter_mm = dn
            matched_any = True

    if node.node_id.startswith("NODE_"):
        m_id = NODE_ID_REGEX.search(text)
        if m_id:
            node.node_id = m_id.group(1).upper()
            matched_any = True

    return matched_any


def _en1610_min_trench_width_m(dn_mm: int, trench_depth_m: float) -> float:
    """
    Calculate minimum trench bottom width according to MSZ EN 1610:
    Max of (OD + 2 * working_space) and minimum width by trench depth.
    """
    od_m = (dn_mm / 1000.0) * 1.10
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

    if trench_depth_m < 1.00:
        width_by_depth = 0.60
    elif trench_depth_m <= 1.75:
        width_by_depth = 0.80
    elif trench_depth_m <= 4.00:
        width_by_depth = 0.90
    else:
        width_by_depth = 1.00

    return round(max(width_by_dn, width_by_depth), 3)


def extract_utility_network_and_levels(
    file_path: str | Path,
    unit_scale_to_meter: float = 1.0,
    node_layers: list[str] | None = None,
    pipe_layers: list[str] | None = None,
    text_layers: list[str] | None = None,
    search_radius_m: float = 8.0,
    leader_snap_tolerance_m: float = 1.5,
    node_snap_tolerance_m: float = 1.5,
    cover_thickness_deduction_m: float = 0.17,
    calculate_trench_earthworks: bool = True,
    fallback_trench_depth_m: float | None = None,
    trench_wall_angle_deg: float = 90.0,
    bedding_thickness_m: float = 0.10,
    side_cover_above_crown_m: float = 0.30,
    default_pipe_dn_mm: int = 200,
) -> UtilityNetworkReport:
    """
    Extract utility manholes/gullies and pipe segments, match cover/invert levels via
    INSERT block ATTRIBs, LEADER arrows, and STRtree spatial proximity, and compute
    DIN 4124 / MSZ EN 1610 trench earthwork volumes.
    """
    if unit_scale_to_meter <= 0:
        raise ValueError(f"unit_scale_to_meter must be positive, got {unit_scale_to_meter}")

    doc, path_obj = load_cad_document(file_path)
    msp = doc.modelspace()

    node_layer_set = set(node_layers) if node_layers else None
    pipe_layer_set = set(pipe_layers) if pipe_layers else None
    text_layer_set = set(text_layers) if text_layers else None

    nodes: list[UtilityNodeInfo] = []
    node_points: list[Point] = []
    global_anomalies: list[str] = []

    # -------------------------------------------------------------------------
    # 1. EXTRACT UTILITY NODES (INSERT with ATTRIBs, CIRCLE, small box LWPOLYLINE)
    # -------------------------------------------------------------------------
    seq_counter = 1
    for ent in msp:
        dxftype = ent.dxftype()
        layer = getattr(ent.dxf, "layer", "0") or "0"
        if node_layer_set is not None and layer not in node_layer_set:
            continue

        handle = str(getattr(ent.dxf, "handle", "UNKNOWN") or "UNKNOWN")

        if dxftype == "INSERT":
            assert isinstance(ent, Insert)
            ocs = ent.ocs()
            ins_wcs = ocs.to_wcs(Vec3(ent.dxf.insert))
            xm = float(ins_wcs.x) * unit_scale_to_meter
            ym = float(ins_wcs.y) * unit_scale_to_meter
            bname = getattr(ent.dxf, "name", "") or ""

            node = UtilityNodeInfo(
                node_id=f"NODE_{seq_counter:03d}",
                handle=handle,
                layer=layer,
                source_type="INSERT",
                block_name=bname,
                x_m=round(xm, 3),
                y_m=round(ym, 3),
                nominal_diameter_mm=_parse_dn_from_string(bname),
            )
            seq_counter += 1

            # Step 1A: Read INSERT block ATTRIBs first!
            attrib_matched = False
            for attrib in ent.attribs:
                raw_tag = clean_cad_text(getattr(attrib.dxf, "tag", "")).upper().strip()
                raw_val = clean_cad_text(getattr(attrib.dxf, "text", "")).strip()
                if not raw_tag or not raw_val:
                    continue
                node.raw_attributes[raw_tag] = raw_val

                if raw_tag in ID_TAG_NAMES:
                    node.node_id = raw_val
                    attrib_matched = True
                elif raw_tag in COVER_TAG_NAMES:
                    v = _parse_float(raw_val)
                    if v is not None:
                        node.cover_level_m = round(v, 3)
                        attrib_matched = True
                elif raw_tag in INVERT_TAG_NAMES:
                    v = _parse_float(raw_val)
                    if v is not None:
                        node.invert_level_m = round(v, 3)
                        attrib_matched = True
                elif raw_tag in DN_TAG_NAMES:
                    dn = _parse_dn_from_string(raw_val) or (
                        int(_parse_float(raw_val) or 0) if _parse_float(raw_val) else None
                    )
                    if dn and 50 <= dn <= 4000:
                        node.nominal_diameter_mm = dn
                        attrib_matched = True
                else:
                    # Fallback regex inside attribute value
                    if _apply_text_to_node(node, f"{raw_tag}: {raw_val}"):
                        attrib_matched = True

            if attrib_matched:
                node.matched_via = "BLOCK_ATTRIB"

            nodes.append(node)
            node_points.append(Point(xm, ym))

        elif dxftype == "CIRCLE":
            ocs = ent.ocs()
            c_wcs = ocs.to_wcs(Vec3(ent.dxf.center))
            r_m = float(ent.dxf.radius) * unit_scale_to_meter
            # Manholes / gullies typically have radius 0.15m <= R <= 2.50m
            if 0.15 <= r_m <= 2.50:
                xm = float(c_wcs.x) * unit_scale_to_meter
                ym = float(c_wcs.y) * unit_scale_to_meter
                # Check if an INSERT node already exists within 0.20m
                if any(math.hypot(xm - n.x_m, ym - n.y_m) < 0.20 for n in nodes):
                    continue
                diam_mm = round(r_m * 2000.0 / 50.0) * 50
                node = UtilityNodeInfo(
                    node_id=f"NODE_{seq_counter:03d}",
                    handle=handle,
                    layer=layer,
                    source_type="CIRCLE",
                    x_m=round(xm, 3),
                    y_m=round(ym, 3),
                    nominal_diameter_mm=int(diam_mm) if 300 <= diam_mm <= 3000 else None,
                )
                seq_counter += 1
                nodes.append(node)
                node_points.append(Point(xm, ym))

        elif dxftype == "LWPOLYLINE":
            assert isinstance(ent, LWPolyline)
            if not ent.closed:
                continue
            try:
                path = make_path(ent)
                coords = [
                    (float(v.x) * unit_scale_to_meter, float(v.y) * unit_scale_to_meter)
                    for v in path.flattening(distance=0.01)
                ]
            except (DXFError, ValueError, TypeError):
                continue
            if len(coords) >= 4:
                poly = Polygon(coords)
                # Gully or chamber box: 0.15 m2 <= area <= 4.0 m2
                if 0.15 <= poly.area <= 4.0:
                    c = poly.centroid
                    if any(math.hypot(c.x - n.x_m, c.y - n.y_m) < 0.25 for n in nodes):
                        continue
                    side_mm = round(math.sqrt(poly.area) * 1000.0 / 50.0) * 50
                    node = UtilityNodeInfo(
                        node_id=f"NODE_{seq_counter:03d}",
                        handle=handle,
                        layer=layer,
                        source_type="POLYLINE_BOX",
                        x_m=round(float(c.x), 3),
                        y_m=round(float(c.y), 3),
                        nominal_diameter_mm=int(side_mm),
                    )
                    seq_counter += 1
                    nodes.append(node)
                    node_points.append(Point(float(c.x), float(c.y)))

    # -------------------------------------------------------------------------
    # 2. COLLECT LEADERS AND CALLOUT TEXTS (Leader-Aware + STRtree Matching)
    # -------------------------------------------------------------------------
    # Leader lines: pairs of (endpoint_A_xy, endpoint_B_xy)
    leader_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    callout_texts: list[tuple[Point, str, str]] = []  # (point_m, cleaned_text, handle)

    for ent in msp:
        dxftype = ent.dxftype()
        layer = getattr(ent.dxf, "layer", "0") or "0"
        if text_layer_set is not None and layer not in text_layer_set:
            continue
        handle = str(getattr(ent.dxf, "handle", "UNKNOWN") or "UNKNOWN")

        if dxftype == "LEADER":
            verts = [
                (float(v[0]) * unit_scale_to_meter, float(v[1]) * unit_scale_to_meter)
                for v in ent.vertices
            ]
            if len(verts) >= 2:
                leader_segments.append((verts[0], verts[-1]))
        elif dxftype == "LINE" and (
            node_layer_set is None or layer not in node_layer_set
        ) and (pipe_layer_set is None or layer not in pipe_layer_set):
            s = ent.dxf.start
            e = ent.dxf.end
            p1 = (float(s.x) * unit_scale_to_meter, float(s.y) * unit_scale_to_meter)
            p2 = (float(e.x) * unit_scale_to_meter, float(e.y) * unit_scale_to_meter)
            dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if 0.3 <= dist <= search_radius_m * 1.5:
                leader_segments.append((p1, p2))
        elif dxftype in ("TEXT", "MTEXT"):
            raw_txt = ent.dxf.text if dxftype == "TEXT" else ent.text
            cleaned = clean_cad_text(raw_txt)
            if not cleaned:
                continue
            ins = getattr(ent.dxf, "insert", None)
            if ins is not None:
                ocs = ent.ocs()
                w = ocs.to_wcs(Vec3(ins))
                pt = Point(float(w.x) * unit_scale_to_meter, float(w.y) * unit_scale_to_meter)
                callout_texts.append((pt, cleaned, handle))

    if nodes and callout_texts:
        node_tree = STRtree(node_points)

        for txt_pt, txt_str, _txt_handle in callout_texts:
            # Check if this text is a manhole callout (has cover/invert level or node ID)
            has_node_data = bool(
                COVER_LEVEL_REGEX.search(txt_str)
                or INVERT_LEVEL_REGEX.search(txt_str)
                or NODE_ID_REGEX.search(txt_str)
            )
            if not has_node_data:
                continue

            target_node_idx: int | None = None
            via_mode: str = "SPATIAL_PROXIMITY"

            # Step 2A: Check if a LEADER or pointer line connects this text to a node
            for end_a, end_b in leader_segments:
                dist_a_to_txt = math.hypot(end_a[0] - txt_pt.x, end_a[1] - txt_pt.y)
                dist_b_to_txt = math.hypot(end_b[0] - txt_pt.x, end_b[1] - txt_pt.y)

                arrow_tip: tuple[float, float] | None = None
                if dist_b_to_txt <= leader_snap_tolerance_m:
                    arrow_tip = end_a
                elif dist_a_to_txt <= leader_snap_tolerance_m:
                    arrow_tip = end_b

                if arrow_tip is not None:
                    tip_pt = Point(arrow_tip[0], arrow_tip[1])
                    nearest_idx = int(node_tree.nearest(tip_pt))
                    if node_points[nearest_idx].distance(tip_pt) <= leader_snap_tolerance_m:
                        target_node_idx = nearest_idx
                        via_mode = "LEADER_ARROW"
                        break

            # Step 2B: Fallback to STRtree nearest node within search_radius_m
            if target_node_idx is None:
                nearest_idx = int(node_tree.nearest(txt_pt))
                if node_points[nearest_idx].distance(txt_pt) <= search_radius_m:
                    target_node_idx = nearest_idx
                    via_mode = "SPATIAL_PROXIMITY"

            if target_node_idx is not None:
                target_node = nodes[target_node_idx]
                if _apply_text_to_node(target_node, txt_str):
                    target_node.matched_texts.append(txt_str)
                    if target_node.matched_via == "NONE":
                        target_node.matched_via = via_mode  # type: ignore[assignment]

    # -------------------------------------------------------------------------
    # 3. COMPUTE MANHOLE DEPTHS & CHECK PHYSICAL PLAUSIBILITY
    # -------------------------------------------------------------------------
    nodes_with_complete_levels = 0
    diam_counter: Counter[str] = Counter()

    for node in nodes:
        dn_key = f"DN{node.nominal_diameter_mm}" if node.nominal_diameter_mm else "DN_UNKNOWN"
        diam_counter[dn_key] += 1

        if node.cover_level_m is not None and node.invert_level_m is not None:
            nodes_with_complete_levels += 1
            h1 = round(node.cover_level_m - node.invert_level_m, 3)
            node.depth_h1_m = h1
            if h1 <= 0.0:
                msg = (
                    f"Node {node.node_id} (handle={node.handle}): cover_level ({node.cover_level_m}m) "
                    f"<= invert_level ({node.invert_level_m}m) -> invalid depth h1={h1}m!"
                )
                node.anomalies.append(msg)
                global_anomalies.append(msg)
            else:
                node.net_shaft_height_h2_m = round(
                    max(0.0, h1 - cover_thickness_deduction_m), 3
                )
                if h1 < 0.40 or h1 > 10.00:
                    msg = (
                        f"Node {node.node_id} (handle={node.handle}): depth h1={h1:.2f}m is outside "
                        "normal engineering range [0.40m, 10.00m]."
                    )
                    node.anomalies.append(msg)
                    global_anomalies.append(msg)

    # -------------------------------------------------------------------------
    # 4. EXTRACT PIPE SEGMENTS & COMPUTE DIN 4124 / EN 1610 TRENCH EARTHWORKS
    # -------------------------------------------------------------------------
    pipe_segments: list[PipeSegmentInfo] = []
    length_by_dn_mat: defaultdict[str, float] = defaultdict(float)
    node_tree_for_pipes = STRtree(node_points) if node_points else None

    if pipe_layer_set:
        seg_idx = 1
        for ent in msp:
            layer = getattr(ent.dxf, "layer", "0") or "0"
            if layer not in pipe_layer_set:
                continue
            handle = str(getattr(ent.dxf, "handle", "UNKNOWN") or "UNKNOWN")
            lin_seg = extract_linear_segment(ent, layer, handle, unit_scale_to_meter)
            if lin_seg is None:
                continue

            geom = lin_seg.geometry_2d
            coords = list(geom.coords)
            start_pt = Point(coords[0])
            end_pt = Point(coords[-1])
            mid_pt = geom.interpolate(0.5, normalized=True)
            length_2d = round(float(geom.length), 3)

            start_node: UtilityNodeInfo | None = None
            end_node: UtilityNodeInfo | None = None
            if node_tree_for_pipes is not None:
                s_idx = int(node_tree_for_pipes.nearest(start_pt))
                if node_points[s_idx].distance(start_pt) <= node_snap_tolerance_m:
                    start_node = nodes[s_idx]
                e_idx = int(node_tree_for_pipes.nearest(end_pt))
                if node_points[e_idx].distance(end_pt) <= node_snap_tolerance_m:
                    end_node = nodes[e_idx]

            # Match nearby pipe texts (DN, material, slope)
            pipe_dn: int | None = _parse_dn_from_string(layer)
            pipe_mat: str | None = None
            pipe_slope: float | None = None
            matched_pipe_texts: list[str] = []
            seg_anomalies: list[str] = []

            for txt_pt, txt_str, _ in callout_texts:
                if geom.distance(txt_pt) <= search_radius_m and txt_pt.distance(mid_pt) <= search_radius_m:
                    dn_cand = _parse_dn_from_string(txt_str)
                    m_mat = MATERIAL_REGEX.search(txt_str)
                    m_slp = SLOPE_REGEX.search(txt_str)
                    if dn_cand or m_mat or m_slp:
                        matched_pipe_texts.append(txt_str)
                        if pipe_dn is None and dn_cand is not None:
                            pipe_dn = dn_cand
                        if pipe_mat is None and m_mat is not None:
                            pipe_mat = m_mat.group(1).upper()
                        if pipe_slope is None and m_slp is not None:
                            slp_val = _parse_float(m_slp.group(1))
                            if slp_val is not None:
                                pipe_slope = (
                                    round(slp_val / 10.0, 4)
                                    if m_slp.group(2) == "‰"
                                    else round(slp_val, 4)
                                )

            eff_dn = pipe_dn or default_pipe_dn_mm
            eff_mat = pipe_mat or "UNSPECIFIED"

            # Compute hydraulic slope from endpoint invert levels if both are known
            if (
                start_node is not None
                and end_node is not None
                and start_node.invert_level_m is not None
                and end_node.invert_level_m is not None
                and length_2d > 0.1
            ):
                dz = abs(start_node.invert_level_m - end_node.invert_level_m)
                calc_slope_pct = round((dz / length_2d) * 100.0, 3)
                if pipe_slope is None:
                    pipe_slope = calc_slope_pct
                if calc_slope_pct < 0.05 or calc_slope_pct > 25.0:
                    msg = (
                        f"Pipe PIPE_{seg_idx:03d}: calculated slope {calc_slope_pct:.2f}% "
                        f"between {start_node.node_id} and {end_node.node_id} is outside normal range [0.05%, 25%]."
                    )
                    seg_anomalies.append(msg)
                    global_anomalies.append(msg)

            # Determine average trench depth to invert
            known_depths = [
                n.depth_h1_m
                for n in (start_node, end_node)
                if n is not None and n.depth_h1_m is not None and n.depth_h1_m > 0
            ]
            avg_invert_depth: float | None = None
            if len(known_depths) == 2:
                avg_invert_depth = 0.5 * (known_depths[0] + known_depths[1])
            elif len(known_depths) == 1:
                avg_invert_depth = known_depths[0]
                seg_anomalies.append(
                    "SINGLE_NODE_DEPTH_USED: Only one endpoint manhole had complete level data."
                )
            elif fallback_trench_depth_m is not None and fallback_trench_depth_m > 0:
                avg_invert_depth = fallback_trench_depth_m
                seg_anomalies.append(
                    f"USED_FALLBACK_TRENCH_DEPTH: Applied fallback_trench_depth_m={fallback_trench_depth_m:.2f}m."
                )
            else:
                msg = (
                    f"Pipe PIPE_{seg_idx:03d} (handle={handle}): MISSING_INVERT_LEVEL on endpoint nodes; "
                    "2D length measured, trench volume skipped."
                )
                seg_anomalies.append("MISSING_INVERT_LEVEL")
                global_anomalies.append(msg)

            avg_trench_depth_m: float | None = None
            b_bottom: float | None = None
            exc_vol: float | None = None
            zone_vol: float | None = None
            pipe_disp_vol: float | None = None
            backfill_vol: float | None = None

            if calculate_trench_earthworks and avg_invert_depth is not None:
                h_exc = round(avg_invert_depth + bedding_thickness_m, 3)
                avg_trench_depth_m = h_exc
                b_bottom = _en1610_min_trench_width_m(eff_dn, h_exc)
                od_m = (eff_dn / 1000.0) * 1.10

                if trench_wall_angle_deg >= 89.9 or h_exc <= 1.25:
                    area_exc = b_bottom * h_exc
                else:
                    rad = math.radians(max(20.0, min(89.9, trench_wall_angle_deg)))
                    top_half_extra = h_exc / math.tan(rad)
                    area_exc = (b_bottom + top_half_extra) * h_exc

                exc_vol = round(area_exc * length_2d, 3)
                pipe_disp_vol = round((math.pi * (od_m**2) / 4.0) * length_2d, 3)
                h_zone = min(h_exc, bedding_thickness_m + od_m + side_cover_above_crown_m)
                zone_vol = round(max(0.0, b_bottom * h_zone * length_2d - pipe_disp_vol), 3)
                backfill_vol = round(max(0.0, exc_vol - zone_vol - pipe_disp_vol), 3)

            group_key = f"DN{eff_dn}_{eff_mat}"
            length_by_dn_mat[group_key] = round(length_by_dn_mat[group_key] + length_2d, 3)

            pipe_segments.append(
                PipeSegmentInfo(
                    segment_id=f"PIPE_{seg_idx:03d}",
                    handle=handle,
                    layer=layer,
                    start_node_id=start_node.node_id if start_node else None,
                    end_node_id=end_node.node_id if end_node else None,
                    length_2d_m=length_2d,
                    nominal_diameter_dn_mm=eff_dn,
                    material=eff_mat,
                    slope_percent=pipe_slope,
                    avg_trench_depth_m=avg_trench_depth_m,
                    trench_bottom_width_m=b_bottom,
                    excavation_volume_m3=exc_vol,
                    bedding_and_pipe_zone_volume_m3=zone_vol,
                    pipe_displacement_volume_m3=pipe_disp_vol,
                    main_backfill_volume_m3=backfill_vol,
                    matched_texts=matched_pipe_texts,
                    anomalies=seg_anomalies,
                )
            )
            seg_idx += 1

    earthwork_summary: TrenchEarthworkSummary | None = None
    if calculate_trench_earthworks and pipe_segments:
        earthwork_summary = TrenchEarthworkSummary(
            trench_wall_angle_deg=trench_wall_angle_deg,
            bedding_thickness_m=bedding_thickness_m,
            side_cover_above_crown_m=side_cover_above_crown_m,
            total_pipe_length_m=round(sum(p.length_2d_m for p in pipe_segments), 3),
            total_excavation_volume_m3=round(
                sum(p.excavation_volume_m3 or 0.0 for p in pipe_segments), 3
            ),
            total_bedding_and_zone_sand_gravel_m3=round(
                sum(p.bedding_and_pipe_zone_volume_m3 or 0.0 for p in pipe_segments), 3
            ),
            total_pipe_displacement_m3=round(
                sum(p.pipe_displacement_volume_m3 or 0.0 for p in pipe_segments), 3
            ),
            total_main_backfill_m3=round(
                sum(p.main_backfill_volume_m3 or 0.0 for p in pipe_segments), 3
            ),
        )

    return UtilityNetworkReport(
        file_path=str(path_obj),
        unit_scale_to_meter=unit_scale_to_meter,
        node_count=len(nodes),
        nodes_with_complete_levels=nodes_with_complete_levels,
        diameter_distribution=dict(diam_counter),
        total_network_length_m=round(sum(p.length_2d_m for p in pipe_segments), 3),
        length_by_dn_and_material=dict(length_by_dn_mat),
        nodes=nodes,
        pipe_segments=pipe_segments,
        earthwork_summary=earthwork_summary,
        anomalies=global_anomalies,
    )
