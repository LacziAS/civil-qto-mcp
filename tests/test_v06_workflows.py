"""
Unit and integration tests for v0.6.0 workflow tools:
- Tool 18: calculate_trench_earthwork_and_bedding (MSZ EN 1610 / DIN 4124 depth-banded utility trench
  excavation, machine/hand split, lower bedding, pipe-zone cover, pipe & manhole displacement,
  native backfill, excess spoil haul-off, two-sided shoring, warning tape, and geotextile wrap)
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import openpyxl
import pytest

from civil_qto_mcp.boq.completeness_auditor import audit_technological_completeness
from civil_qto_mcp.boq.progress_billing_generator import generate_progress_certificate
from civil_qto_mcp.boq.supplier_bid_tabulator import compare_supplier_quotes_and_build_bid_tab
from civil_qto_mcp.cad.cross_section_calculator import calculate_cross_section_and_embankment_qto
from civil_qto_mcp.cad.inspector import inspect_civil_dxf
from civil_qto_mcp.cad.qto_measurer import measure_linear_and_area_features
from civil_qto_mcp.cad.trench_earthwork_calculator import calculate_trench_earthwork_and_bedding
from civil_qto_mcp.cad.utility_extractor import extract_utility_network_and_levels
from civil_qto_mcp.models.boq_models import (
    BidTabTargetItemInput,
    ProgressItemEntryInput,
    SupplierQuoteInput,
    SupplierQuoteLineInput,
)
from civil_qto_mcp.models.dxf_models import (
    RoadCrossSectionSegmentInput,
    TrenchManholePitInput,
    TrenchPipeSegmentInput,
)
from civil_qto_mcp.server import mcp
from tests.factories.dxf_factory import create_utility_network_dxf


def test_trench_earthwork_vertical_shored_depth_bands_and_displacement() -> None:
    """
    Verify Tool 18 on a 50m KG-PVC DN200 storm drain span + 2 DN1000 manholes:
    - Depth to invert: 1.90m constant, lower bedding: 0.10m -> Total trench depth = 2.00m
    - OD for KG-PVC DN200 = 0.208m; EN 1610 bottom width for 2.00m depth = 0.90m
    - Total pipe trench excavation = 50.0 * 0.90 * 2.00 = 90.0 m3
      * 0-1.50m band: 50.0 * 0.90 * 1.50 = 67.5 m3
      * 1.50-3.00m band: 50.0 * 0.90 * 0.50 = 22.5 m3
    - Shoring (>1.25m): 2 * 2.00m * 50.0m = 200.0 m2 for pipe trench
    - Mass conservation: total_excavation == total_main_backfill + total_excess_spoil_haul_off
    """
    rep = calculate_trench_earthwork_and_bedding(
        pipe_segments=[
            TrenchPipeSegmentInput(
                segment_id="CS-01..CS-02",
                utility_type="STORM_DRAIN",
                pipe_material="KG-PVC",
                nominal_diameter_dn_mm=200,
                length_m=50.0,
                start_depth_to_invert_m=1.90,
                end_depth_to_invert_m=1.90,
                lower_bedding_thickness_m=0.10,
                protective_cover_above_crown_m=0.30,
                trench_wall_mode="VERTICAL_SHORED",
                crossings_count=1,
                wrap_pipe_zone_in_geotextile=True,
            )
        ],
        manhole_pits=[
            TrenchManholePitInput(
                node_id="CS-01",
                nominal_diameter_mm=1000,
                depth_to_invert_m=1.90,
                base_slab_thickness_m=0.20,
                under_base_bedding_m=0.15,
                working_space_m=0.50,
            ),
            TrenchManholePitInput(
                node_id="CS-02",
                nominal_diameter_mm=1000,
                depth_to_invert_m=1.90,
                base_slab_thickness_m=0.20,
                under_base_bedding_m=0.15,
                working_space_m=0.50,
            ),
        ],
        shoring_depth_threshold_m=1.25,
        hand_excavation_ratio=0.15,
    )

    assert len(rep.pipe_breakdowns) == 1
    assert rep.manhole_pits_count == 2

    pb = rep.pipe_breakdowns[0]
    assert pb.trench_bottom_width_m == pytest.approx(0.90, abs=0.01)
    assert pb.total_excavation_m3 == pytest.approx(90.0, abs=0.05)
    assert pb.excavation_0_to_1_5m_m3 == pytest.approx(67.5, abs=0.05)
    assert pb.excavation_1_5_to_3_0m_m3 == pytest.approx(22.5, abs=0.05)
    assert pb.shoring_area_m2 == pytest.approx(200.0, abs=0.1)
    assert pb.warning_tape_length_m == pytest.approx(50.0)
    assert pb.geotextile_wrap_area_m2 > 150.0

    # Verify strict volume balance: Excavation = Backfill + Excess Spoil
    assert rep.total_excavation_m3 == pytest.approx(
        rep.total_main_backfill_m3 + rep.total_excess_spoil_haul_off_m3, abs=0.01
    )
    # Excess spoil equals imported bedding/zone + pipe & manhole structure displacement
    assert rep.total_excess_spoil_haul_off_m3 == pytest.approx(
        rep.total_bedding_and_zone_net_m3 + rep.total_pipe_and_manhole_displacement_m3, abs=0.01
    )
    assert rep.total_bedding_order_tonnage_t > rep.total_bedding_and_zone_net_m3


def test_trench_earthwork_sloped_and_shallow_cover_warning_and_dxf_auto(tmp_path: Path) -> None:
    """
    Verify sloped unshored trench calculation, shallow cover (<0.80m) warning,
    and automatic extraction from a utility CAD DXF when only `dxf_path` is passed.
    """
    rep_sloped = calculate_trench_earthwork_and_bedding(
        pipe_segments=[
            TrenchPipeSegmentInput(
                segment_id="KPE-01",
                utility_type="WATER_PRESSURE",
                pipe_material="KPE",
                nominal_diameter_dn_mm=110,
                length_m=30.0,
                start_depth_to_invert_m=0.75,  # Shallow! Crown cover = 0.75 - 0.11 = 0.64m < 0.80m
                end_depth_to_invert_m=0.85,
                trench_wall_mode="SLOPED_UNSHORED",
                slope_h_to_v=0.5,
            )
        ]
    )
    assert rep_sloped.total_shoring_area_m2 == 0.0
    assert len(rep_sloped.warnings) >= 1
    assert "0.80m" in rep_sloped.warnings[0]

    # Auto-extraction from DXF
    dxf_file = create_utility_network_dxf(tmp_path / "utility_trench_auto.dxf")
    rep_dxf = calculate_trench_earthwork_and_bedding(dxf_path=dxf_file)
    assert rep_dxf.total_pipe_length_m > 0.0
    assert rep_dxf.total_excavation_m3 > 0.0
    assert rep_dxf.manhole_pits_count >= 2


def test_supplier_bid_tabulator_landed_cost_and_excel_export(tmp_path: Path) -> None:
    """
    Verify Tool 19 (compare_supplier_quotes_and_build_bid_tab):
    - Supplier A quotes lower ex-works unit prices (4,100 HUF/m2 paving, 3,200 HUF/m curb)
      but adds 95,000 HUF/truckload freight + 2,500 HUF/pallet crane fee + 1,800 HUF/pallet usage fee,
      plus fine-print traps ('100% előleg', 'helyszíni lerakodást nem tartalmaz').
    - Supplier B (parsed from an .xlsx quote file) quotes 4,550 HUF/m2 and 3,500 HUF/m with -5% discount
      and INCLUDED freight/unloading -> Supplier B wins on True Landed Package Cost!
    """
    sup_b_xlsx = tmp_path / "supplier_b_quote.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Kód", "Megnevezés", "Egységár (Ft)"])
    ws.append(["TK-01", "8 cm vastag szürke hullám térkő", 4550])
    ws.append(["SZEG-01", "Kiemelt útszegély 15x30x100 cm", 3500])
    wb.save(sup_b_xlsx)
    wb.close()

    out_bid_tab = tmp_path / "Artukor_Teszt.xlsx"

    rep = compare_supplier_quotes_and_build_bid_tab(
        target_items=[
            BidTabTargetItemInput(
                item_id="TK-01",
                description="8 cm vastag szürke hullám térkő",
                unit="m2",
                required_quantity=500.0,
                boq_sheet_name="IV.1. Útépítés",
                boq_row_number=12,
            ),
            BidTabTargetItemInput(
                item_id="SZEG-01",
                description="Kiemelt útszegély 15x30x100 cm",
                unit="fm",
                required_quantity=200.0,
                boq_sheet_name="IV.1. Útépítés",
                boq_row_number=18,
            ),
        ],
        supplier_quotes=[
            SupplierQuoteInput(
                supplier_name="Olcsó Telephelyi Kft.",
                quote_reference="AJ-2026/101",
                lines=[
                    SupplierQuoteLineInput(item_id="TK-01", quoted_unit_price=4100.0),
                    SupplierQuoteLineInput(item_id="SZEG-01", quoted_unit_price=3200.0),
                ],
                freight_mode="PER_TRUCKLOAD",
                freight_rate=95000.0,
                unloading_fee_per_pallet=2500.0,
                non_refundable_pallet_fee_per_pallet=1800.0,
                payment_terms="100% előleg (előre utalás)",
                notes_and_conditions="Az ár a helyszíni lerakodást nem tartalmazza, bontott raklap esetén szálkadíj.",
            ),
            SupplierQuoteInput(
                supplier_name="Komplett Helyszíni Kft.",
                quote_reference="AJ-2026/202",
                quote_file_path=str(sup_b_xlsx),
                global_discount_percent=5.0,
                freight_mode="INCLUDED",
                payment_terms="30 napos utólagos átutalás",
            ),
        ],
        output_xlsx_path=out_bid_tab,
        contractor_markup_factor=1.15,
        round_step=10.0,
    )

    assert rep.best_single_supplier == "Komplett Helyszíni Kft."
    assert rep.single_supplier_savings_vs_second > 200_000.0
    assert out_bid_tab.exists()
    assert len(rep.recommended_boq_pricings) == 2

    sup_a = next(s for s in rep.supplier_summaries if s.supplier_name == "Olcsó Telephelyi Kft.")
    assert sup_a.estimated_truckloads == 5  # ~112 tonnes / 24t = 5 trucks
    assert sup_a.freight_cost_total == pytest.approx(5 * 95000.0)
    assert len(sup_a.fine_print_warnings) >= 3


def test_road_cross_section_shoulder_slope_and_ditch_qto() -> None:
    """
    Verify Tool 20 (calculate_cross_section_and_embankment_qto) on a 100m road section:
    - Left & right shoulder width: 1.00m each -> Top shoulder area = 2.00m * 100m = 200.0 m2
    - Shoulder thickness: 20cm (0.20m), slope 1:1.5 -> Avg width per side = 1.00 + 0.5*1.5*0.20 = 1.15m
      -> Compacted shoulder volume = 2 * 1.15 * 0.20 * 100 = 46.0 m3
    - Embankment height: 1.00m left + 1.00m right (sum=2.0m), slope 1:1.5
      -> Inclined slope surface area = 2.0 * sqrt(1 + 1.5^2) * 100 = 200 * 1.8027756 = 360.56 m2
      -> Slope topsoil (10cm) = 36.056 m3
    - Ditch on BOTH sides (200m total ditch length), bottom=0.40m, depth=0.60m, 1:1.5 slopes,
      GRAVEL_INFILTRATION_TRENCH filled to 0.50m depth:
      -> Ditch top width = 0.40 + 0.60 * 3.0 = 2.20m
      -> Ditch cross-section area = 0.5 * (0.40 + 2.20) * 0.60 = 0.78 m2 -> Volume = 0.78 * 200 = 156.0 m3
    """
    rep = calculate_cross_section_and_embankment_qto(
        segments=[
            RoadCrossSectionSegmentInput(
                segment_id="0+000..0+100",
                length_m=100.0,
                shoulder_width_left_m=1.00,
                shoulder_width_right_m=1.00,
                shoulder_thickness_cm=20.0,
                embankment_height_left_m=1.00,
                embankment_height_right_m=1.00,
                slope_ratio_h_to_v=1.5,
                slope_topsoil_thickness_m=0.10,
                ditch_sides="BOTH",
                ditch_bottom_width_m=0.40,
                ditch_depth_m=0.60,
                ditch_inner_slope_h_to_v=1.5,
                ditch_outer_slope_h_to_v=1.5,
                ditch_lining_type="GRAVEL_INFILTRATION_TRENCH",
                infiltration_gravel_depth_m=0.50,
            )
        ],
        shoulder_compaction_factor=1.15,
        shoulder_bulk_density_t_per_m3=2.05,
    )

    assert rep.total_shoulder_top_area_m2 == pytest.approx(200.0)
    assert rep.total_shoulder_compacted_volume_m3 == pytest.approx(46.0, abs=0.01)
    assert rep.total_shoulder_order_volume_m3 == pytest.approx(46.0 * 1.15, abs=0.01)
    assert rep.total_slope_inclined_area_m2 == pytest.approx(360.56, abs=0.05)
    assert rep.total_slope_topsoil_volume_m3 == pytest.approx(36.056, abs=0.01)
    assert rep.total_ditch_length_m == pytest.approx(200.0)
    assert rep.total_ditch_excavation_m3 == pytest.approx(156.0, abs=0.05)
    assert rep.total_infiltration_gravel_m3 > 100.0
    assert rep.total_infiltration_geotextile_m2 > 800.0


def test_generate_progress_certificate_and_overrun_detection(tmp_path: Path) -> None:
    """
    Verify Tool 21 (generate_progress_certificate):
    - Item 1: 1,000 m2 paving at 10,000 HUF/m2 (Contract = 10,000,000 HUF)
      * Previous cumulative: 400 m2 (40%), Current period: 400 m2 (40%) -> New cumulative = 800 m2 (80%)
      * Current period work = 4,000,000 HUF
    - Item 2: 200 m curb at 5,000 HUF/m (Contract = 1,000,000 HUF)
      * Previous cumulative: 100 m, Current period: 120 m -> New cumulative = 220 m (110% -> +20 m overrun!)
      * Current period work = 600,000 HUF, Overrun amount = 100,000 HUF
    - Total current period gross work = 4,600,000 HUF
    - Advance deduction: 10% (-460,000 HUF), Retention: 5% (-230,000 HUF)
    - Net invoicable = 3,910,000 HUF
    - Verify 2-sheet Excel output ('1_Teljesítésigazolás' and '2_Felmérési_Napló').
    """
    out_cert = tmp_path / "Teljesitesigazolas_1_reszszamla.xlsx"

    rep = generate_progress_certificate(
        progress_entries=[
            ProgressItemEntryInput(
                item_key="1.1",
                description="8 cm vastag térkő burkolat építése",
                unit="m2",
                contracted_quantity=1000.0,
                contracted_unit_price=10000.0,
                previous_cumulative_quantity=400.0,
                current_period_quantity=400.0,
                measurement_proof_formula="Geodéziai felmérés II. ütem: 20,0 m × 20,0 m = 400,0 m2",
            ),
            ProgressItemEntryInput(
                item_key="1.2",
                description="Kiemelt útszegély építése C20/25 alappal",
                unit="fm",
                contracted_quantity=200.0,
                contracted_unit_price=5000.0,
                previous_cumulative_quantity=100.0,
                current_period_quantity=120.0,
                measurement_proof_formula="Geodéziai felmérés keleti ág: 120,0 fm (+20,0 fm pótmunka)",
            ),
        ],
        project_name="Foktő Ipari Park Útépítés",
        certificate_number="2. Részszámla",
        advance_deduction_percent=10.0,
        retention_percent=5.0,
        vat_percent=27.0,
        output_xlsx_path=out_cert,
    )

    assert rep.total_contract_amount == pytest.approx(11_000_000.0)
    assert rep.current_period_gross_work_amount == pytest.approx(4_600_000.0)
    assert rep.overrun_items_count == 1
    assert rep.total_overrun_variation_amount == pytest.approx(100_000.0)
    assert rep.advance_deduction_amount == pytest.approx(460_000.0)
    assert rep.retention_deduction_amount == pytest.approx(230_000.0)
    assert rep.net_invoicable_amount == pytest.approx(3_910_000.0)
    assert len(rep.warnings) >= 1
    assert out_cert.exists()

    wb = openpyxl.load_workbook(out_cert)
    assert wb.sheetnames == ["1_Teljesítésigazolás", "2_Felmérési_Napló"]
    wb.close()


def test_cad_hardening_mleader_schedule_and_cross_layer_polygonize(tmp_path: Path) -> None:
    """
    Verify v0.6.1 CAD geometry & callout hardening:
    1. MULTILEADER (MLEADER) arrow-tip matching to manhole CS-10 from outside search_radius_m.
    2. Distant Schedule Table row matching to manhole CS-11 by Node ID ('CS-11 | F: 118.40 | Ff: 115.90').
    3. Cross-layer polygonize across 3 separate layers ('SZEGELY_KIEMELT', 'SZEGELY_SULLYESZTETT',
       'BURKOLATVALTAS') with a 0.05m (5cm) corner gap bridged automatically into a 200.0 m2 polygon.
    """
    dxf_path = tmp_path / "hardened_cad_test.dxf"
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()

    # 1. Manhole CS-10 at (10, 20) with MULTILEADER text placed 15m away at (25, 35)
    msp.add_circle((10.0, 20.0), radius=0.50, dxfattribs={"layer": "AKNAK"})
    ml_builder = msp.add_multileader_mtext("Standard")
    ml_builder.multileader.dxf.layer = "KOZMU_FELIRAT"
    ml_builder.quick_leader(
        "CS-10\\PF: 116.20\\PFf: 113.70\\PDN1000",
        target=ezdxf.math.Vec2(10.0, 20.0),
        segment1=ezdxf.math.Vec2(15.0, 15.0),
    )

    # 2. Manhole CS-11 at (50, 20) with only ID label nearby, and levels in a schedule at (250, 250)
    msp.add_circle((50.0, 20.0), radius=0.50, dxfattribs={"layer": "AKNAK"})
    msp.add_text("CS-11", dxfattribs={"layer": "KOZMU_FELIRAT", "insert": (50.5, 20.5)})
    msp.add_text(
        "CS-11 | Fedlapszint: 118,40 | Folyásfenék: 115,90 | DN1000",
        dxfattribs={"layer": "KOZMU_FELIRAT", "insert": (250.0, 250.0)},
    )

    # 3. Cross-layer 20m x 10m (200 m2) roadway enclosed by 3 different layers with a 5cm (0.05m) corner gap at (20, 10)
    msp.add_line((0.0, 0.0), (20.0, 0.0), dxfattribs={"layer": "SZEGELY_KIEMELT"})
    msp.add_line((20.0, 0.0), (20.0, 9.95), dxfattribs={"layer": "BURKOLATVALTAS"})  # 5cm gap short of (20, 10)!
    msp.add_line((20.0, 10.0), (0.0, 10.0), dxfattribs={"layer": "SZEGELY_SULLYESZTETT"})
    msp.add_line((0.0, 10.0), (0.0, 0.0), dxfattribs={"layer": "BURKOLATVALTAS"})

    doc.saveas(str(dxf_path))

    # Check Tool 1 (inspect_civil_dxf) sees MULTILEADER sample text
    insp = inspect_civil_dxf(dxf_path)
    felirat_lyr = next(l for l in insp.layers if l.layer_name == "KOZMU_FELIRAT")
    assert any("CS-10" in s for s in felirat_lyr.sample_texts)

    # Check Tool 3 (extract_utility_network_and_levels) resolves both CS-10 (MLEADER) and CS-11 (Schedule)
    util_rep = extract_utility_network_and_levels(
        file_path=dxf_path,
        node_layers=["AKNAK"],
        text_layers=["KOZMU_FELIRAT"],
        search_radius_m=8.0,
    )
    nodes_by_id = {n.node_id: n for n in util_rep.nodes}
    assert "CS-10" in nodes_by_id
    assert nodes_by_id["CS-10"].matched_via == "LEADER_ARROW"
    assert nodes_by_id["CS-10"].depth_h1_m == pytest.approx(2.50)

    assert "CS-11" in nodes_by_id
    assert nodes_by_id["CS-11"].cover_level_m == pytest.approx(118.40)
    assert nodes_by_id["CS-11"].invert_level_m == pytest.approx(115.90)
    assert nodes_by_id["CS-11"].depth_h1_m == pytest.approx(2.50)

    # Check Tool 2 (measure_linear_and_area_features) bridges the 5cm corner gap across 3 layers -> 200.0 m2
    meas_rep = measure_linear_and_area_features(
        file_path=dxf_path,
        boundary_layers=["SZEGELY_KIEMELT", "SZEGELY_SULLYESZTETT", "BURKOLATVALTAS"],
        auto_close_gap_tolerance_m=0.10,
    )
    assert meas_rep.area_summary is not None
    assert meas_rep.area_summary.net_area_m2 == pytest.approx(200.0, abs=0.5)
    assert any("Bridged" in w for w in meas_rep.warnings)


def test_manufacturer_synonyms_and_lidl_bbs_profile_audit(tmp_path: Path) -> None:
    """
    Verify v0.6.1 Package #2 & #3:
    1. Manufacturer brand synonym matching ('Leier Solido', 'KK Rotterdam') and automatic freight/pallet
       fee extraction from supplier quote Excel files in Tool 19.
    2. Lidl BBS profile auditing ('LIDL-01-GRASS-GRID-BBS-VIOLATION' & 'LIDL-02-STAINLESS-SEPARATOR')
       and automatic technical clarification questions generation in Tool 13.
    """
    leier_xlsx = tmp_path / "leier_quote.xlsx"
    wb_l = openpyxl.Workbook()
    ws_l = wb_l.active
    assert ws_l is not None
    ws_l.append(["Megnevezés", "Egységár"])
    ws_l.append(["Leier Solido szürke 8 cm", 4200])
    ws_l.append(["Fuvardíj (nyerges, 24t) fordulónként", 90000])
    ws_l.append(["Raklaphasználati díj", 1600])
    wb_l.save(leier_xlsx)
    wb_l.close()

    kk_xlsx = tmp_path / "kk_quote.xlsx"
    wb_k = openpyxl.Workbook()
    ws_k = wb_k.active
    assert ws_k is not None
    ws_k.append(["Megnevezés", "Egységár"])
    ws_k.append(["KK Rotterdam szürke 8 cm", 4350])
    wb_k.save(kk_xlsx)
    wb_k.close()

    bid_rep = compare_supplier_quotes_and_build_bid_tab(
        target_items=[
            BidTabTargetItemInput(
                item_id="TK-01",
                description="Hullám térkő burkolat",
                unit="m2",
                required_quantity=240.0,
            )
        ],
        supplier_quotes=[
            SupplierQuoteInput(supplier_name="Leier", quote_file_path=str(leier_xlsx)),
            SupplierQuoteInput(supplier_name="KK Kavics Beton", quote_file_path=str(kk_xlsx)),
        ],
    )
    leier_sum = next(s for s in bid_rep.supplier_summaries if s.supplier_name == "Leier")
    assert leier_sum.quoted_items_count == 1
    assert leier_sum.freight_cost_total > 0.0
    assert leier_sum.pallet_and_packaging_cost_total > 0.0

    # Test Lidl BBS profile audit in Tool 13
    lidl_boq = tmp_path / "lidl_tender_boq.xlsx"
    wb_b = openpyxl.Workbook()
    ws_b = wb_b.active
    assert ws_b is not None
    ws_b.title = "Útépítés"
    ws_b.append(["Tételszám", "Kód", "Megnevezés", "Me.", "Mennyiség", "Egységár", "Összesen"])
    ws_b.append(["1", "E-1", "Beton gyeprács burkolat 10 cm parkolóban", "m2", 600.0, 8500.0, "=E2*F2"])
    ws_b.append(["2", "E-2", "Térkő burkolat építése", "m2", 1200.0, 9500.0, "=E3*F3"])
    ws_b.append(["3", "E-3", "AC 11 aszfalt kopóréteg 4 cm", "m2", 800.0, 6200.0, "=E4*F4"])
    wb_b.save(lidl_boq)
    wb_b.close()

    comp_rep = audit_technological_completeness(
        boq_xlsx_path=lidl_boq,
        project_profile="LIDL_BBS",
        locale="hu",
    )
    rule_ids = {f.rule_id for f in comp_rep.findings}
    assert "LIDL-01-GRASS-GRID-BBS-VIOLATION" in rule_ids
    assert "LIDL-02-STAINLESS-SEPARATOR" in rule_ids
    assert len(comp_rep.technical_clarification_questions) == len(comp_rep.findings)
    assert any("Lidl BBS" in q for q in comp_rep.technical_clarification_questions)


def test_mcp_server_has_all_21_v06_tools() -> None:
    """Verify all 21 tools, 5 engineering resources, and 5 workflow prompts are registered on the MCPServer instance."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    assert tool_manager is not None
    tools_dict = tool_manager._tools
    assert "calculate_trench_earthwork_and_bedding" in tools_dict
    assert "compare_supplier_quotes_and_build_bid_tab" in tools_dict
    assert "calculate_cross_section_and_embankment_qto" in tools_dict
    assert "generate_progress_certificate" in tools_dict
    assert len(tools_dict) == 21

    resource_manager = getattr(mcp, "_resource_manager", None)
    assert resource_manager is not None
    resources_dict = resource_manager._resources
    assert "civil-qto://standards/en1610-trench-widths" in resources_dict
    assert "civil-qto://standards/pavement-densities-and-factors" in resources_dict
    assert "civil-qto://standards/precast-manhole-catalogs" in resources_dict
    assert "civil-qto://standards/technological-completeness-chains" in resources_dict
    assert "civil-qto://workflow/hitl-3-step-protocol" in resources_dict
    assert len(resources_dict) == 5

    prompt_manager = getattr(mcp, "_prompt_manager", None)
    assert prompt_manager is not None
    prompts_dict = prompt_manager._prompts
    assert "tender_full_audit" in prompts_dict
    assert "roadworks_and_paving_qto" in prompts_dict
    assert "utility_network_and_trench_qto" in prompts_dict
    assert "supplier_bid_tabulation" in prompts_dict
    assert "monthly_progress_billing" in prompts_dict
    assert len(prompts_dict) == 5
