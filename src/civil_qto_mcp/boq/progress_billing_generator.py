"""
Tool 21 implementation: generate_progress_certificate
Generates official Construction Measurement Sheets (Felmérési Napló) and Monthly Progress
Payment Certificates (Teljesítésigazolás / FIDIC IPC / VOB/B Abschlagsrechnung) from a
contracted BoQ workbook and/or structured progress entries, calculating previous, current,
and cumulative quantities/amounts, detecting >100% quantity overruns (Pótmunka), applying
advance payment amortization and retention deductions, and exporting a 2-sheet Excel workbook
with live formulas.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from civil_qto_mcp.boq.excel_auditor import inspect_and_audit_boq_xlsx
from civil_qto_mcp.models.boq_models import (
    ProgressCertificateLineResult,
    ProgressCertificateReport,
    ProgressItemEntryInput,
)


def _write_progress_certificate_excel(
    output_path: Path,
    report: ProgressCertificateReport,
    client_name: str,
    contractor_name: str,
) -> None:
    """Write a 2-sheet Excel workbook: '1_Teljesítésigazolás' and '2_Felmérési_Napló'."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()

    # -------------------------------------------------------------------------
    # SHEET 1: 1_Teljesítésigazolás
    # -------------------------------------------------------------------------
    ws1 = wb.active
    assert ws1 is not None
    ws1.title = "1_Teljesítésigazolás"

    hdr_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    sub_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    overrun_fill = PatternFill(start_color="FEF2F2", end_color="FEF2F2", fill_type="solid")
    pay_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")

    white_bold = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    dark_bold = Font(name="Calibri", size=10, bold=True, color="0F172A")
    green_bold = Font(name="Calibri", size=11, bold=True, color="166534")
    red_bold = Font(name="Calibri", size=10, bold=True, color="B91C1C")
    reg_font = Font(name="Calibri", size=10, color="1E293B")

    thin = Side(border_style="thin", color="CBD5E1")
    grid_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws1["A1"] = f"TELJESÍTÉSIGAZOLÁS MELLÉKLET – {report.project_name}"
    ws1["A1"].font = Font(name="Calibri", size=14, bold=True, color="0F172A")
    ws1["A2"] = (
        f"Bizonylat: {report.certificate_number} | Elszámolási időszak: {report.billing_period} | "
        f"Megrendelő: {client_name} | Vállalkozó: {contractor_name} | Pénznem: {report.currency}"
    )
    ws1["A2"].font = Font(name="Calibri", size=10, italic=True, color="475569")

    headers = [
        "Tétel kulcs",
        "Munkalap",
        "Megnevezés / Műszaki tartalom",
        "Me.",
        "Szerz. menny.",
        f"Szerz. egységár ({report.currency})",
        f"Szerz. összeg ({report.currency})",
        "Előző halm. menny.",
        "Tárgyidőszaki menny.",
        "Új halmozott menny.",
        "Készültség (%)",
        f"Előző halm. összeg ({report.currency})",
        f"Tárgyidőszaki összeg ({report.currency})",
        f"Új halmozott összeg ({report.currency})",
        f"Hátralévő maradvány ({report.currency})",
    ]
    for col_idx, text in enumerate(headers, start=1):
        c = ws1.cell(row=4, column=col_idx, value=text)
        c.fill = hdr_fill
        c.font = white_bold
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    start_data_row = 5
    curr_row = start_data_row
    for ln in report.lines:
        ws1.cell(row=curr_row, column=1, value=ln.item_key).font = dark_bold
        ws1.cell(row=curr_row, column=2, value=ln.sheet_name).font = reg_font
        ws1.cell(row=curr_row, column=3, value=ln.description).font = reg_font
        ws1.cell(row=curr_row, column=4, value=ln.unit).alignment = Alignment(horizontal="center")
        ws1.cell(row=curr_row, column=5, value=ln.contracted_quantity).number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=6, value=ln.contracted_unit_price).number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=7, value=f"=E{curr_row}*F{curr_row}").number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=8, value=ln.previous_cumulative_quantity).number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=9, value=ln.current_period_quantity).number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=10, value=f"=H{curr_row}+I{curr_row}").number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=11, value=f"=IF(E{curr_row}>0,J{curr_row}/E{curr_row},0)").number_format = "0.0%"
        ws1.cell(row=curr_row, column=12, value=f"=H{curr_row}*F{curr_row}").number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=13, value=f"=I{curr_row}*F{curr_row}").number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=14, value=f"=J{curr_row}*F{curr_row}").number_format = "#,##0.00"
        ws1.cell(row=curr_row, column=15, value=f"=MAX(0,G{curr_row}-N{curr_row})").number_format = "#,##0.00"

        is_over = ln.overrun_quantity > 0
        for col_i in range(1, 16):
            cell = ws1.cell(row=curr_row, column=col_i)
            cell.border = grid_border
            if is_over and col_i in (10, 11, 14):
                cell.fill = overrun_fill
                cell.font = red_bold

        curr_row += 1

    end_data_row = max(start_data_row, curr_row - 1)

    # Totals row
    ws1.cell(row=curr_row, column=3, value="ÖSSZESEN (Munkarésszel igazolt nettó érték)").font = dark_bold
    for col_i in range(1, 16):
        ws1.cell(row=curr_row, column=col_i).fill = sub_fill
        ws1.cell(row=curr_row, column=col_i).border = grid_border

    for sum_col in (7, 12, 13, 14, 15):
        col_let = get_column_letter(sum_col)
        sc = ws1.cell(row=curr_row, column=sum_col, value=f"=SUM({col_let}{start_data_row}:{col_let}{end_data_row})")
        sc.font = dark_bold
        sc.number_format = "#,##0.00"

    tot_row = curr_row
    curr_row += 2

    # Financial Deductions & Payable Summary Box
    fin_rows = [
        ("Tárgyidőszaki igazolt nettó munkaérték", f"=M{tot_row}", dark_bold, sub_fill),
        (
            f"(-) Előleg arányos elszámolása / levonása ({report.advance_deduction_percent:.1f}%)",
            -report.advance_deduction_amount,
            reg_font,
            sub_fill,
        ),
        (
            f"(-) Jóteljesítési visszatartás ({report.retention_percent:.1f}%)",
            -report.retention_deduction_amount,
            reg_font,
            sub_fill,
        ),
        (
            "TÁRGYIDŐSZAKI SZÁMLÁZHATÓ NETTÓ ÖSSZEG",
            report.net_invoicable_amount,
            green_bold,
            pay_fill,
        ),
        (
            f"ÁFA ({report.vat_percent:.1f}%)",
            report.vat_amount,
            reg_font,
            sub_fill,
        ),
        (
            "TÁRGYIDŐSZAKI BRUTTÓ FIZETENDŐ ÖSSZEG",
            report.gross_invoicable_amount,
            dark_bold,
            sub_fill,
        ),
    ]
    for label, val, fnt, fl in fin_rows:
        lbl_c = ws1.cell(row=curr_row, column=3, value=label)
        lbl_c.font = fnt
        lbl_c.fill = fl
        lbl_c.border = grid_border

        val_c = ws1.cell(row=curr_row, column=13, value=val)
        val_c.font = fnt
        val_c.fill = fl
        val_c.border = grid_border
        val_c.number_format = "#,##0.00"
        curr_row += 1

    # Signature lines
    curr_row += 2
    ws1.cell(row=curr_row, column=3, value="............................................................").font = reg_font
    ws1.cell(row=curr_row, column=12, value="............................................................").font = reg_font
    curr_row += 1
    ws1.cell(row=curr_row, column=3, value=f"Felelős Műszaki Vezető ({contractor_name})").font = dark_bold
    ws1.cell(row=curr_row, column=12, value=f"Műszaki Ellenőr / Megrendelő ({client_name})").font = dark_bold

    col_widths_1 = [14, 16, 42, 8, 14, 16, 18, 14, 14, 14, 12, 18, 18, 18, 18]
    for idx, w in enumerate(col_widths_1, start=1):
        ws1.column_dimensions[get_column_letter(idx)].width = w

    # -------------------------------------------------------------------------
    # SHEET 2: 2_Felmérési_Napló
    # -------------------------------------------------------------------------
    ws2 = wb.create_sheet(title="2_Felmérési_Napló")
    ws2["A1"] = f"FELMÉRÉSI NAPLÓ – {report.project_name} ({report.certificate_number})"
    ws2["A1"].font = Font(name="Calibri", size=14, bold=True, color="0F172A")

    m_headers = [
        "Tétel kulcs",
        "Megnevezés",
        "Me.",
        "Szerz. menny.",
        "Előző halm. menny.",
        "Tárgyidőszaki menny.",
        "Új halmozott menny.",
        "Felmérési számítás / Geodéziai igazolás (Képlet / Méretsor)",
        "Többlet / Pótmunka menny.",
    ]
    for col_idx, text in enumerate(m_headers, start=1):
        c = ws2.cell(row=3, column=col_idx, value=text)
        c.fill = hdr_fill
        c.font = white_bold
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    r2 = 4
    for ln in report.lines:
        ws2.cell(row=r2, column=1, value=ln.item_key).font = dark_bold
        ws2.cell(row=r2, column=2, value=ln.description).font = reg_font
        ws2.cell(row=r2, column=3, value=ln.unit).alignment = Alignment(horizontal="center")
        ws2.cell(row=r2, column=4, value=ln.contracted_quantity).number_format = "#,##0.00"
        ws2.cell(row=r2, column=5, value=ln.previous_cumulative_quantity).number_format = "#,##0.00"
        ws2.cell(row=r2, column=6, value=ln.current_period_quantity).number_format = "#,##0.00"
        ws2.cell(row=r2, column=7, value=ln.new_cumulative_quantity).number_format = "#,##0.00"
        ws2.cell(
            row=r2,
            column=8,
            value=ln.measurement_proof_formula
            or f"Felmért mennyiség: {ln.current_period_quantity:,.2f} {ln.unit} ({ln.completion_percent:.1f}%)",
        ).font = reg_font
        ov_c = ws2.cell(row=r2, column=9, value=ln.overrun_quantity)
        ov_c.number_format = "#,##0.00"
        if ln.overrun_quantity > 0:
            ov_c.fill = overrun_fill
            ov_c.font = red_bold

        for col_i in range(1, 10):
            ws2.cell(row=r2, column=col_i).border = grid_border
        r2 += 1

    col_widths_2 = [14, 42, 8, 14, 14, 14, 14, 48, 18]
    for idx, w in enumerate(col_widths_2, start=1):
        ws2.column_dimensions[get_column_letter(idx)].width = w

    wb.save(output_path)
    wb.close()


def generate_progress_certificate(
    progress_entries: list[ProgressItemEntryInput | dict] | None = None,
    contract_boq_xlsx_path: str | Path | None = None,
    sheet_names: list[str] | None = None,
    default_completion_percent: float | None = None,
    project_name: str = "Mélyépítési és Útépítési Projekt",
    certificate_number: str = "1. Részszámla (1. sz. Teljesítésigazolás)",
    billing_period: str = "2026. november",
    client_name: str = "Megrendelő Kft.",
    contractor_name: str = "Térburkolat Kft.",
    currency: str = "HUF",
    advance_deduction_percent: float = 0.0,
    retention_percent: float = 5.0,
    vat_percent: float = 27.0,
    output_xlsx_path: str | Path | None = None,
) -> ProgressCertificateReport:
    """
    Generate a Construction Measurement Sheet (Felmérési Napló) and Monthly Progress Payment
    Certificate (Teljesítésigazolás) from a contracted BoQ Excel file and/or structured progress
    entries, computing previous, current, and cumulative quantities and amounts, flagging >100%
    overruns (Pótmunka), applying advance repayment and retention deductions, and saving a
    formatted 2-sheet Excel workbook.
    """
    warnings: list[str] = []

    parsed_entries: list[ProgressItemEntryInput] = [
        e if isinstance(e, ProgressItemEntryInput) else ProgressItemEntryInput.model_validate(e)
        for e in (progress_entries or [])
    ]

    # Map entries by multiple candidate keys (exact item_key, row number, item code)
    entry_map: dict[str, ProgressItemEntryInput] = {}
    for e in parsed_entries:
        entry_map[e.item_key.strip()] = e
        entry_map[e.item_key.strip().lower()] = e

    lines: list[ProgressCertificateLineResult] = []

    if contract_boq_xlsx_path is not None:
        audit = inspect_and_audit_boq_xlsx(
            file_path=contract_boq_xlsx_path,
            sheet_names=sheet_names,
            limit=5000,
        )
        for item in audit.items:
            if item.quantity <= 0:
                continue
            key_full = f"{item.sheet_name}::{item.row_number}"
            key_row = str(item.row_number)
            key_code = (item.item_code or "").strip()

            matched_entry = (
                entry_map.get(key_full)
                or entry_map.get(key_full.lower())
                or (entry_map.get(key_code) if key_code else None)
                or entry_map.get(key_row)
            )

            prev_q = matched_entry.previous_cumulative_quantity if matched_entry else 0.0
            if matched_entry and matched_entry.current_period_quantity is not None:
                curr_q = matched_entry.current_period_quantity
            elif matched_entry and matched_entry.target_cumulative_percent is not None:
                target_cum_q = item.quantity * (matched_entry.target_cumulative_percent / 100.0)
                curr_q = max(0.0, target_cum_q - prev_q)
            elif default_completion_percent is not None:
                target_cum_q = item.quantity * (default_completion_percent / 100.0)
                curr_q = max(0.0, target_cum_q - prev_q)
            else:
                curr_q = 0.0

            new_cum_q = prev_q + curr_q
            unit_p = float(item.unit_price)
            cont_q = float(item.quantity)
            cont_tot = cont_q * unit_p
            prev_amt = prev_q * unit_p
            curr_amt = curr_q * unit_p
            new_cum_amt = new_cum_q * unit_p
            comp_pct = (new_cum_q / cont_q * 100.0) if cont_q > 0 else 0.0
            rem_amt = max(0.0, cont_tot - new_cum_amt)

            over_q = max(0.0, new_cum_q - cont_q)
            over_amt = over_q * unit_p
            if over_q > 1e-6:
                warnings.append(
                    f"Item '{key_full}' ({item.description[:50]}): cumulative quantity ({new_cum_q:.2f} {item.unit}) "
                    f"exceeds contracted quantity ({cont_q:.2f} {item.unit}) by +{over_q:.2f} {item.unit} "
                    f"(+{over_amt:,.2f} {currency}) -> requires Variation Order (Pótmunka)!"
                )

            lines.append(
                ProgressCertificateLineResult(
                    item_key=key_code or key_full,
                    sheet_name=item.sheet_name,
                    row_number=item.row_number,
                    description=item.description,
                    unit=item.unit,
                    contracted_quantity=round(cont_q, 3),
                    contracted_unit_price=round(unit_p, 2),
                    contracted_total_amount=round(cont_tot, 2),
                    previous_cumulative_quantity=round(prev_q, 3),
                    current_period_quantity=round(curr_q, 3),
                    new_cumulative_quantity=round(new_cum_q, 3),
                    completion_percent=round(comp_pct, 2),
                    previous_cumulative_amount=round(prev_amt, 2),
                    current_period_amount=round(curr_amt, 2),
                    new_cumulative_amount=round(new_cum_amt, 2),
                    remaining_contract_amount=round(rem_amt, 2),
                    overrun_quantity=round(over_q, 3),
                    overrun_amount=round(over_amt, 2),
                    measurement_proof_formula=matched_entry.measurement_proof_formula if matched_entry else "",
                )
            )
    else:
        for idx, e in enumerate(parsed_entries, start=1):
            if e.contracted_quantity is None or e.contracted_unit_price is None:
                raise ValueError(
                    f"Progress entry '{e.item_key}' must specify `contracted_quantity` and `contracted_unit_price` "
                    "when `contract_boq_xlsx_path` is not provided."
                )
            cont_q = e.contracted_quantity
            unit_p = e.contracted_unit_price
            prev_q = e.previous_cumulative_quantity
            if e.current_period_quantity is not None:
                curr_q = e.current_period_quantity
            elif e.target_cumulative_percent is not None:
                curr_q = max(0.0, cont_q * (e.target_cumulative_percent / 100.0) - prev_q)
            elif default_completion_percent is not None:
                curr_q = max(0.0, cont_q * (default_completion_percent / 100.0) - prev_q)
            else:
                curr_q = 0.0

            new_cum_q = prev_q + curr_q
            cont_tot = cont_q * unit_p
            prev_amt = prev_q * unit_p
            curr_amt = curr_q * unit_p
            new_cum_amt = new_cum_q * unit_p
            comp_pct = (new_cum_q / cont_q * 100.0) if cont_q > 0 else 0.0
            rem_amt = max(0.0, cont_tot - new_cum_amt)

            over_q = max(0.0, new_cum_q - cont_q)
            over_amt = over_q * unit_p
            if over_q > 1e-6:
                warnings.append(
                    f"Item '{e.item_key}': cumulative quantity ({new_cum_q:.2f}) exceeds contracted quantity "
                    f"({cont_q:.2f}) by +{over_q:.2f} (+{over_amt:,.2f} {currency}) -> requires Variation Order (Pótmunka)!"
                )

            lines.append(
                ProgressCertificateLineResult(
                    item_key=e.item_key,
                    sheet_name="Szerződés",
                    row_number=idx,
                    description=e.description or f"Tétel {e.item_key}",
                    unit=e.unit or "m2",
                    contracted_quantity=round(cont_q, 3),
                    contracted_unit_price=round(unit_p, 2),
                    contracted_total_amount=round(cont_tot, 2),
                    previous_cumulative_quantity=round(prev_q, 3),
                    current_period_quantity=round(curr_q, 3),
                    new_cumulative_quantity=round(new_cum_q, 3),
                    completion_percent=round(comp_pct, 2),
                    previous_cumulative_amount=round(prev_amt, 2),
                    current_period_amount=round(curr_amt, 2),
                    new_cumulative_amount=round(new_cum_amt, 2),
                    remaining_contract_amount=round(rem_amt, 2),
                    overrun_quantity=round(over_q, 3),
                    overrun_amount=round(over_amt, 2),
                    measurement_proof_formula=e.measurement_proof_formula,
                )
            )

    if not lines:
        raise ValueError("No contracted items found to generate progress certificate.")

    tot_contract = sum(ln.contracted_total_amount for ln in lines)
    tot_prev = sum(ln.previous_cumulative_amount for ln in lines)
    tot_curr_work = sum(ln.current_period_amount for ln in lines)
    tot_new_cum = sum(ln.new_cumulative_amount for ln in lines)
    tot_rem = sum(ln.remaining_contract_amount for ln in lines)
    tot_over_amt = sum(ln.overrun_amount for ln in lines)
    overrun_cnt = sum(1 for ln in lines if ln.overrun_quantity > 0)
    overall_pct = (tot_new_cum / tot_contract * 100.0) if tot_contract > 0 else 0.0

    adv_ded_amt = tot_curr_work * (advance_deduction_percent / 100.0)
    ret_ded_amt = tot_curr_work * (retention_percent / 100.0)
    net_invoicable = tot_curr_work - adv_ded_amt - ret_ded_amt
    vat_amt = net_invoicable * (vat_percent / 100.0)
    gross_invoicable = net_invoicable + vat_amt

    report = ProgressCertificateReport(
        project_name=project_name,
        certificate_number=certificate_number,
        billing_period=billing_period,
        currency=currency,
        items_processed_count=len(lines),
        overrun_items_count=overrun_cnt,
        total_contract_amount=round(tot_contract, 2),
        previous_cumulative_amount=round(tot_prev, 2),
        current_period_gross_work_amount=round(tot_curr_work, 2),
        new_cumulative_amount=round(tot_new_cum, 2),
        overall_completion_percent=round(overall_pct, 2),
        remaining_contract_balance=round(tot_rem, 2),
        total_overrun_variation_amount=round(tot_over_amt, 2),
        advance_deduction_percent=advance_deduction_percent,
        advance_deduction_amount=round(adv_ded_amt, 2),
        retention_percent=retention_percent,
        retention_deduction_amount=round(ret_ded_amt, 2),
        net_invoicable_amount=round(net_invoicable, 2),
        vat_percent=vat_percent,
        vat_amount=round(vat_amt, 2),
        gross_invoicable_amount=round(gross_invoicable, 2),
        output_xlsx_path=None,
        lines=lines,
        warnings=warnings,
    )

    if output_xlsx_path is not None:
        out_p = Path(output_xlsx_path)
        _write_progress_certificate_excel(
            output_path=out_p,
            report=report,
            client_name=client_name,
            contractor_name=contractor_name,
        )
        report.output_xlsx_path = str(out_p.resolve())

    return report
