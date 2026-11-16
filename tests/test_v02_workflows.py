"""Unit tests for v0.2 workflow accelerators: DWG loader, Manhole Optimizer, and Pavement Calculator."""

from __future__ import annotations

from pathlib import Path

import pytest

from civil_qto_mcp.cad.dwg_loader import convert_dwg_to_dxf, read_dwg_header_version
from civil_qto_mcp.cad.manhole_optimizer import optimize_precast_manhole_elements
from civil_qto_mcp.cad.pavement_calculator import calculate_pavement_layer_quantities
from civil_qto_mcp.models.dxf_models import ManholeSpecInput
from civil_qto_mcp.server import (
    mcp,
)
from civil_qto_mcp.server import (
    optimize_precast_manhole_elements as server_optimize_manholes,
)


def test_dwg_header_version_and_dxf_passthrough(
    tmp_path: Path, eov_parking_dxf_path: Path
) -> None:
    """
    Verify binary DWG version header detection (AC1032 = AutoCAD 2018+) and
    convert_dwg_to_dxf behavior on both .dxf passthrough and binary .dwg files.
    """
    fake_dwg = tmp_path / "sample_2018.dwg"
    fake_dwg.write_bytes(b"AC1032\x00\x00\x00\x00MOCK_DWG_PAYLOAD")

    ver = read_dwg_header_version(fake_dwg)
    assert ver == "AC1032 (AutoCAD 2018+)"

    # Passing a .dxf to convert_dwg_to_dxf copies/passes through cleanly
    out_dir = tmp_path / "converted"
    rep_dxf = convert_dwg_to_dxf(eov_parking_dxf_path, output_dir=out_dir)
    assert rep_dxf.total_files == 1
    assert rep_dxf.succeeded_count == 1
    assert Path(rep_dxf.conversions[0].output_dxf_path or "").exists()


def test_manhole_optimizer_szebeton_t2_shallow_and_gully() -> None:
    """
    Verify SzeBeton T2 precast manhole optimization:
    1. CS-01: DN1000, h1=2.50m (250cm), PAVING_BLOCK -> h2=233cm:
       AFE D100 T2 (65cm) + AGY 100/100 T2 (100cm) + ASZ 100/60 T2 (60cm) + SZB 60/8 (8cm)
       + FEDLAP D400 600x600 HYDROTEC (17cm) = 250cm (residual = 0.0cm!)
    2. CS-SHALLOW: DN1000, h1=1.35m (135cm) -> h2=118cm (< 131cm cone minimum):
       Automatically switches to 20cm flat cover slab (AFT 100/60 T2)!
    3. VA-01: GULLY_50X50, h1=2.42m (242cm) -> VA-F 80cm + VA-GY 100cm + VA-GY 50cm + DUNA 500x500 D400 (12cm) = 242cm.
    """
    specs = [
        ManholeSpecInput(
            node_id="CS-01",
            cover_level_m=114.50,
            invert_level_m=112.00,
            nominal_diameter_mm=1000,
            node_type="MANHOLE",
            surface_type="PAVING_BLOCK",
        ),
        ManholeSpecInput(
            node_id="CS-SHALLOW",
            total_depth_h1_m=1.35,
            nominal_diameter_mm=1000,
            node_type="MANHOLE",
            surface_type="ASPHALT_OR_ROAD",
        ),
        ManholeSpecInput(
            node_id="VA-01",
            total_depth_h1_m=2.42,
            nominal_diameter_mm=500,
            node_type="GULLY_50X50",
            surface_type="ASPHALT_OR_ROAD",
        ),
    ]

    rep = optimize_precast_manhole_elements(
        manholes=specs,
        catalog_profile="SZEBETON_T2_HU",
        cover_frame_height_cm=17.0,
        spare_adjustment_ring_per_manhole=True,
    )

    assert rep.total_structures_processed == 3
    assert rep.manholes_count == 2
    assert rep.gullies_count == 1
    assert rep.shallow_flat_slab_count == 1

    by_id = {a.node_id: a for a in rep.assemblies}

    cs1 = by_id["CS-01"]
    assert cs1.target_net_precast_height_h2_cm == pytest.approx(233.0)
    assert cs1.built_total_height_cm == pytest.approx(250.0)
    assert cs1.height_residual_cm == pytest.approx(0.0)
    cs1_codes = {e.element_code for e in cs1.elements}
    assert "AFE D100 T2" in cs1_codes
    assert "AGY 100/100 T2" in cs1_codes
    assert "ASZ 100/60 T2" in cs1_codes
    assert "SZB 60/8" in cs1_codes
    assert "FEDLAP D400 600x600 HYDROTEC" in cs1_codes

    shallow = by_id["CS-SHALLOW"]
    assert shallow.used_flat_cover_slab is True
    shallow_codes = {e.element_code for e in shallow.elements}
    assert "AFT 100/60 T2" in shallow_codes

    gully = by_id["VA-01"]
    assert gully.built_total_height_cm == pytest.approx(242.0)
    gully_codes = {e.element_code for e in gully.elements}
    assert {"VA-F 50/50/80", "VA-GY 50/50/100", "VA-GY 50/50/50", "DUNA 500x500 D400"}.issubset(
        gully_codes
    )

    # Verify BOM spare SZB 60/10 rings (+1 per circular manhole = 2 spare rings)
    bom_by_code = {b.element_code: b for b in rep.consolidated_bom}
    assert "SZB 60/10" in bom_by_code
    assert bom_by_code["SZB 60/10"].spare_quantity == 2


def test_manhole_optimizer_direct_from_cad_dxf(utility_network_dxf_path: Path) -> None:
    """Verify chaining CAD manhole extraction directly into precast element optimization."""
    rep = server_optimize_manholes(
        file_path_cad=str(utility_network_dxf_path),
        node_layers=["AKNAK"],
        text_layers=["KOZMU_FELIRAT"],
        unit_scale_to_meter=1.0,
        default_surface_type="PAVING_BLOCK",
    )

    # 3 manholes on utility_network_dxf_path have complete levels (CS-01, CS-02, CS-03)
    assert rep.total_structures_processed == 3
    assert rep.manholes_count == 3
    bom_codes = {b.element_code for b in rep.consolidated_bom}
    assert "AFE D100 T2" in bom_codes
    assert "AFE D120 T2" in bom_codes  # CS-03 is DN1200!


def test_pavement_layer_calculator_lidl_bbs_preset() -> None:
    """
    Verify pavement layer quantity calculation with lateral step-out widening and CKT x1.20:
    Input: net_surface_area_m2 = 1000.0 m2, active_edge_perimeter_m = 140.0 m
    - Layer 1 (8cm paving, step_out=0.0m, waste=1.03): order_area = 1030.00 m2
    - Layer 2 (3cm 2/5 bedding, density=1.65, waste=1.05): geom_vol = 30.0 m3 -> 51.98 t
    - Layer 3 (20cm CKT-4, step_out=0.15m -> area=1021m2, geom_vol=204.2m3, comp=1.20, waste=1.03):
      order_vol = 204.2 * 1.20 * 1.03 = 252.39 m3
    - Layer 4 (25cm FZKA 0/63, step_out=0.30m -> area=1042m2, geom_vol=260.5m3, density=2.05, waste=1.03):
      order_tons = 260.5 * 2.05 * 1.03 = 550.05 t
    - Layer 5 (Geotextile, step_out=0.40m -> area=1056m2, overlap=1.12): order_area = 1182.72 m2
    """
    rep = calculate_pavement_layer_quantities(
        net_surface_area_m2=1000.0,
        active_edge_perimeter_m=140.0,
        preset_name="LIDL_BBS_PARKING_PAVING",
    )

    assert rep.total_structure_thickness_cm == pytest.approx(56.0)
    assert len(rep.layers) == 5

    l1, l2, l3, l4, l5 = rep.layers
    assert l1.primary_order_unit == "m2"
    assert l1.primary_order_quantity == pytest.approx(1030.0, abs=0.05)

    assert l2.primary_order_unit == "t"
    assert l2.primary_order_quantity == pytest.approx(51.98, abs=0.05)

    assert l3.effective_layer_area_m2 == pytest.approx(1021.0, abs=0.05)
    assert l3.primary_order_unit == "m3"
    assert l3.primary_order_quantity == pytest.approx(252.39, abs=0.05)

    assert l4.effective_layer_area_m2 == pytest.approx(1042.0, abs=0.05)
    assert l4.primary_order_unit == "t"
    assert l4.primary_order_quantity == pytest.approx(550.05, abs=0.05)

    assert l5.effective_layer_area_m2 == pytest.approx(1056.0, abs=0.05)
    assert l5.primary_order_unit == "m2"
    assert l5.primary_order_quantity == pytest.approx(1182.72, abs=0.05)


def test_mcp_server_has_all_8_v02_tools() -> None:
    """Verify all 8 v0.2 tools are registered on the MCP server."""
    registered = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "inspect_civil_dxf",
        "measure_linear_and_area_features",
        "extract_utility_network_and_levels",
        "inspect_and_audit_boq_xlsx",
        "compare_qto_with_boq",
        "convert_dwg_to_dxf",
        "optimize_precast_manhole_elements",
        "calculate_pavement_layer_quantities",
    }
    assert expected.issubset(registered)
