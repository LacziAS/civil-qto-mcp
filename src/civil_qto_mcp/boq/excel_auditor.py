"""
Tool 4 implementation: inspect_and_audit_boq_xlsx
Safe, deterministic Excel (.xlsx / .xlsm) Bill of Quantities parser and mathematical auditor.
- Strictly excludes hidden and veryHidden worksheets (reporting them in skipped_hidden_sheets)
- Skips and counts hidden rows (row_dimensions[r].hidden)
- Detects single-price and TERC split (Material + Labor) column layouts
- Performs dual-pass (formula + cached value) and Decimal arithmetic verification
- Detects MATH_MISMATCH, HARDCODED_TOTAL, SUM_RANGE_GAP, SHARED_FORMULA_RISK, and ZERO anomalies
- Supports sheet filtering, keyword filtering, and pagination (limit/offset) to protect LLM context
"""

from __future__ import annotations

import re
import warnings
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from civil_qto_mcp.boq.unit_normalizer import normalize_engineering_unit
from civil_qto_mcp.models.boq_models import (
    BoQAuditReport,
    BoQFormulaAnomaly,
    BoQItem,
    BoQSheetSummary,
)

SUM_FORMULA_REGEX = re.compile(
    r"^=\s*SUM\(\s*\$?([A-Z]{1,3})\$?(\d+)\s*:\s*\$?([A-Z]{1,3})\$?(\d+)\s*\)",
    re.IGNORECASE,
)


def _to_float(val: object) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s or s.startswith("="):
        return None
    s = s.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    s = re.sub(r"[^0-9.+-]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _to_decimal(val: float | None) -> Decimal:
    if val is None:
        return Decimal(0)
    try:
        return Decimal(str(round(val, 6)))
    except InvalidOperation:
        return Decimal(0)


def _check_shared_formulas_in_zip(xlsx_path: Path) -> list[tuple[str, str]]:
    """
    Scan worksheet XML files inside the .xlsx/.xlsm archive for `<f t="shared"` tags.
    Returns list of (xml_filename, sample_ref).
    """
    shared_hits: list[tuple[str, str]] = []
    shared_re = re.compile(r'<f[^>]*\bt="shared"[^>]*\bref="([^"]+)"', re.IGNORECASE)
    try:
        with zipfile.ZipFile(xlsx_path, "r") as zf:
            for name in zf.namelist():
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                    content = zf.read(name).decode("utf-8", errors="ignore")
                    for m in shared_re.finditer(content):
                        shared_hits.append((name, m.group(1)))
    except (zipfile.BadZipFile, OSError):
        pass
    return shared_hits


def _detect_sheet_columns(
    rows_text: list[tuple[int, list[str]]],
) -> tuple[int, dict[str, int]]:
    """
    Scan top rows of a worksheet to locate the BoQ header row and column indices (0-based).
    Returns (header_row_1based, col_map).
    """
    best_row = 0
    best_map: dict[str, int] = {}
    best_score = 0

    for r_idx, cells in rows_text:
        col_map: dict[str, int] = {}
        for c_idx, raw_txt in enumerate(cells):
            # Normalize hyphens, newlines, and multiple spaces
            t = re.sub(r"[-_\r\n\t]+", " ", raw_txt.strip().lower())
            t = re.sub(r"\s+", " ", t).strip()
            t_no_hyphen = raw_txt.strip().lower().replace("-", "").replace("\n", " ")
            if not t:
                continue
            if "item_no" not in col_map and any(
                k in t or k in t_no_hyphen
                for k in ("sorsz", "ssz", "tételsz", "tetelsz", "item no", "pos", "tsz")
            ):
                col_map["item_no"] = c_idx
            elif "code" not in col_map and any(
                k in t or k in t_no_hyphen
                for k in ("éngy", "engy", "terc", "kód", "kod", "code", "azonosító", "tételsz", "tetelsz")
            ):
                col_map["code"] = c_idx
            elif (
                ("desc" not in col_map or t in ("megnevezés", "megnevezes", "tétel megnevezése", "munka megnevezése"))
                and "kategória" not in t
                and "kategoria" not in t
                and "fejezet" not in t
                and any(
                    k in t or k in t_no_hyphen
                    for k in (
                        "megnevezés",
                        "megnevezes",
                        "tétel szöveg",
                        "tetel szoveg",
                        "leírás",
                        "description",
                        "munka megnevezése",
                        "tétel megnevezése",
                    )
                )
            ):
                col_map["desc"] = c_idx
            elif "qty" not in col_map and any(
                k in t or k in t_no_hyphen
                for k in ("mennyiség", "mennyiseg", "menny.", "menny", "qty", "quantity", "menge")
            ):
                col_map["qty"] = c_idx
            elif "unit" not in col_map and (
                t in ("me", "m.e.", "m.e", "egység", "egyseg", "egys.", "egys", "unit", "eh")
                or "mértékegység" in t_no_hyphen
                or "mertekegyseg" in t_no_hyphen
            ):
                col_map["unit"] = c_idx
            elif "mat_unit" not in col_map and (
                ("anyag" in t or "material" in t)
                and any(k in t for k in ("egység", "egyseg", "ár", "ar", "unit", "price"))
            ):
                col_map["mat_unit"] = c_idx
            elif "equip_unit" not in col_map and (
                ("gépdíj" in t_no_hyphen or "gepdij" in t_no_hyphen or "gép ár" in t or "equipment" in t)
                and any(k in t for k in ("egység", "egyseg", "ár", "ar", "díj", "dij", "unit", "price"))
            ):
                col_map["equip_unit"] = c_idx
            elif "lab_unit" not in col_map and (
                ("díj" in t or "dij" in t or "labor" in t or "munkadíj" in t)
                and "gép" not in t
                and "gep" not in t
                and any(k in t for k in ("egység", "egyseg", "ár", "ar", "unit", "price"))
            ):
                col_map["lab_unit"] = c_idx
            elif (
                "unit_price" not in col_map
                and "mat_unit" not in col_map
                and (
                    any(k in t_no_hyphen for k in ("egységár", "egysegar", "unit price", "egys. ár"))
                    or t.startswith("ár ")
                    or t == "ár"
                )
            ):
                col_map["unit_price"] = c_idx
            elif (
                "total" in col_map
                and any(
                    k in t
                    for k in (
                        "összesen",
                        "osszesen",
                        "mindösszesen",
                        "összeg",
                        "osszeg",
                        "összköltség",
                        "osszköltseg",
                        "total",
                    )
                )
                and "anyag" not in t
                and "díj" not in t
                and "dij" not in t
                and "munka" not in t
                and "gép" not in t
                and "opció" not in t
                and "opcio" not in t
            ):
                # Prefer the rightmost grand total column on split-price sheets (e.g. 'A+D összesen')
                col_map["total"] = c_idx
            elif "total" not in col_map and any(
                k in t
                for k in (
                    "összesen",
                    "osszesen",
                    "összeg",
                    "osszeg",
                    "összköltség",
                    "osszköltseg",
                    "érték",
                    "ertek",
                    "total",
                    "amount",
                )
            ):
                if "anyag" in t:
                    col_map["mat_total"] = c_idx
                elif "díj" in t or "dij" in t or "munka" in t:
                    col_map["lab_total"] = c_idx
                elif "gép" in t or "gep" in t:
                    col_map["equip_total"] = c_idx
                elif "opció" not in t and "opcio" not in t:
                    col_map["total"] = c_idx

        score = len(col_map)
        if "desc" in col_map and ("qty" in col_map or "total" in col_map):
            score += 3
        if score > best_score:
            best_score = score
            best_row = r_idx
            best_map = col_map

    return best_row, best_map


def inspect_and_audit_boq_xlsx(
    file_path: str | Path,
    sheet_names: list[str] | None = None,
    keyword_filter: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> BoQAuditReport:
    """
    Safely read and mathematically audit a civil engineering BoQ Excel workbook (.xlsx / .xlsm).
    Strictly skips hidden/veryHidden sheets and hidden rows, checks all formulas and SUM ranges,
    and returns normalized BoQ items (filtered by sheet_names, keyword_filter, limit, and offset).
    """
    path_obj = Path(file_path).resolve()
    if not path_obj.exists():
        raise FileNotFoundError(f"Excel file not found: {path_obj}")

    shared_hits = _check_shared_formulas_in_zip(path_obj)
    has_shared = len(shared_hits) > 0

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb_formula = openpyxl.load_workbook(str(path_obj), data_only=False, read_only=False)
        wb_values = openpyxl.load_workbook(str(path_obj), data_only=True, read_only=False)

    visible_sheets: list[str] = []
    skipped_hidden_sheets: list[str] = []
    sheet_filter_set = set(sheet_names) if sheet_names else None

    for ws in wb_formula.worksheets:
        if ws.sheet_state != "visible":
            skipped_hidden_sheets.append(ws.title)
        else:
            if sheet_filter_set is None or ws.title in sheet_filter_set:
                visible_sheets.append(ws.title)

    anomalies: list[BoQFormulaAnomaly] = []
    if has_shared:
        sample_xml, sample_ref = shared_hits[0]
        anomalies.append(
            BoQFormulaAnomaly(
                sheet_name=sample_xml,
                row_number=0,
                cell_coord=sample_ref,
                anomaly_type="SHARED_FORMULA_RISK",
                message=(
                    f"Workbook contains {len(shared_hits)} shared formula definition(s) (e.g., {sample_xml} ref={sample_ref}). "
                    "When inserting rows, convert shared formulas to explicit per-cell formulas."
                ),
            )
        )

    all_extracted_items: list[BoQItem] = []
    sheet_summaries: list[BoQSheetSummary] = []

    for sname in visible_sheets:
        ws_f = wb_formula[sname]
        ws_v = wb_values[sname]
        max_row = ws_f.max_row or 0
        max_col = min(ws_f.max_column or 0, 30)

        # Scan top 40 visible rows for header detection
        top_rows_text: list[tuple[int, list[str]]] = []
        for r in range(1, min(max_row + 1, 41)):
            if ws_f.row_dimensions[r].hidden:
                continue
            row_strs = [
                str(ws_v.cell(row=r, column=c).value or "").strip()
                for c in range(1, max_col + 1)
            ]
            if any(row_strs):
                top_rows_text.append((r, row_strs))

        header_row, col_map = _detect_sheet_columns(top_rows_text)

        if "desc" not in col_map or "qty" not in col_map:
            # Check if it's a Summary sheet
            is_sum = any(
                k in sname.lower() for k in ("összes", "osszes", "summary", "főösszes", "foosszes")
            )
            sheet_summaries.append(
                BoQSheetSummary(
                    sheet_name=sname,
                    layout_type="SUMMARY_ONLY" if is_sum else "UNSTRUCTURED",
                    extracted_item_count=0,
                    hidden_rows_skipped=sum(
                        1 for r in range(1, max_row + 1) if ws_f.row_dimensions[r].hidden
                    ),
                )
            )
            continue

        is_split = "mat_unit" in col_map and "lab_unit" in col_map
        layout_type = "SPLIT_MATERIAL_LABOR" if is_split else "SINGLE_PRICE"

        hidden_rows_skipped = 0
        sheet_items: list[BoQItem] = []
        item_row_numbers: list[int] = []
        last_subtotal_row = header_row
        calculated_subtotal = Decimal(0)
        reported_subtotal: float | None = None

        c_item = col_map.get("item_no")
        c_code = col_map.get("code")
        c_desc = col_map["desc"]
        c_qty = col_map["qty"]
        c_unit = col_map.get("unit")
        c_mat_u = col_map.get("mat_unit")
        c_lab_u = col_map.get("lab_unit")
        c_equip_u = col_map.get("equip_unit")
        c_up = col_map.get("unit_price")
        c_tot = col_map.get("total")

        for r in range(header_row + 1, max_row + 1):
            if ws_f.row_dimensions[r].hidden:
                hidden_rows_skipped += 1
                continue

            desc_val = str(ws_v.cell(row=r, column=c_desc + 1).value or "").strip()
            qty_raw = ws_v.cell(row=r, column=c_qty + 1).value
            qty_val = _to_float(qty_raw)
            unit_val = (
                str(ws_v.cell(row=r, column=c_unit + 1).value or "").strip()
                if c_unit is not None
                else ""
            )

            tot_formula_obj = ws_f.cell(row=r, column=c_tot + 1).value if c_tot is not None else None
            tot_formula_str = (
                str(tot_formula_obj).strip()
                if isinstance(tot_formula_obj, str) and str(tot_formula_obj).startswith("=")
                else None
            )
            tot_cached_val = (
                _to_float(ws_v.cell(row=r, column=c_tot + 1).value) if c_tot is not None else None
            )

            # Check if this row is a SUM(...) subtotal row
            if tot_formula_str and SUM_FORMULA_REGEX.match(tot_formula_str):
                m_sum = SUM_FORMULA_REGEX.match(tot_formula_str)
                assert m_sum is not None
                sum_start_row = int(m_sum.group(2))
                sum_end_row = int(m_sum.group(4))
                reported_subtotal = tot_cached_val

                # Check if any item rows in the current section (since last_subtotal_row)
                # fall outside [sum_start_row, sum_end_row]
                section_item_rows = [
                    ir for ir in item_row_numbers if last_subtotal_row < ir < r
                ]
                missed_rows = [
                    ir
                    for ir in section_item_rows
                    if (ir < sum_start_row or ir > sum_end_row)
                ]
                last_subtotal_row = r
                if missed_rows and c_tot is not None:
                    coord = f"{get_column_letter(c_tot + 1)}{r}"
                    anomalies.append(
                        BoQFormulaAnomaly(
                            sheet_name=sname,
                            row_number=r,
                            cell_coord=coord,
                            anomaly_type="SUM_RANGE_GAP",
                            message=(
                                f"SUM formula '{tot_formula_str}' at {coord} covers rows {sum_start_row}..{sum_end_row}, "
                                f"excluding item row(s): {missed_rows}."
                            ),
                            formula_str=tot_formula_str,
                            cached_value=tot_cached_val,
                            expected_value=float(calculated_subtotal),
                        )
                    )
                continue

            # Skip blank or section title rows that have neither quantity nor unit
            if not desc_val or (qty_val is None and not unit_val):
                continue

            eff_qty = qty_val if qty_val is not None else 0.0
            item_no_str = (
                str(ws_v.cell(row=r, column=c_item + 1).value or "").strip()
                if c_item is not None
                else ""
            )
            code_str = (
                str(ws_v.cell(row=r, column=c_code + 1).value or "").strip()
                if c_code is not None
                else ""
            )

            mat_u_val = (
                _to_float(ws_v.cell(row=r, column=c_mat_u + 1).value)
                if c_mat_u is not None
                else None
            )
            lab_u_val = (
                _to_float(ws_v.cell(row=r, column=c_lab_u + 1).value)
                if c_lab_u is not None
                else None
            )
            equip_u_val = (
                _to_float(ws_v.cell(row=r, column=c_equip_u + 1).value)
                if c_equip_u is not None
                else None
            )
            single_u_val = (
                _to_float(ws_v.cell(row=r, column=c_up + 1).value) if c_up is not None else None
            )

            if is_split:
                comb_u_val: float | None = (mat_u_val or 0.0) + (lab_u_val or 0.0) + (equip_u_val or 0.0)
            else:
                comb_u_val = single_u_val

            # Audit quantity & price
            if eff_qty == 0.0:
                coord = f"{get_column_letter(c_qty + 1)}{r}"
                anomalies.append(
                    BoQFormulaAnomaly(
                        sheet_name=sname,
                        row_number=r,
                        cell_coord=coord,
                        anomaly_type="ZERO_QUANTITY",
                        message=f"Row {r} ('{desc_val[:50]}') has 0 quantity.",
                        cached_value=0.0,
                    )
                )

            if comb_u_val is not None and comb_u_val == 0.0:
                up_col_idx = (c_up if c_up is not None else c_mat_u or c_qty) + 1
                coord = f"{get_column_letter(up_col_idx)}{r}"
                anomalies.append(
                    BoQFormulaAnomaly(
                        sheet_name=sname,
                        row_number=r,
                        cell_coord=coord,
                        anomaly_type="ZERO_UNIT_PRICE",
                        message=f"Row {r} ('{desc_val[:50]}') has 0 unit price.",
                        cached_value=0.0,
                    )
                )

            expected_dec = _to_decimal(eff_qty) * _to_decimal(comb_u_val)
            calculated_subtotal += expected_dec

            if c_tot is not None and comb_u_val is not None and comb_u_val > 0:
                tot_coord = f"{get_column_letter(c_tot + 1)}{r}"
                # Check hardcoded total
                if tot_formula_str is None and tot_cached_val is not None:
                    anomalies.append(
                        BoQFormulaAnomaly(
                            sheet_name=sname,
                            row_number=r,
                            cell_coord=tot_coord,
                            anomaly_type="HARDCODED_TOTAL",
                            message=(
                                f"Row {r} total at {tot_coord} ({tot_cached_val}) is a hardcoded number "
                                "instead of a live multiplication formula."
                            ),
                            cached_value=tot_cached_val,
                            expected_value=float(expected_dec),
                        )
                    )
                # Check mathematical mismatch
                if tot_cached_val is not None:
                    diff_dec = abs(expected_dec - _to_decimal(tot_cached_val))
                    if diff_dec > Decimal("1.0"):
                        anomalies.append(
                            BoQFormulaAnomaly(
                                sheet_name=sname,
                                row_number=r,
                                cell_coord=tot_coord,
                                anomaly_type="MATH_MISMATCH",
                                message=(
                                    f"Row {r} math mismatch at {tot_coord}: "
                                    f"Qty ({eff_qty}) * UnitPrice ({comb_u_val}) = {float(expected_dec):.2f}, "
                                    f"but cell value is {tot_cached_val:.2f}."
                                ),
                                formula_str=tot_formula_str,
                                cached_value=tot_cached_val,
                                expected_value=float(expected_dec),
                            )
                        )

            canon_unit, mult = normalize_engineering_unit(unit_val)
            canon_qty = round(eff_qty * mult, 4)

            item = BoQItem(
                sheet_name=sname,
                row_number=r,
                item_number=item_no_str,
                code=code_str,
                description=desc_val,
                quantity=eff_qty,
                raw_unit=unit_val,
                canonical_unit=canon_unit,
                unit_multiplier_to_canonical=mult,
                canonical_quantity=canon_qty,
                material_unit_price=mat_u_val,
                labor_unit_price=lab_u_val,
                combined_unit_price=comb_u_val,
                total_price=tot_cached_val if tot_cached_val is not None else float(expected_dec),
                has_live_formula=tot_formula_str is not None,
            )
            sheet_items.append(item)
            all_extracted_items.append(item)
            item_row_numbers.append(r)

        sheet_summaries.append(
            BoQSheetSummary(
                sheet_name=sname,
                layout_type=layout_type,  # type: ignore[arg-type]
                extracted_item_count=len(sheet_items),
                hidden_rows_skipped=hidden_rows_skipped,
                calculated_sheet_subtotal=round(float(calculated_subtotal), 2),
                reported_sheet_subtotal=round(reported_subtotal, 2)
                if reported_subtotal is not None
                else None,
            )
        )

    wb_formula.close()
    wb_values.close()

    # Apply keyword_filter and pagination (offset, limit) to protect LLM context window
    if keyword_filter:
        kw_re = re.compile(keyword_filter, re.IGNORECASE)
        matching_items = [
            it
            for it in all_extracted_items
            if kw_re.search(it.description)
            or kw_re.search(it.item_number)
            or kw_re.search(it.code)
        ]
    else:
        matching_items = all_extracted_items

    safe_offset = max(0, offset)
    safe_limit = max(1, limit)
    paged_items = matching_items[safe_offset : safe_offset + safe_limit]

    return BoQAuditReport(
        file_path=str(path_obj),
        visible_sheets_processed=visible_sheets,
        skipped_hidden_sheets=skipped_hidden_sheets,
        has_shared_formulas=has_shared,
        total_items_extracted=len(all_extracted_items),
        total_matching_items=len(matching_items),
        returned_items_count=len(paged_items),
        sheet_summaries=sheet_summaries,
        formula_anomalies=anomalies,
        items=paged_items,
    )
