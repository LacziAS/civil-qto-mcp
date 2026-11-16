"""
Tool 10: Longitudinal Utility Profile Analyzer & Crossing Calculator (extract_longitudinal_profile_data).

Extracts or processes longitudinal profile (hossz-szelvény) station tables to compute:
- Segment-by-segment trench excavation, bedding/pipe-zone sand, pipe displacement, and backfill (DIN 4124 / EN 1610).
- Automatic shallow-cover concrete encasement (`C12/15 védő körbebetonozás`) where crown cover < 1.00 m.
- Two-sided trench shoring area (`dúcolás [m2]`) where trench depth >= 1.25 m.
- Machine vs. hand excavation split (`gépi vs. kézi földkiemelés [m3]`) based on crossed utilities.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import ezdxf

from civil_qto_mcp.cad.geometry_engine import clean_cad_text
from civil_qto_mcp.models.dxf_models import (
    LongitudinalProfileReport,
    ProfileCrossingInput,
    ProfileSpanAnalysis,
    ProfileStationComputed,
    ProfileStationInput,
)

CHAINAGE_REGEX = re.compile(r"^(\d+)\+(\d+(?:[.,]\d+)?)$")
NUMBER_REGEX = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")


def _parse_float_hu(text: str) -> float | None:
    s = text.strip().replace(" ", "").replace(",", ".")
    m_ch = CHAINAGE_REGEX.match(s)
    if m_ch:
        return float(m_ch.group(1)) * 1000.0 + float(m_ch.group(2))
    if NUMBER_REGEX.match(s):
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _extract_stations_from_profile_dxf(
    dxf_path: Path,
) -> tuple[list[ProfileStationInput], list[ProfileCrossingInput], list[str]]:
    """
    Parse a longitudinal profile DXF table band where row labels ('Terepszint', 'Csőfenék', 'Szelvény')
    define Y-bands and station values are aligned vertically along X columns.
    """
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    warnings_list: list[str] = []

    text_records: list[tuple[float, float, str]] = []
    for ent in msp:
        dxftype = ent.dxftype()
        if dxftype == "TEXT":
            raw = getattr(ent.dxf, "text", "") or ""
            ins = getattr(ent.dxf, "insert", None)
        elif dxftype == "MTEXT":
            raw = getattr(ent, "text", "") or ""
            ins = getattr(ent.dxf, "insert", None)
        else:
            continue
        if ins is None:
            continue
        cleaned = clean_cad_text(raw).strip()
        if cleaned:
            text_records.append((float(ins.x), float(ins.y), cleaned))

    y_ground: float | None = None
    y_invert: float | None = None
    y_station: float | None = None
    crossings: list[ProfileCrossingInput] = []

    for x, y, txt in text_records:
        low = txt.lower()
        if y_ground is None and any(
            k in low
            for k in (
                "ground level",
                "existing grade",
                "finished grade",
                "rim elevation",
                "geländehöhe",
                "gelaendehoehe",
                "deckelhöhe",
                "terepszint",
                "burkolatszint",
                "terep kóta",
            )
        ):
            y_ground = y
        elif y_invert is None and any(
            k in low
            for k in (
                "invert level",
                "invert elevation",
                "flow line",
                "pipe bottom",
                "sohlhöhe",
                "sohlhoehe",
                "rohrsohle",
                "csőfenék",
                "csofenek",
                "folyásfenék",
                "folyasfenek",
            )
        ):
            y_invert = y
        elif y_station is None and any(
            k in low
            for k in (
                "station",
                "chainage",
                "stationierung",
                "szelvény",
                "szelveny",
            )
        ):
            y_station = y
        elif any(
            k in low
            for k in (
                "crossing",
                "existing utility",
                "existing cable",
                "existing pipe",
                "kreuzung",
                "bestand",
                "keresztez",
                "meglévő kábel",
                "meglevo kabel",
                "meglévő cső",
                "védőcső",
            )
        ):
            crossings.append(ProfileCrossingInput(crossed_utility_name=txt[:60]))

    if y_ground is None or y_invert is None or y_station is None:
        warnings_list.append(
            "Could not auto-detect all 3 profile band headers ('Terepszint', 'Csőfenék', 'Szelvény') in DXF."
        )
        return [], crossings, warnings_list

    band_tol = max(abs(y_ground - y_invert) * 0.35, 1.5)

    def collect_band(target_y: float) -> list[tuple[float, float, str]]:
        pts: list[tuple[float, float, str]] = []
        for x, y, txt in text_records:
            if abs(y - target_y) <= band_tol:
                val = _parse_float_hu(txt)
                if val is not None:
                    pts.append((x, val, txt))
        pts.sort(key=lambda item: item[0])
        return pts

    st_pts = collect_band(y_station)
    gr_pts = collect_band(y_ground)
    inv_pts = collect_band(y_invert)

    x_tol = 3.0
    stations_out: list[ProfileStationInput] = []
    for sx, sval, sraw in st_pts:
        g_matches = [ p for p in gr_pts if abs(p[0] - sx) <= x_tol ]
        i_matches = [ p for p in inv_pts if abs(p[0] - sx) <= x_tol ]
        if g_matches and i_matches:
            g_val = min(g_matches, key=lambda p: abs(p[0] - sx))[1]
            i_val = min(i_matches, key=lambda p: abs(p[0] - sx))[1]
            stations_out.append(
                ProfileStationInput(
                    station_m=sval,
                    station_label=sraw,
                    ground_level_m=g_val,
                    invert_level_m=i_val,
                )
            )

    stations_out.sort(key=lambda s: s.station_m)
    return stations_out, crossings, warnings_list


def extract_longitudinal_profile_data(
    profile_name: str = "Utility Longitudinal Profile",
    nominal_diameter_dn_mm: int = 200,
    stations: list[ProfileStationInput] | list[dict] | None = None,
    crossings: list[ProfileCrossingInput] | list[dict] | None = None,
    dxf_path: str | Path | None = None,
    trench_width_override_m: float | None = None,
    bedding_thickness_m: float = 0.15,
    side_cover_above_crown_m: float = 0.30,
    min_cover_threshold_m: float = 1.00,
    concrete_encase_thickness_m: float = 0.15,
    shoring_depth_threshold_m: float = 1.25,
) -> LongitudinalProfileReport:
    """
    Analyze a utility longitudinal profile (from explicit station points or a profile DXF)
    to calculate trench excavation, machine vs. hand excavation at utility crossings,
    two-sided shoring area, and shallow-cover C12/15 concrete encasement volume.
    """
    warnings_list: list[str] = []
    parsed_stations: list[ProfileStationInput] = []
    parsed_crossings: list[ProfileCrossingInput] = []

    if stations:
        for s in stations:
            parsed_stations.append(
                s if isinstance(s, ProfileStationInput) else ProfileStationInput.model_validate(s)
            )
    if crossings:
        for c in crossings:
            parsed_crossings.append(
                c if isinstance(c, ProfileCrossingInput) else ProfileCrossingInput.model_validate(c)
            )

    if dxf_path is not None:
        path_obj = Path(dxf_path).resolve()
        if path_obj.exists():
            dxf_stations, dxf_crossings, dxf_warns = _extract_stations_from_profile_dxf(path_obj)
            warnings_list.extend(dxf_warns)
            if not parsed_stations:
                parsed_stations = dxf_stations
            if not parsed_crossings:
                parsed_crossings = dxf_crossings
        else:
            warnings_list.append(f"DXF file not found: {path_obj}")

    parsed_stations.sort(key=lambda s: s.station_m)

    # Outer diameter estimation (e.g. DN200 -> 0.20m, DN160 -> 0.16m)
    od_m = round(nominal_diameter_dn_mm / 1000.0, 4)

    computed_stations: list[ProfileStationComputed] = []
    for st in parsed_stations:
        h1 = round(st.ground_level_m - st.invert_level_m, 4)
        cover = round(h1 - od_m, 4)
        label = st.station_label or f"0+{st.station_m:06.2f}"
        computed_stations.append(
            ProfileStationComputed(
                station_m=round(st.station_m, 3),
                station_label=label,
                node_id=st.node_id,
                ground_level_m=round(st.ground_level_m, 3),
                invert_level_m=round(st.invert_level_m, 3),
                depth_to_invert_h1_m=round(h1, 3),
                crown_cover_depth_m=round(cover, 3),
            )
        )

    # Determine DIN 4124 / EN 1610 trench width
    avg_overall_h1 = (
        sum(s.depth_to_invert_h1_m for s in computed_stations) / len(computed_stations)
        if computed_stations
        else 1.50
    )
    if trench_width_override_m is not None and trench_width_override_m > 0:
        b_width = round(trench_width_override_m, 3)
    else:
        min_by_dn = od_m + 0.60
        min_by_depth = 0.90 if avg_overall_h1 > 1.75 else 0.80
        b_width = round(max(min_by_dn, min_by_depth), 3)

    spans: list[ProfileSpanAnalysis] = []
    tot_length = 0.0
    tot_exc = 0.0
    tot_zone = 0.0
    tot_pipe = 0.0
    tot_backfill = 0.0
    tot_shoring = 0.0
    shallow_len = 0.0
    shallow_encase_m3 = 0.0

    pipe_area_m2 = math.pi * ((od_m / 2.0) ** 2)
    encased_box_side = od_m + 2.0 * concrete_encase_thickness_m
    encase_cross_section_m2 = max((encased_box_side**2) - pipe_area_m2, 0.0)

    for idx in range(len(computed_stations) - 1):
        s0 = computed_stations[idx]
        s1 = computed_stations[idx + 1]
        seg_len = round(s1.station_m - s0.station_m, 3)
        if seg_len <= 0:
            continue

        avg_h1 = (s0.depth_to_invert_h1_m + s1.depth_to_invert_h1_m) / 2.0
        min_cov = min(s0.crown_cover_depth_m, s1.crown_cover_depth_m)
        slope_pm = round(((s0.invert_level_m - s1.invert_level_m) / seg_len) * 1000.0, 2)

        total_trench_depth = avg_h1 + bedding_thickness_m
        exc_v = round(b_width * total_trench_depth * seg_len, 3)
        pipe_v = round(pipe_area_m2 * seg_len, 3)
        zone_height = min(total_trench_depth, bedding_thickness_m + od_m + side_cover_above_crown_m)
        zone_v = round(max(b_width * zone_height * seg_len - pipe_v, 0.0), 3)
        backfill_v = round(max(exc_v - zone_v - pipe_v, 0.0), 3)

        shoring_v = (
            round(2.0 * total_trench_depth * seg_len, 2)
            if total_trench_depth >= shoring_depth_threshold_m
            else 0.0
        )

        is_shallow = min_cov < min_cover_threshold_m
        encase_v = round(encase_cross_section_m2 * seg_len, 3) if is_shallow else 0.0
        if is_shallow:
            shallow_len += seg_len
            shallow_encase_m3 += encase_v
            warnings_list.append(
                f"Span {idx + 1} ({s0.station_label}..{s1.station_label}, L={seg_len:.2f}m) has "
                f"minimum cover {min_cov:.2f}m < {min_cover_threshold_m:.2f}m: requires {encase_v:.2f} m3 "
                f"C12/15 concrete encasement."
            )

        spans.append(
            ProfileSpanAnalysis(
                span_index=idx + 1,
                start_station_m=s0.station_m,
                end_station_m=s1.station_m,
                start_node_id=s0.node_id,
                end_node_id=s1.node_id,
                length_m=seg_len,
                dn_mm=nominal_diameter_dn_mm,
                outer_diameter_m=od_m,
                slope_permille=slope_pm,
                avg_depth_to_invert_m=round(avg_h1, 3),
                min_crown_cover_m=round(min_cov, 3),
                trench_width_m=b_width,
                total_excavation_m3=exc_v,
                bedding_and_pipe_zone_m3=zone_v,
                pipe_displacement_m3=pipe_v,
                backfill_m3=backfill_v,
                shoring_area_m2=shoring_v,
                requires_shallow_concrete_protection=is_shallow,
                shallow_concrete_encase_m3=encase_v,
            )
        )

        tot_length += seg_len
        tot_exc += exc_v
        tot_zone += zone_v
        tot_pipe += pipe_v
        tot_backfill += backfill_v
        tot_shoring += shoring_v

    # Calculate hand excavation at utility crossings
    avg_total_depth = (avg_overall_h1 + bedding_thickness_m) if computed_stations else 1.50
    hand_exc = 0.0
    for cr in parsed_crossings:
        zone_len = 2.0 * cr.protection_half_width_m
        hand_exc += zone_len * b_width * avg_total_depth

    hand_exc = round(min(hand_exc, tot_exc), 2)
    machine_exc = round(max(tot_exc - hand_exc, 0.0), 2)
    excess_spoil = round(tot_zone + tot_pipe + shallow_encase_m3, 2)

    return LongitudinalProfileReport(
        profile_name=profile_name,
        nominal_diameter_dn_mm=nominal_diameter_dn_mm,
        outer_diameter_m=od_m,
        trench_width_m=b_width,
        total_profile_length_m=round(tot_length, 2),
        min_cover_threshold_m=min_cover_threshold_m,
        shoring_depth_threshold_m=shoring_depth_threshold_m,
        stations=computed_stations,
        spans=spans,
        crossings_count=len(parsed_crossings),
        total_excavation_m3=round(tot_exc, 2),
        machine_excavation_m3=machine_exc,
        hand_excavation_m3=hand_exc,
        total_bedding_and_zone_m3=round(tot_zone, 2),
        total_pipe_displacement_m3=round(tot_pipe, 2),
        total_backfill_m3=round(tot_backfill, 2),
        total_excess_spoil_m3=excess_spoil,
        total_shoring_area_m2=round(tot_shoring, 2),
        shallow_protection_length_m=round(shallow_len, 2),
        shallow_concrete_encase_m3=round(shallow_encase_m3, 2),
        warnings=warnings_list,
    )
