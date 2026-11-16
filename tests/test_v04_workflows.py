"""
Unit and integration tests for v0.4.0 workflow tools:
- Tool 13: audit_technological_completeness (6 technological completeness chains & K-tétel generation)
- Tool 14: calculate_foundation_and_slab_qto (blinding overhang, formwork, DIN 4124 prismoidal pit, XPS, slab joints)
- Tool 15: match_prices_and_generate_rfq (regional routing, transport sensitivity, supplier RFQ packages)
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from civil_qto_mcp.boq.completeness_auditor import audit_technological_completeness
from civil_qto_mcp.boq.price_and_rfq_engine import (
    match_prices_and_generate_rfq,
    resolve_project_region,
)
from civil_qto_mcp.cad.foundation_calculator import calculate_foundation_and_slab_qto
from civil_qto_mcp.server import mcp
from tests.factories.xlsx_factory import create_boq_with_traps_xlsx


def test_completeness_auditor_missing_curbs_covers_tack_coat_and_joints(tmp_path: Path) -> None:
    """Verify that Tool 13 detects missing tack coat, tie-in joint, curb, manhole covers, shallow concrete, and slab joints."""
    boq_path = tmp_path / "incomplete_project_boq.xlsx"
    wb = openpyxl.Workbook()

    # Sheet 1: Road & Industrial Floor (missing tack coat, tie-in joint, curb, and slab contraction joints)
    ws1 = wb.active
    assert ws1 is not None
    ws1.title = "IX_Utepites"
    ws1.append(["Sorszám", "Kód", "Megnevezés", "Mennyiség", "M.e.", "Egységár", "Összesen"])
    ws1.append([1, "3.1", "AC 16 kopó F kopóréteg építése 5 cm vastagságban", 36.0, "m3", 150000.0, "=D2*F2"])
    ws1.append([2, "3.2", "AC 22 kötő F kötőréteg építése 7 cm vastagságban", 51.0, "m3", 140000.0, "=D3*F3"])
    ws1.append([3, "3.3", "6 cm vastag beton térkő burkolat", 120.0, "m2", 9500.0, "=D4*F4"])
    ws1.append([4, "3.4", "20 cm vtg. C30/37 ipari padló készítése", 600.0, "m2", 18000.0, "=D5*F5"])

    # Sheet 2: Utilities (15 manholes total: 9 beton akna + 6 TeraPlast vizsgálóakna, but only 9 aknafedlap -> 6 missing!)
    ws2 = wb.create_sheet("VIII_Kozmu")
    ws2.append(["Sorszám", "Kód", "Megnevezés", "Mennyiség", "M.e.", "Egységár", "Összesen"])
    ws2.append([1, "4.1", "KG-PVC SN8 D200 csatornacső", 55.0, "m", 10000.0, "=D2*F2"])
    ws2.append([2, "4.2", "Előregyártott Ø1000 beton akna", 9.0, "db", 280000.0, "=D3*F3"])
    ws2.append([3, "4.3", "TeraPlast Vizsgálóakna D200/160", 6.0, "db", 55000.0, "=D4*F4"])
    ws2.append([4, "4.4", "Hydrotec Ø600 ÖV D400 aknafedlap", 9.0, "db", 60000.0, "=D5*F5"])
    ws2.append([5, "1.1", "PE100 SDR11 PN16 vízcső D32", 131.0, "m", 3200.0, "=D6*F6"])

    # Sheet 3: Foundations (missing blinding concrete)
    ws3 = wb.create_sheet("IV_Alapozas")
    ws3.append(["Sorszám", "Kód", "Megnevezés", "Mennyiség", "M.e.", "Egységár", "Összesen"])
    ws3.append([1, "2.1", "Alaplemez beton C30/37", 280.0, "m3", 65000.0, "=D2*F2"])

    wb.save(str(boq_path))

    rep = audit_technological_completeness(
        boq_xlsx_path=boq_path,
        cad_curb_length_m=94.0,
        cad_asphalt_tie_in_length_m=99.0,
        cad_shallow_pipe_concrete_m3=12.5,
        locale="hu",
    )

    rule_ids = {f.rule_id for f in rep.findings}
    assert "ASPH-01-TACK-COAT" in rule_ids
    assert "ASPH-02-TIE-IN-JOINT" in rule_ids
    assert "PAV-01-MISSING-CURB" in rule_ids
    assert "SEW-01-MISSING-MANHOLE-COVERS" in rule_ids
    assert "SEW-02-SHALLOW-CONCRETE-ENCASEMENT" in rule_ids
    assert "WAT-01-PRESSURE-TEST" in rule_ids
    assert "FND-01-BLINDING-CONCRETE" in rule_ids
    assert "SLB-01-CONTRACTION-JOINTS" in rule_ids

    # Verify the missing manhole cover count is exactly 15 - 9 = 6 db!
    cov_finding = next(f for f in rep.findings if f.rule_id == "SEW-01-MISSING-MANHOLE-COVERS")
    assert cov_finding.recommended_quantity == pytest.approx(6.0)
    assert len(rep.ready_supplemental_items) == len(rep.findings)


def test_foundation_and_slab_qto_blinding_formwork_and_prismoidal_pit() -> None:
    """
    Verify Tool 14 structural & earthwork calculations:
    - 4x CUP_FOOTING (2.0m x 2.0m x 1.0m, cup_void = 0.15 m3):
      * Structural concrete = 4 * (4.0 * 1.0 - 0.15) = 15.40 m3
      * Blinding area (+10cm overhang per side -> 2.2m x 2.2m = 4.84 m2 * 4 = 19.36 m2)
      * Blinding volume (0.10m thick) = 1.936 m3
      * Formwork area = 4 * (8.0m * 1.0m) = 32.00 m2
    - 1x PLINTH_WALL (L=100.0m, W=0.30m, H=1.00m, xps_insulation_sides=1):
      * XPS area = 100.0 m2
    - 1x INDUSTRIAL_FLOOR (L=30.0m, W=20.0m -> 600 m2, H=0.20m, grid=6.0m):
      * Saw-cut contraction joints = 600 * (2 / 6.0) = 200.0 m
      * Perimeter isolation strip = 2 * (30 + 20) = 100.0 m
    """
    rep = calculate_foundation_and_slab_qto(
        elements=[
            {
                "element_id": "KA-1..4",
                "element_type": "CUP_FOOTING",
                "count": 4,
                "length_m": 2.0,
                "width_m": 2.0,
                "height_m": 1.0,
                "cup_void_volume_m3": 0.15,
                "include_blinding": True,
                "include_excavation": True,
            },
            {
                "element_id": "LF-1",
                "element_type": "PLINTH_WALL",
                "count": 1,
                "length_m": 100.0,
                "width_m": 0.30,
                "height_m": 1.0,
                "include_blinding": False,
                "include_excavation": False,
                "xps_insulation_sides": 1,
            },
            {
                "element_id": "IP-1",
                "element_type": "INDUSTRIAL_FLOOR",
                "count": 1,
                "length_m": 30.0,
                "width_m": 20.0,
                "height_m": 0.20,
                "include_blinding": False,
                "include_excavation": False,
                "joint_grid_spacing_m": 6.0,
            },
        ],
        blinding_thickness_m=0.10,
        blinding_overhang_m=0.10,
        working_space_m=0.50,
        batter_angle_deg=60.0,
    )

    assert rep.elements_processed_count == 3
    b_cup, b_plinth, b_floor = rep.breakdowns

    assert b_cup.structural_concrete_m3 == pytest.approx(15.40, abs=0.01)
    assert b_cup.blinding_area_m2 == pytest.approx(19.36, abs=0.01)
    assert b_cup.blinding_concrete_m3 == pytest.approx(1.936, abs=0.01)
    assert b_cup.formwork_area_m2 == pytest.approx(32.00, abs=0.01)
    assert b_cup.excavation_volume_m3 > 50.0
    assert b_cup.backfill_volume_m3 == pytest.approx(
        b_cup.excavation_volume_m3 - b_cup.displaced_spoil_m3, abs=0.02
    )

    assert b_plinth.xps_insulation_area_m2 == pytest.approx(100.0, abs=0.01)
    assert b_floor.saw_cut_joints_m == pytest.approx(200.0, abs=0.01)
    assert b_floor.perimeter_isolation_strip_m == pytest.approx(100.0, abs=0.01)


def test_price_matcher_and_rfq_packager_regional_routing(tmp_path: Path) -> None:
    """Verify regional location routing, unit-price matching, transport-sensitivity, and RFQ packaging."""
    assert resolve_project_region("Foktő") == "Bács-Kiskun (Kecskemét térsége)"
    assert resolve_project_region("Vecsés") == "Budapest / Pest megye"
    assert resolve_project_region("Tótkomlós") == "Tiszántúl / Dél-Alföld"

    boq_path = create_boq_with_traps_xlsx(tmp_path / "rfq_test_boq.xlsx")
    rep = match_prices_and_generate_rfq(
        boq_xlsx_path=boq_path,
        project_location="Foktő",
        markup_factor=1.20,
        round_step=10.0,
    )

    assert rep.resolved_region == "Bács-Kiskun (Kecskemét térsége)"
    assert rep.matched_rows_count >= 5
    assert rep.transport_sensitive_rows_count >= 3
    assert rep.estimated_grand_total_net > 1_000_000.0
    assert len(rep.ready_row_pricings) == rep.matched_rows_count

    pkg_codes = {p.package_code for p in rep.rfq_packages}
    assert "PAVING_AND_CURBS" in pkg_codes
    assert "READYMIX_AND_CKT" in pkg_codes
    assert "UTILITY_PIPES_AND_MANHOLES" in pkg_codes


def test_mcp_server_has_all_15_v04_tools() -> None:
    """Verify all 15 tools (v0.1 + v0.2 + v0.3 + v0.4) are registered on the MCPServer instance."""
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
    }
    assert expected.issubset(set(tools_dict.keys()))
    assert len(tools_dict) == 15
