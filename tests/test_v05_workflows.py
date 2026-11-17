"""
Unit and integration tests for v0.5.0 workflow tools:
- Tool 16: calculate_tin_earthwork_volumes (3D Delaunay TIN topsoil, subgrade offset, Cut/Fill, and zero-line integration)
- Tool 17: compare_drawing_and_boq_revisions (spatial CAD polygon diff, linear/point diff, and BoQ revision diff)
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import openpyxl
import pytest

from civil_qto_mcp.cad.revision_comparator import compare_drawing_and_boq_revisions
from civil_qto_mcp.cad.tin_earthwork_engine import calculate_tin_earthwork_volumes
from civil_qto_mcp.server import mcp


def test_tin_earthwork_pure_cut_and_pavement_subgrade_offset(tmp_path: Path) -> None:
    """
    Verify Tool 16 on a 20m x 10m (200 m2) grading zone extracted from a CAD DXF:
    - Existing ground elevation texts on 'MEGLEVO_TEREP': Z = 100.00 m
    - Topsoil strip thickness: 0.10 m -> Topsoil volume = 200 * 0.10 = 20.0 m3, stripped ground = 99.90 m
    - Finished pavement elevation texts on 'TERVEZETT_BURKOLAT': Z = 100.00 m
    - Pavement structure thickness: 0.60 m -> Subgrade mirror level = 100.00 - 0.60 = 99.40 m
    - Net Cut below stripped ground = 99.90 - 99.40 = 0.50 m -> Cut volume = 200 * 0.50 = 100.0 m3, Fill = 0.0 m3!
    """
    cad_file = tmp_path / "earthwork_site.dxf"
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (20, 0), (20, 10), (0, 10)], close=True, dxfattribs={"layer": "TEREPRENDEZES_HATAR"}
    )
    for x, y in [(0, 0), (20, 0), (20, 10), (0, 10), (10, 5)]:
        msp.add_text("100,00", dxfattribs={"layer": "MEGLEVO_TEREP", "insert": (x, y)})
        msp.add_text("100,00", dxfattribs={"layer": "TERVEZETT_BURKOLAT", "insert": (x, y)})
    doc.saveas(str(cad_file))

    rep = calculate_tin_earthwork_volumes(
        file_path_cad=cad_file,
        existing_elevation_layers=["MEGLEVO_TEREP"],
        design_elevation_layers=["TERVEZETT_BURKOLAT"],
        boundary_layers=["TEREPRENDEZES_HATAR"],
        topsoil_strip_thickness_m=0.10,
        pavement_structure_thickness_m=0.60,
        fill_compaction_factor=1.15,
    )

    assert rep.footprint_area_m2 == pytest.approx(200.0, abs=0.1)
    assert rep.topsoil_strip_volume_m3 == pytest.approx(20.0, abs=0.1)
    assert rep.cut_volume_bank_m3 == pytest.approx(100.0, abs=0.5)
    assert rep.fill_volume_compacted_m3 == pytest.approx(0.0, abs=0.1)
    assert rep.net_earth_balance_m3 == pytest.approx(-100.0, abs=0.5)


def test_tin_earthwork_mixed_cut_and_fill_zero_line_subdivision() -> None:
    """
    Verify that when a site slopes from Z_ex=101.0m (West, X=0) to Z_ex=99.0m (East, X=20)
    and is graded to a horizontal plane Z_des=100.0m (with 0 topsoil & 0 pavement offset),
    sub-triangle zero-line ('nullvonal') integration finds BOTH Cut > 0 and Fill > 0!
    """
    ex_pts = [
        {"x_m": 0.0, "y_m": 0.0, "z_m": 101.0},
        {"x_m": 0.0, "y_m": 10.0, "z_m": 101.0},
        {"x_m": 20.0, "y_m": 0.0, "z_m": 99.0},
        {"x_m": 20.0, "y_m": 10.0, "z_m": 99.0},
    ]
    rep = calculate_tin_earthwork_volumes(
        existing_points=ex_pts,
        constant_design_elevation_m=100.0,
        topsoil_strip_thickness_m=0.0,
        pavement_structure_thickness_m=0.0,
        fill_compaction_factor=1.0,
    )

    assert rep.footprint_area_m2 == pytest.approx(200.0, abs=0.1)
    # Analytical half-wedge volume = 0.5 * 100 m2 * 1.0 m = 50.0 m3 Cut and 50.0 m3 Fill
    assert rep.cut_volume_bank_m3 == pytest.approx(50.0, abs=2.0)
    assert rep.fill_volume_compacted_m3 == pytest.approx(50.0, abs=2.0)


def test_revision_comparator_cad_spatial_diff_and_boq_diff(tmp_path: Path) -> None:
    """
    Verify Tool 17 on:
    1. Old CAD (10x10m square at X=0..10) vs. New CAD (10x10m square shifted to X=5..15):
       - Both have 100 m2 total area (net_delta = 0.0), BUT spatially_added_area_m2 = 50.0 m2 and spatially_removed_area_m2 = 50.0 m2!
       - Curb line grows from 20.0 m to 35.0 m (+15.0 m).
    2. Old BoQ vs. New BoQ:
       - Item 1.1 quantity changed from 100 to 135
       - Item 1.2 added in R1
    """
    old_dxf = tmp_path / "plan_r0.dxf"
    doc0 = ezdxf.new("R2018")
    msp0 = doc0.modelspace()
    msp0.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "BURKOLAT"})
    msp0.add_line((0, 0), (20, 0), dxfattribs={"layer": "SZEGELY"})
    doc0.saveas(str(old_dxf))

    new_dxf = tmp_path / "plan_r1.dxf"
    doc1 = ezdxf.new("R2018")
    msp1 = doc1.modelspace()
    msp1.add_lwpolyline([(5, 0), (15, 0), (15, 10), (5, 10)], close=True, dxfattribs={"layer": "BURKOLAT"})
    msp1.add_line((0, 0), (35, 0), dxfattribs={"layer": "SZEGELY"})
    doc1.saveas(str(new_dxf))

    old_xlsx = tmp_path / "boq_r0.xlsx"
    wb0 = openpyxl.Workbook()
    ws0 = wb0.active
    assert ws0 is not None
    ws0.title = "Utepites"
    ws0.append(["Tételszám", "Kód", "Megnevezés", "Mennyiség", "M.e.", "Egységár", "Összesen"])
    ws0.append(["1.1", "K-01", "Kiemelt betonszegély", 20.0, "fm", 15000.0, 300000.0])
    wb0.save(str(old_xlsx))

    new_xlsx = tmp_path / "boq_r1.xlsx"
    wb1 = openpyxl.Workbook()
    ws1 = wb1.active
    assert ws1 is not None
    ws1.title = "Utepites"
    ws1.append(["Tételszám", "Kód", "Megnevezés", "Mennyiség", "M.e.", "Egységár", "Összesen"])
    ws1.append(["1.1", "K-01", "Kiemelt betonszegély", 35.0, "fm", 15000.0, 525000.0])
    ws1.append(["1.2", "K-02", "Térkő burkolat 8 cm", 100.0, "m2", 10000.0, 1000000.0])
    wb1.save(str(new_xlsx))

    rep = compare_drawing_and_boq_revisions(
        old_cad_path=old_dxf,
        new_cad_path=new_dxf,
        boundary_layers=["BURKOLAT"],
        linear_layers=["SZEGELY"],
        old_boq_xlsx_path=old_xlsx,
        new_boq_xlsx_path=new_xlsx,
        locale="hu",
    )

    area_diff = next(d for d in rep.cad_layer_diffs if d.layer_name == "BURKOLAT")
    assert area_diff.old_quantity == pytest.approx(100.0)
    assert area_diff.new_quantity == pytest.approx(100.0)
    assert area_diff.net_delta == pytest.approx(0.0)
    assert area_diff.spatially_added_area_m2 == pytest.approx(50.0, abs=0.1)
    assert area_diff.spatially_removed_area_m2 == pytest.approx(50.0, abs=0.1)

    lin_diff = next(d for d in rep.cad_layer_diffs if d.layer_name == "SZEGELY")
    assert lin_diff.net_delta == pytest.approx(15.0, abs=0.1)

    assert rep.boq_added_count == 1
    assert rep.boq_modified_count == 1
    assert rep.boq_net_total_delta == pytest.approx(1_225_000.0)
    assert len(rep.ready_variation_items) == 2


def test_mcp_server_has_all_17_v05_tools() -> None:
    """Verify all 17 tools (v0.1..v0.5) are registered on the MCPServer instance."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    assert tool_manager is not None
    tools_dict = tool_manager._tools
    expected = {
        "inspect_civil_dxf",
        "measure_linear_and_area_features",
        "extract_utility_network_and_levels",
        "inspect_and_audit_boq_xlsx",
        "compare_qto_with_boq",
        "convert_dwg_to_dxf",
        "optimize_precast_manhole_elements",
        "calculate_pavement_layer_quantities",
        "convert_vector_pdf_to_dxf",
        "extract_longitudinal_profile_data",
        "apply_boq_modifications_and_pricing",
        "export_qto_visual_overlay",
        "audit_technological_completeness",
        "calculate_foundation_and_slab_qto",
        "match_prices_and_generate_rfq",
        "calculate_tin_earthwork_volumes",
        "compare_drawing_and_boq_revisions",
    }
    assert expected.issubset(set(tools_dict.keys()))
    assert len(tools_dict) >= 17
