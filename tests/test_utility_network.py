"""Unit tests for Phase 3: extract_utility_network_and_levels."""

from __future__ import annotations

from pathlib import Path

import pytest

from civil_qto_mcp.cad.utility_extractor import extract_utility_network_and_levels


def test_utility_nodes_attrib_leader_and_spatial_matching(
    utility_network_dxf_path: Path,
) -> None:
    """
    Verify priority-ordered manhole extraction:
    1. CS-01 via INSERT block ATTRIBs -> F=114.50, Ff=112.00, h1=2.50m, h2=2.33m, DN1000
    2. CS-02 via LEADER arrow -> F=114.30, Ff=111.80, h1=2.50m, h2=2.33m, DN1000
    3. CS-03 via STRtree spatial proximity (German OK/S syntax) -> OK=114.10, S=111.10, h1=3.00m, h2=2.83m, DN1200
    4. Gully box (0.50x0.50m) without level callouts -> DN500, matched_via='NONE'
    """
    report = extract_utility_network_and_levels(
        file_path=utility_network_dxf_path,
        unit_scale_to_meter=1.0,
        node_layers=["AKNAK"],
        pipe_layers=["CSATORNA_GERINC"],
        text_layers=["KOZMU_FELIRAT"],
        cover_thickness_deduction_m=0.17,
        calculate_trench_earthworks=True,
        fallback_trench_depth_m=None,
    )

    assert report.node_count == 4
    assert report.nodes_with_complete_levels == 3

    nodes_by_id = {n.node_id: n for n in report.nodes}
    assert "CS-01" in nodes_by_id
    cs1 = nodes_by_id["CS-01"]
    assert cs1.matched_via == "BLOCK_ATTRIB"
    assert cs1.cover_level_m == pytest.approx(114.50)
    assert cs1.invert_level_m == pytest.approx(112.00)
    assert cs1.depth_h1_m == pytest.approx(2.50)
    assert cs1.net_shaft_height_h2_m == pytest.approx(2.33)
    assert cs1.nominal_diameter_mm == 1000

    assert "CS-02" in nodes_by_id
    cs2 = nodes_by_id["CS-02"]
    assert cs2.matched_via == "LEADER_ARROW"
    assert cs2.cover_level_m == pytest.approx(114.30)
    assert cs2.invert_level_m == pytest.approx(111.80)
    assert cs2.depth_h1_m == pytest.approx(2.50)
    assert cs2.net_shaft_height_h2_m == pytest.approx(2.33)
    assert cs2.nominal_diameter_mm == 1000

    assert "CS-03" in nodes_by_id
    cs3 = nodes_by_id["CS-03"]
    assert cs3.matched_via == "SPATIAL_PROXIMITY"
    assert cs3.cover_level_m == pytest.approx(114.10)
    assert cs3.invert_level_m == pytest.approx(111.10)
    assert cs3.depth_h1_m == pytest.approx(3.00)
    assert cs3.net_shaft_height_h2_m == pytest.approx(2.83)
    assert cs3.nominal_diameter_mm == 1200


def test_utility_pipes_din4124_en1610_and_missing_levels(
    utility_network_dxf_path: Path,
) -> None:
    """
    Verify pipe network extraction and DIN 4124 / EN 1610 trench earthwork calculation:
    - Total network length = 40 + 40 + 20 = 100.0 m
    - Pipe 1 (CS-01 -> CS-02, L=40m, KG-PVC DN300): avg invert depth = 2.50m,
      h_exc = 2.60m, EN 1610 bottom width = 0.90m, vertical excavation = 0.90 * 2.60 * 40 = 93.60 m3
    - Pipe 3 (gully branch L=20m without invert levels): flags 'MISSING_INVERT_LEVEL' when fallback=None,
      and calculates volume when fallback_trench_depth_m=1.80 is supplied.
    """
    report_no_fb = extract_utility_network_and_levels(
        file_path=utility_network_dxf_path,
        unit_scale_to_meter=1.0,
        node_layers=["AKNAK"],
        pipe_layers=["CSATORNA_GERINC"],
        text_layers=["KOZMU_FELIRAT"],
        calculate_trench_earthworks=True,
        fallback_trench_depth_m=None,
        trench_wall_angle_deg=90.0,
        bedding_thickness_m=0.10,
        side_cover_above_crown_m=0.30,
    )

    assert report_no_fb.total_network_length_m == pytest.approx(100.0, abs=0.01)
    assert len(report_no_fb.pipe_segments) == 3

    p1 = report_no_fb.pipe_segments[0]
    assert p1.start_node_id == "CS-01"
    assert p1.end_node_id == "CS-02"
    assert p1.nominal_diameter_dn_mm == 300
    assert p1.material == "KG-PVC"
    assert p1.slope_percent == pytest.approx(0.50, abs=0.01)
    assert p1.trench_bottom_width_m == pytest.approx(0.90, abs=0.01)
    assert p1.excavation_volume_m3 == pytest.approx(0.90 * 2.60 * 40.0, abs=0.05)

    p3 = report_no_fb.pipe_segments[2]
    assert p3.length_2d_m == pytest.approx(20.0, abs=0.01)
    assert "MISSING_INVERT_LEVEL" in p3.anomalies
    assert p3.excavation_volume_m3 is None

    # Now re-run with fallback_trench_depth_m=1.80 and sloped 60-degree trench walls
    report_with_fb = extract_utility_network_and_levels(
        file_path=utility_network_dxf_path,
        unit_scale_to_meter=1.0,
        node_layers=["AKNAK"],
        pipe_layers=["CSATORNA_GERINC"],
        text_layers=["KOZMU_FELIRAT"],
        calculate_trench_earthworks=True,
        fallback_trench_depth_m=1.80,
        trench_wall_angle_deg=60.0,
    )
    p3_fb = report_with_fb.pipe_segments[2]
    assert p3_fb.excavation_volume_m3 is not None
    assert p3_fb.excavation_volume_m3 > 0.0
    assert any("USED_FALLBACK_TRENCH_DEPTH" in a for a in p3_fb.anomalies)
    assert report_with_fb.earthwork_summary is not None
    assert (
        report_with_fb.earthwork_summary.total_excavation_volume_m3
        > report_no_fb.earthwork_summary.total_excavation_volume_m3  # type: ignore[union-attr]
    )
