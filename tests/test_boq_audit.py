"""Unit tests for Phase 4: BoQ Excel Auditor, QTO <-> BoQ Cross-Checker, and FastMCP Server."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from civil_qto_mcp.boq.excel_auditor import inspect_and_audit_boq_xlsx
from civil_qto_mcp.models.boq_models import QtoInputRecord
from civil_qto_mcp.server import (
    compare_qto_with_boq,
    inspect_civil_dxf,
    mcp,
)


def test_boq_audit_hidden_sheets_hidden_rows_and_traps(
    boq_traps_xlsx_path: Path,
) -> None:
    """
    Verify:
    - Hidden sheet 'Kalkulacio_Belsos_Rejtett' is strictly excluded and listed in skipped_hidden_sheets
    - Hidden row 7 ('Alternatív gyeprács (REJTETT SOR)') on '01_Ut_es_Burkolat' is skipped
    - Both SINGLE_PRICE and SPLIT_MATERIAL_LABOR (TERC) layouts are parsed
    - Formula anomalies caught: HARDCODED_TOTAL, MATH_MISMATCH, ZERO_UNIT_PRICE, SUM_RANGE_GAP
    """
    report = inspect_and_audit_boq_xlsx(boq_traps_xlsx_path)

    assert "Kalkulacio_Belsos_Rejtett" in report.skipped_hidden_sheets
    assert report.visible_sheets_processed == ["01_Ut_es_Burkolat", "02_Csatorna_TERC"]
    # 5 visible items on sheet 1 + 2 visible items on sheet 2 = 7 items
    assert report.total_items_extracted == 7

    # Ensure neither hidden sheet items nor hidden row 7 items leaked into extracted items
    descriptions = [it.description for it in report.items]
    assert not any("REJTETT SOR" in d for d in descriptions)
    assert not any("titkos tartalékkeret" in d for d in descriptions)

    summaries_by_name = {s.sheet_name: s for s in report.sheet_summaries}
    assert summaries_by_name["01_Ut_es_Burkolat"].layout_type == "SINGLE_PRICE"
    assert summaries_by_name["01_Ut_es_Burkolat"].hidden_rows_skipped == 1
    assert summaries_by_name["02_Csatorna_TERC"].layout_type == "SPLIT_MATERIAL_LABOR"

    anomaly_types = {a.anomaly_type for a in report.formula_anomalies}
    assert "HARDCODED_TOTAL" in anomaly_types
    assert "MATH_MISMATCH" in anomaly_types
    assert "ZERO_UNIT_PRICE" in anomaly_types
    assert "SUM_RANGE_GAP" in anomaly_types


def test_boq_audit_keyword_filter_and_pagination_preserve_anomalies(
    boq_traps_xlsx_path: Path,
) -> None:
    """
    Verify that keyword_filter, limit, and offset restrict returned `items`
    while still reporting 100% of sheet_summaries and formula_anomalies.
    """
    report = inspect_and_audit_boq_xlsx(
        boq_traps_xlsx_path,
        keyword_filter="szegély",
        limit=1,
        offset=0,
    )

    assert report.total_items_extracted == 7
    assert report.total_matching_items == 2  # 'Kiemelt szegély' and 'szegélyalap beton'
    assert report.returned_items_count == 1
    assert len(report.items) == 1
    assert len(report.formula_anomalies) >= 4


def test_compare_qto_with_boq_unit_synonyms_and_conversions(
    boq_traps_xlsx_path: Path,
) -> None:
    """
    Verify unit synonym & multiplier handling in compare_qto_with_boq:
    - 125.708 m (CAD) vs 0.125708 km (BoQ item 1.1) -> EXACT_MATCH
    - 908.18 m2 (CAD) vs 820.0 nm (BoQ item 1.2) -> CRITICAL_DISCREPANCY (+10.75%)
    - 3 pcs (CAD) vs 3.0 akna (BoQ item 2.1) -> EXACT_MATCH
    - 40.0 m (CAD) vs 40.0 fm (BoQ item 2.2) -> EXACT_MATCH
    - Unmatched feature -> MISSING_IN_BOQ
    """
    qto_inputs = [
        QtoInputRecord(
            qto_id="SZEGELY_KIEMELT",
            description="Kiemelt szegély 15x25x100",
            measured_quantity=125.708,
            measured_unit="m",
            target_item_number="1.1",
        ),
        QtoInputRecord(
            qto_id="BURKOLAT_TERKO",
            description="Térkő burkolat 8 cm",
            measured_quantity=908.18,
            measured_unit="m2",
            target_item_number="1.2",
        ),
        QtoInputRecord(
            qto_id="CSAPADEK_AKNAK",
            description="Tisztítóakna DN1000-DN1200",
            measured_quantity=3.0,
            measured_unit="pcs",
            target_item_number="2.1",
        ),
        QtoInputRecord(
            qto_id="CSATORNA_DN300",
            description="KG-PVC DN300 csatorna",
            measured_quantity=40.0,
            measured_unit="m",
            target_item_number="2.2",
        ),
        QtoInputRecord(
            qto_id="OLAJFOGO_MUTARGY",
            description="Olajfogó műtárgy",
            measured_quantity=1.0,
            measured_unit="db",
            target_item_number="99.9",
            keyword_query="Olajfogó műtárgy",
        ),
    ]

    cmp_report = compare_qto_with_boq(
        file_path_xlsx=str(boq_traps_xlsx_path),
        qto_records=qto_inputs,
    )

    assert cmp_report.total_comparisons == 5
    assert cmp_report.exact_or_within_tolerance_count == 3
    assert cmp_report.discrepancy_count == 1
    assert cmp_report.missing_or_incompatible_count == 1

    by_id = {c.qto_id: c for c in cmp_report.comparisons}
    assert by_id["SZEGELY_KIEMELT"].status == "EXACT_MATCH"
    assert by_id["SZEGELY_KIEMELT"].boq_quantity_canonical == pytest.approx(125.708)

    assert by_id["BURKOLAT_TERKO"].status == "CRITICAL_DISCREPANCY"
    assert by_id["BURKOLAT_TERKO"].delta_canonical == pytest.approx(88.18, abs=0.01)

    assert by_id["CSAPADEK_AKNAK"].status == "EXACT_MATCH"
    assert by_id["CSATORNA_DN300"].status == "EXACT_MATCH"
    assert by_id["OLAJFOGO_MUTARGY"].status == "MISSING_IN_BOQ"


def test_mcp_server_tool_registration_and_stdout_protection(
    eov_parking_dxf_path: Path,
) -> None:
    """
    Verify all 5 tools are registered on the FastMCP instance and that executing tools
    emits 0 bytes to sys.stdout (preserving JSON-RPC stdio integrity).
    """
    registered_tools = {t.name for t in mcp._tool_manager.list_tools()}
    expected_tools = {
        "inspect_civil_dxf",
        "measure_linear_and_area_features",
        "extract_utility_network_and_levels",
        "inspect_and_audit_boq_xlsx",
        "compare_qto_with_boq",
    }
    assert expected_tools.issubset(registered_tools)

    # Capture sys.stdout during tool invocation to ensure zero stdout pollution
    captured_stdout = io.StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = captured_stdout
        res = inspect_civil_dxf(file_path=str(eov_parking_dxf_path))
        assert res.total_modelspace_entities > 0
    finally:
        sys.stdout = old_stdout

    assert captured_stdout.getvalue() == ""
