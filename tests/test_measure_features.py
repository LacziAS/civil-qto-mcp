"""Unit tests for Phase 2: measure_linear_and_area_features (Geometry Engine & QTO Measurer)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from civil_qto_mcp.cad.qto_measurer import measure_linear_and_area_features


def test_linear_measurement_overlap_dedup_and_flying_z(eov_parking_dxf_path: Path) -> None:
    """
    Verify linear measurement on 'SZEGELY_KIEMELT':
    - 100m straight line + two 20m exact duplicates + one 25m partial overlap -> 100m net
    - 10m horizontal line with Z=114.5m jump -> 10m 2D length + ElevationAnomaly logged
    - 90-deg R=10m arc -> 5*pi = 15.708m curved length
    Total Gross = 190.708m, Deducted Overlap = 65.000m, Net Length = 125.708m.
    """
    report = measure_linear_and_area_features(
        file_path=eov_parking_dxf_path,
        unit_scale_to_meter=1.0,
        linear_layers=["SZEGELY_KIEMELT"],
        deduplicate_linear_overlaps=True,
        snap_tolerance_m=0.01,
    )

    assert report.linear_summary is not None
    lin = report.linear_summary
    assert lin.straight_gross_length_m == pytest.approx(175.0, abs=0.01)
    assert lin.curved_gross_length_m == pytest.approx(5.0 * math.pi, abs=0.01)
    assert lin.gross_length_m == pytest.approx(175.0 + 5.0 * math.pi, abs=0.02)
    assert lin.deducted_overlap_m == pytest.approx(65.0, abs=0.02)
    assert lin.net_length_m == pytest.approx(110.0 + 5.0 * math.pi, abs=0.02)

    # Verify Flying-Z anomaly was caught and 2D projection prevented 114.5m distortion
    assert len(report.elevation_anomalies) == 1
    anom = report.elevation_anomalies[0]
    assert anom.layer == "SZEGELY_KIEMELT"
    assert anom.delta_z_m == pytest.approx(114.50, abs=0.01)


def test_area_measurement_bulges_auto_close_and_islands(eov_parking_dxf_path: Path) -> None:
    """
    Verify area measurement on 'BURKOLAT_TERKO' minus 'ZOLDSZIGET':
    - Outer 50x20m rounded rect (R=5m bulges): Gross Area = 900 + 25*pi = 978.540 m2
    - Outer Perimeter = 100 + 10*pi = 131.416 m
    - Islands: 40.0 m2 (closed) + 30.0 m2 (auto-closed 4cm gap) + 0.36 m2 (small chamber) = 70.360 m2
    - Net Area (min_island_deduction_m2=0.0) = 908.180 m2
    """
    report = measure_linear_and_area_features(
        file_path=eov_parking_dxf_path,
        unit_scale_to_meter=1.0,
        boundary_layers=["BURKOLAT_TERKO"],
        island_hole_layers=["ZOLDSZIGET"],
        min_island_deduction_m2=0.0,
        auto_close_gap_tolerance_m=0.10,
    )

    assert report.area_summary is not None
    area = report.area_summary
    expected_gross = 900.0 + 25.0 * math.pi  # 978.5398 m2
    expected_perim = 100.0 + 10.0 * math.pi  # 131.4159 m

    assert area.gross_area_m2 == pytest.approx(expected_gross, abs=0.05)
    assert area.outer_perimeter_m == pytest.approx(expected_perim, abs=0.05)
    assert area.deducted_islands_area_m2 == pytest.approx(70.360, abs=0.02)
    assert area.net_area_m2 == pytest.approx(expected_gross - 70.360, abs=0.05)

    # Verify the 4cm gap island was automatically closed and audited
    assert len(area.auto_closed_polygons) == 1
    ac = area.auto_closed_polygons[0]
    assert ac.layer == "ZOLDSZIGET"
    assert ac.gap_distance_m == pytest.approx(0.04, abs=0.002)
    assert ac.resulting_area_m2 == pytest.approx(30.0, abs=0.02)


def test_area_vob_c_minimum_island_deduction_threshold(eov_parking_dxf_path: Path) -> None:
    """
    Verify VOB/C (DIN 18318) threshold: when min_island_deduction_m2=0.50,
    the 0.36 m2 chamber island is NOT deducted from the paving area.
    """
    report = measure_linear_and_area_features(
        file_path=eov_parking_dxf_path,
        unit_scale_to_meter=1.0,
        boundary_layers=["BURKOLAT_TERKO"],
        island_hole_layers=["ZOLDSZIGET"],
        min_island_deduction_m2=0.50,
        auto_close_gap_tolerance_m=0.10,
    )

    assert report.area_summary is not None
    area = report.area_summary
    expected_gross = 900.0 + 25.0 * math.pi

    assert area.island_polygon_count_deducted == 2
    assert area.island_polygon_count_skipped_below_threshold == 1
    assert area.skipped_small_islands_area_m2 == pytest.approx(0.360, abs=0.01)
    assert area.deducted_islands_area_m2 == pytest.approx(70.000, abs=0.02)
    assert area.net_area_m2 == pytest.approx(expected_gross - 70.000, abs=0.05)


def test_hatch_hole_double_deduction_immunity(eov_parking_dxf_path: Path) -> None:
    """
    Verify that when a HATCH on 'BURKOLAT_HATCH' (200 m2 outer with 12 m2 internal hole)
    is measured together with 'ZOLDSZIGET_HATCH' (which has the exact same 12 m2 island),
    Shapely boolean difference does NOT double-deduct the 12 m2 island!
    Net area must be 188.0 m2, not 176.0 m2.
    """
    report = measure_linear_and_area_features(
        file_path=eov_parking_dxf_path,
        unit_scale_to_meter=1.0,
        boundary_layers=["BURKOLAT_HATCH"],
        island_hole_layers=["ZOLDSZIGET_HATCH"],
    )

    assert report.area_summary is not None
    area = report.area_summary
    assert area.gross_area_m2 == pytest.approx(200.0, abs=0.01)
    assert area.deducted_islands_area_m2 == pytest.approx(12.0, abs=0.01)
    assert area.net_area_m2 == pytest.approx(188.0, abs=0.01)


def test_exploded_lines_polygonization(eov_parking_dxf_path: Path) -> None:
    """Verify that 4 separate exploded LINE entities on 'EXPLODED_BOX' form an 80.0 m2 polygon."""
    report = measure_linear_and_area_features(
        file_path=eov_parking_dxf_path,
        unit_scale_to_meter=1.0,
        boundary_layers=["EXPLODED_BOX"],
    )

    assert report.area_summary is not None
    assert report.area_summary.net_area_m2 == pytest.approx(80.0, abs=0.01)


def test_self_intersecting_bowtie_polygon_repair(eov_parking_dxf_path: Path) -> None:
    """
    Verify that a self-intersecting figure-8 ('bowtie') polygon on 'BOWTIE_LAYER'
    is automatically repaired by shapely.make_valid() into two valid triangles (25 + 25 = 50.0 m2).
    """
    report = measure_linear_and_area_features(
        file_path=eov_parking_dxf_path,
        unit_scale_to_meter=1.0,
        boundary_layers=["BOWTIE_LAYER"],
    )

    assert report.area_summary is not None
    assert report.area_summary.net_area_m2 == pytest.approx(50.0, abs=0.01)


def test_mirrored_ocs_and_scaled_insert_block(eov_parking_dxf_path: Path) -> None:
    """
    Verify:
    1. Mirrored OCS LWPOLYLINE (extrusion Z=-1) of 10m x 5m measures 50.0 m2 area and 30.0 m length.
    2. Millimeter block (2000x1000) inserted with scale=0.001 measures 2.0 m2 area and 6.0 m length.
    """
    mir_report = measure_linear_and_area_features(
        file_path=eov_parking_dxf_path,
        unit_scale_to_meter=1.0,
        linear_layers=["MIRRORED_OCS"],
        boundary_layers=["MIRRORED_OCS"],
    )
    assert mir_report.linear_summary is not None
    assert mir_report.linear_summary.net_length_m == pytest.approx(30.0, abs=0.01)
    assert mir_report.area_summary is not None
    assert mir_report.area_summary.net_area_m2 == pytest.approx(50.0, abs=0.01)

    ins_report = measure_linear_and_area_features(
        file_path=eov_parking_dxf_path,
        unit_scale_to_meter=1.0,
        linear_layers=["SCALED_INSERT_LAYER"],
        boundary_layers=["SCALED_INSERT_LAYER"],
        explode_inserts=True,
    )
    assert ins_report.linear_summary is not None
    assert ins_report.linear_summary.net_length_m == pytest.approx(6.0, abs=0.01)
    assert ins_report.area_summary is not None
    assert ins_report.area_summary.net_area_m2 == pytest.approx(2.0, abs=0.01)


