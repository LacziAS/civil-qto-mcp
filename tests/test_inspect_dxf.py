"""Unit tests for Phase 1: inspect_civil_dxf (Discovery, EOV CRS & Unit heuristics, CAD Hygiene)."""

from __future__ import annotations

from pathlib import Path

import pytest

from civil_qto_mcp.cad.inspector import inspect_civil_dxf


def test_inspect_eov_crs_and_insunits_mismatch(eov_parking_dxf_path: Path) -> None:
    """Verify Hungarian EOV HD72 detection and $INSUNITS mismatch warning."""
    report = inspect_civil_dxf(eov_parking_dxf_path)

    assert report.total_paperspace_entities_ignored >= 1
    assert report.crs_and_units.dxf_insunits_code == 4  # Millimeters in header
    assert report.crs_and_units.detected_crs_type == "HU_EOV_HD72"
    assert report.crs_and_units.inferred_drawing_unit == "m"
    assert report.crs_and_units.recommended_unit_scale_to_meter == pytest.approx(1.0)
    assert report.crs_and_units.confidence == "HIGH"

    warning_codes = {w.code for w in report.cad_health_warnings}
    assert "INSUNITS_HEADER_MISMATCH" in warning_codes
    assert "NEARLY_CLOSED_POLYLINES" in warning_codes
    assert "FLYING_Z_COORDINATES" in warning_codes
    assert "DUPLICATE_SEGMENTS_DETECTED" in warning_codes


def test_inspect_layers_blocks_attribs_and_texts(eov_parking_dxf_path: Path) -> None:
    """Verify per-layer entity counts, INSERT block names, ATTRIB tags, and cleaned MTEXT samples."""
    report = inspect_civil_dxf(eov_parking_dxf_path)
    layers_by_name = {lyr.layer_name: lyr for lyr in report.layers}

    assert "BURKOLAT_TERKO" in layers_by_name
    assert layers_by_name["BURKOLAT_TERKO"].closed_polyline_count == 1

    assert "ZOLDSZIGET" in layers_by_name
    zold = layers_by_name["ZOLDSZIGET"]
    assert zold.closed_polyline_count == 2
    assert zold.open_polyline_count == 1
    assert zold.nearly_closed_polyline_count == 1

    assert "CSAPADEK_AKNA" in layers_by_name
    akna_lyr = layers_by_name["CSAPADEK_AKNA"]
    assert "AKNA_D1000" in akna_lyr.distinct_block_names
    assert {"AKNA_ID", "FEDLAP", "FOLYASFENEK"}.issubset(set(akna_lyr.block_attribute_tags))
    assert any("AKNA_ID=CS-01" in s for s in akna_lyr.sample_texts)

    assert "FELIRATOK" in layers_by_name
    fel_lyr = layers_by_name["FELIRATOK"]
    assert any("Kiemelt szegély 15x25x100 | DN1000 tisztítóakna" in s for s in fel_lyr.sample_texts)


def test_inspect_layer_regex_filter(eov_parking_dxf_path: Path) -> None:
    """Verify regex filtering of layers."""
    report = inspect_civil_dxf(eov_parking_dxf_path, layer_filter_regex=r"^BURKOLAT")
    layer_names = {lyr.layer_name for lyr in report.layers}
    assert layer_names == {"BURKOLAT_TERKO", "BURKOLAT_HATCH"}
