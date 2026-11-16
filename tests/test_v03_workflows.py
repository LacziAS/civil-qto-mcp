"""
Unit and integration tests for v0.3.0 workflow tools:
- Tool 9: convert_vector_pdf_to_dxf (scale calibration from title block & reference dimensions)
- Tool 10: extract_longitudinal_profile_data (shallow C12/15 encasement, shoring, hand/machine split, DXF table band parser)
- Tool 11: apply_boq_modifications_and_pricing (markup_factor, quantity mod, K-tétel, '-'/'+' kiv. észrevétel pair, XML cache)
- Tool 12: export_qto_visual_overlay (interactive HTML + SVG generation)
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import openpyxl

from civil_qto_mcp.boq.boq_writer import apply_boq_modifications_and_pricing
from civil_qto_mcp.cad.pdf_importer import convert_vector_pdf_to_dxf
from civil_qto_mcp.cad.profile_extractor import extract_longitudinal_profile_data
from civil_qto_mcp.cad.visual_overlay import export_qto_visual_overlay
from civil_qto_mcp.server import mcp
from tests.factories.dxf_factory import create_eov_parking_lot_dxf
from tests.factories.xlsx_factory import create_boq_with_traps_xlsx


def test_pdf_importer_title_block_and_reference_scale_calibration(tmp_path: Path) -> None:
    """Verify automatic paper scale detection ('M=1:250') and reference dimension calibration."""
    dxf_file = tmp_path / "imported_plan.dxf"
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True, dxfattribs={"layer": "PDF_Road"})
    msp.add_text("Méretarány: M=1:250", dxfattribs={"layer": "PDF_Texto", "insert": (2, 1)})
    doc.saveas(str(dxf_file))

    # 1. Auto-detect 1:250 from title block text (0.0254 * 250 = 6.35)
    rep_auto = convert_vector_pdf_to_dxf(pdf_path=dxf_file, pdf_import_base_unit="inch")
    assert rep_auto.status == "SUCCESS"
    assert rep_auto.detected_scale_ratio_text == "1:250"
    assert rep_auto.calibration_method == "TITLE_BLOCK_SCALE"
    assert abs(rep_auto.recommended_scale_factor_to_meters - 6.35) < 1e-6

    # 2. Calibrate from reference dimension (e.g., 14.00 m road measured as 2.2047244 CAD units)
    rep_ref = convert_vector_pdf_to_dxf(
        pdf_path=dxf_file,
        reference_cad_length=2.2047244,
        reference_real_meters=14.0,
    )
    assert rep_ref.calibration_method == "REFERENCE_DIMENSION"
    assert abs(rep_ref.recommended_scale_factor_to_meters - 6.35) < 1e-4


def test_longitudinal_profile_shallow_concrete_shoring_and_crossings(tmp_path: Path) -> None:
    """Verify shallow cover (<1.0m) C12/15 encasement, shoring (>1.25m), and crossing hand excavation."""
    # Create a synthetic profile DXF with Terepszint, Csőfenék, Szelvény bands
    prof_dxf = tmp_path / "ud_3108_profile.dxf"
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()

    # Row headers at X=0
    msp.add_text("Terepszint [mBf]", dxfattribs={"insert": (0, 30)})
    msp.add_text("Csőfenék [mBf]", dxfattribs={"insert": (0, 20)})
    msp.add_text("Szelvény [m]", dxfattribs={"insert": (0, 10)})

    # Station 1 at X=20: 0+000.00, ground=93.85, invert=92.90 (h1=0.95m, cover=0.75m < 1.00m -> shallow!)
    msp.add_text("93,85", dxfattribs={"insert": (20, 30)})
    msp.add_text("92,90", dxfattribs={"insert": (20, 20)})
    msp.add_text("0+000.00", dxfattribs={"insert": (20, 10)})

    # Station 2 at X=50: 0+020.00, ground=94.05, invert=92.80 (h1=1.25m, cover=1.05m >= 1.00m -> end of shallow span!)
    msp.add_text("94,05", dxfattribs={"insert": (50, 30)})
    msp.add_text("92,80", dxfattribs={"insert": (50, 20)})
    msp.add_text("0+020.00", dxfattribs={"insert": (50, 10)})

    # Station 3 at X=80: 0+050.00, ground=94.20, invert=92.50 (h1=1.70m, cover=1.50m >= 1.00m -> deep, shored!)
    msp.add_text("94,20", dxfattribs={"insert": (80, 30)})
    msp.add_text("92,50", dxfattribs={"insert": (80, 20)})
    msp.add_text("0+050.00", dxfattribs={"insert": (80, 10)})

    # Add 2 utility crossings
    msp.add_text("Keresztező tűzivíz DN150", dxfattribs={"insert": (35, 45)})
    msp.add_text("Keresztező 0,4 kV kábel", dxfattribs={"insert": (65, 45)})
    doc.saveas(str(prof_dxf))

    rep = extract_longitudinal_profile_data(
        profile_name="UD-3108 Test",
        nominal_diameter_dn_mm=200,
        dxf_path=prof_dxf,
    )

    assert len(rep.stations) == 3
    assert len(rep.spans) == 2
    assert rep.total_profile_length_m == 50.0
    # Span 1 (20m) is shallow (min cover 0.75m < 1.00m) -> requires concrete encasement
    assert rep.spans[0].requires_shallow_concrete_protection is True
    assert rep.shallow_protection_length_m == 20.0
    assert rep.shallow_concrete_encase_m3 > 4.0
    # Span 2 (30m) has avg depth + bedding > 1.25m -> requires shoring
    assert rep.spans[1].shoring_area_m2 > 80.0
    # 2 crossings -> hand excavation > 0 and machine + hand == total
    assert rep.crossings_count == 2
    assert rep.hand_excavation_m3 > 0
    assert abs((rep.machine_excavation_m3 + rep.hand_excavation_m3) - rep.total_excavation_m3) < 0.05


def test_boq_writer_pricing_markup_k_items_and_kiv_eszrevetel_pairs(tmp_path: Path) -> None:
    """Verify safe BoQ pricing (+20% markup), K-tétel, and '-'/'+' kiv. észrevétel pair generation."""
    src_xlsx = create_boq_with_traps_xlsx(tmp_path / "src_boq.xlsx")
    out_xlsx = tmp_path / "out_priced_boq.xlsx"

    rep = apply_boq_modifications_and_pricing(
        source_xlsx_path=src_xlsx,
        output_xlsx_path=out_xlsx,
        markup_factor=1.20,
        round_step=10.0,
        row_pricings=[
            {
                "sheet_name": "01_Ut_es_Burkolat",
                "row_number": 6,
                "single_unit_price": 8000.0,
                "modified_quantity": 250.0,
                "justification_comment": "Módosított mennyiség a terv alapján",
            },
            {
                "sheet_name": "01_Ut_es_Burkolat",
                "row_number": 8,
                "single_unit_price": 40000.0,
            },
            {
                "sheet_name": "02_Csatorna_TERC",
                "row_number": 4,
                "material_unit_price": 4500.0,
                "labor_unit_price": 3500.0,
                "modified_quantity": 250.0,
            },
        ],
        supplemental_items=[
            {
                "sheet_name": "01_Ut_es_Burkolat",
                "item_mode": "K_TETEL",
                "item_number": "K-1",
                "code": "K-tétel",
                "description": "K-1: 15x25x100 cm kiemelt betonszegély C20/25 alapban",
                "quantity": 60.0,
                "unit": "fm",
                "material_unit_price": 8000.0,
                "labor_unit_price": 6500.0,
                "justification_note": "RD-3101 terven szereplő, kiírásból kimaradt szegély",
            },
            {
                "sheet_name": "01_Ut_es_Burkolat",
                "item_mode": "KIV_ESZREVETEL_PAIR",
                "original_row_number": 5,
                "description": "15 cm (150 mm) vtg. C25/30 szegélyalap beton",
                "quantity": 120.0,
                "unit": "m",
                "material_unit_price": 7500.0,
                "labor_unit_price": 6000.0,
                "justification_note": "Módosított betonminőség C25/30-ra",
            },
        ],
    )

    assert out_xlsx.exists()
    assert rep.priced_rows_count == 3
    assert rep.quantity_modified_rows_count == 2
    assert rep.inserted_k_items_count == 1
    assert rep.inserted_observation_pairs_count == 1
    assert rep.calc_chain_removed is True
    assert rep.cached_formula_values_updated is True

    # Open with data_only=True to confirm cached <v> values are populated on both SINGLE and SPLIT sheets
    wb_val = openpyxl.load_workbook(str(out_xlsx), data_only=True)
    ws_ut = wb_val["01_Ut_es_Burkolat"]
    # Row 6 single price 8000 * 1.20 = 9600, qty = 250 -> total = 2,400,000
    assert ws_ut.cell(6, 4).value == 250.0
    assert ws_ut.cell(6, 6).value == 9600.0
    assert ws_ut.cell(6, 7).value == 2400000.0

    ws_terc = wb_val["02_Csatorna_TERC"]
    # Row 4 material 4500 * 1.20 = 5400, labor 3500 * 1.20 = 4200, qty = 250 -> total = 2,400,000
    assert ws_terc.cell(4, 4).value == 250.0
    assert ws_terc.cell(4, 6).value == 5400.0
    assert ws_terc.cell(4, 7).value == 4200.0
    assert ws_terc.cell(4, 8).value == 2400000.0


def test_export_qto_visual_overlay_html_and_svg(tmp_path: Path) -> None:
    """Verify SVG and interactive HTML visual QTO audit overlay generation."""
    dxf_path = create_eov_parking_lot_dxf(tmp_path / "overlay_test.dxf")
    out_dir = tmp_path / "overlays"

    rep = export_qto_visual_overlay(
        file_path=dxf_path,
        unit_scale_to_meter=1.0,
        boundary_layers=["BURKOLAT_TERKO"],
        island_hole_layers=["ZOLDSZIGET"],
        linear_layers=["SZEGELY_KIEMELT"],
        point_layers=["CSAPADEK_AKNA"],
        output_dir=out_dir,
    )

    assert Path(rep.output_svg_path).exists()
    assert Path(rep.output_html_path).exists()
    assert rep.rendered_polygons_count >= 1
    assert rep.rendered_lines_count >= 1
    assert rep.rendered_points_count >= 1
    assert rep.total_net_area_m2 > 900.0
    assert rep.total_linear_length_m > 10.0


def test_mcp_server_has_all_12_v03_tools() -> None:
    """Verify all 12 tools (v0.1 + v0.2 + v0.3) are registered on the MCPServer instance."""
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
    }
    assert expected.issubset(set(tools_dict.keys()))
    assert len(tools_dict) == 12


def test_international_i18n_profiles_and_decimal_currency(tmp_path: Path) -> None:
    """
    Verify international i18n readiness:
    1. Decimal currency (EUR/USD) default round_step=0.01 (12.40 * 1.20 = 14.88 EUR, not rounded to 10!)
    2. International 'SUPPLEMENTAL_ITEM' & 'VARIATION_PAIR' modes in BoQ writer
    3. German ('Geländehöhe', 'Sohlhöhe', 'Stationierung', 'Kreuzung') profile DXF extraction
    4. English ('en') and German ('de') visual overlay HTML output
    """
    # 1. EUR/USD decimal pricing + SUPPLEMENTAL_ITEM + VARIATION_PAIR
    src_xlsx = create_boq_with_traps_xlsx(tmp_path / "eur_boq.xlsx")
    out_xlsx = tmp_path / "eur_boq_out.xlsx"
    rep_boq = apply_boq_modifications_and_pricing(
        source_xlsx_path=src_xlsx,
        output_xlsx_path=out_xlsx,
        markup_factor=1.20,  # default round_step=0.01!
        row_pricings=[
            {
                "sheet_name": "01_Ut_es_Burkolat",
                "row_number": 6,
                "single_unit_price": 12.40,  # 12.40 * 1.20 = 14.88 EUR
                "modified_quantity": 100.0,
            }
        ],
        supplemental_items=[
            {
                "sheet_name": "01_Ut_es_Burkolat",
                "item_mode": "SUPPLEMENTAL_ITEM",
                "code": "SUPP-01",
                "description": "Precast concrete road curb 15x25 cm (EN 1340)",
                "quantity": 50.0,
                "unit": "m",
                "material_unit_price": 18.50,
            },
            {
                "sheet_name": "01_Ut_es_Burkolat",
                "item_mode": "VARIATION_PAIR",
                "original_row_number": 5,
                "description": "Heavy-duty granite curb 15x30 cm",
                "quantity": 0.125708,
                "unit": "km",
                "material_unit_price": 25000.0,
            },
        ],
    )
    assert rep_boq.inserted_k_items_count == 1
    assert rep_boq.inserted_observation_pairs_count == 1
    wb_eur = openpyxl.load_workbook(str(out_xlsx), data_only=True)
    ws_eur = wb_eur["01_Ut_es_Burkolat"]
    assert ws_eur.cell(6, 6).value == 14.88
    assert ws_eur.cell(6, 7).value == 1488.0

    # 2. German profile DXF ('Geländehöhe', 'Sohlhöhe', 'Stationierung', 'Kreuzung')
    de_dxf = tmp_path / "de_profile.dxf"
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_text("Geländehöhe [m ü. NHN]", dxfattribs={"insert": (0, 30)})
    msp.add_text("Sohlhöhe [m ü. NHN]", dxfattribs={"insert": (0, 20)})
    msp.add_text("Stationierung [m]", dxfattribs={"insert": (0, 10)})
    msp.add_text("105.00", dxfattribs={"insert": (20, 30)})
    msp.add_text("103.20", dxfattribs={"insert": (20, 20)})
    msp.add_text("0+000.00", dxfattribs={"insert": (20, 10)})
    msp.add_text("105.20", dxfattribs={"insert": (45, 30)})
    msp.add_text("103.00", dxfattribs={"insert": (45, 20)})
    msp.add_text("0+025.00", dxfattribs={"insert": (45, 10)})
    msp.add_text("Kreuzung Bestand Gas DN100", dxfattribs={"insert": (30, 40)})
    doc.saveas(str(de_dxf))

    rep_de = extract_longitudinal_profile_data(dxf_path=de_dxf, nominal_diameter_dn_mm=300)
    assert len(rep_de.stations) == 2
    assert rep_de.total_profile_length_m == 25.0
    assert rep_de.crossings_count == 1

    # 3. Visual overlay in English ('en') and German ('de')
    dxf_vis = create_eov_parking_lot_dxf(tmp_path / "i18n_overlay.dxf")
    rep_en = export_qto_visual_overlay(
        file_path=dxf_vis,
        boundary_layers=["BURKOLAT_TERKO"],
        output_dir=tmp_path / "en_out",
        locale="en",
    )
    html_en = Path(rep_en.output_html_path).read_text(encoding="utf-8")
    assert "Civil QTO Visual Audit Overlay" in html_en
    assert "Measured Features Breakdown" in html_en

    rep_de_vis = export_qto_visual_overlay(
        file_path=dxf_vis,
        boundary_layers=["BURKOLAT_TERKO"],
        output_dir=tmp_path / "de_out",
        locale="de",
    )
    html_de = Path(rep_de_vis.output_html_path).read_text(encoding="utf-8")
    assert "Visueller Prüfplan" in html_de
    assert "Aufmaß-Einzelverzeichnis" in html_de

