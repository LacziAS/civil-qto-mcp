"""
Tool 19 implementation: compare_supplier_quotes_and_build_bid_tab
Normalizes multi-supplier quotes (structured inputs, .xlsx, or .pdf), calculates truckload
freight, crane unloading, and non-refundable pallet/packaging surcharges to determine
True Landed Unit Prices (Helyszínre lerakott fajlagos egységár), scans for commercial
fine-print traps, generates a formatted Excel Bid Tabulation (Ártükör.xlsx), and outputs
ready-to-apply BoQRowPricingInput records.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from civil_qto_mcp.models.boq_models import (
    BidTabItemComparisonRow,
    BidTabTargetItemInput,
    BoQRowPricingInput,
    SupplierBidTabulationReport,
    SupplierPackageSummary,
    SupplierQuoteInput,
    SupplierQuoteLineInput,
)

# Multi-language (HU / DE / EN) commercial fine-print trap detectors
FINE_PRINT_TRAPS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(lerakod[aá]st\s+nem|daruz[aá]st\s+nem|unloading\s+not\s+included|ohne\s+entladung)",
            re.IGNORECASE,
        ),
        "UNLOADING_EXCLUDED: Quote excludes site unloading/crane costs (helyszíni lerakodást/daruzást nem tartalmaz).",
    ),
    (
        re.compile(
            r"(el[oő]re\s+utal[aá]s|100\s*%\s*el[oő]leg|advance\s+payment|prepayment|vorkasse)",
            re.IGNORECASE,
        ),
        "ADVANCE_PAYMENT_REQUIRED: Supplier requires advance payment / prepayment (előre utalás / előleg).",
    ),
    (
        re.compile(
            r"(bontott\s+raklap|sz[aá]lkad[ií]j|komissi[oó]|broken\s+pallet|picking\s+fee)",
            re.IGNORECASE,
        ),
        "BROKEN_PALLET_SURCHARGE: Surcharge applies for non-full pallets (bontott raklap / szálkadíj).",
    ),
    (
        re.compile(
            r"(raklap\s*haszn[aá]lati|kop[aá]si\s+d[ií]j|csomagol[aá]si\s+d[ií]j|pallet\s+usage|palettengeb[uü]hr)",
            re.IGNORECASE,
        ),
        "PALLET_USAGE_FEE: Non-refundable pallet usage/packaging fee mentioned in terms (raklaphasználati / csomagolási díj).",
    ),
    (
        re.compile(
            r"(napi\s+[aá]r|[aá]rv[aá]ltoztat[aá]s\s+jog[aá]t|subject\s+to\s+change|freibleibend)",
            re.IGNORECASE,
        ),
        "UNFIXED_PRICING: Prices are not locked / subject to change without notice (árváltoztatás joga fenntartva).",
    ),
    (
        re.compile(
            r"([aá]ll[aá]sid[oő]|demurrage|waiting\s+time|wartezeit)",
            re.IGNORECASE,
        ),
        "DEMURRAGE_PENALTY: Strict truck waiting-time / demurrage charges apply (állásidő felár).",
    ),
]


def _estimate_unit_weight_and_pallet_capacity(
    item: BidTabTargetItemInput,
) -> tuple[float, float | None]:
    """
    Estimate (unit_weight_kg, units_per_pallet) when not explicitly provided on BidTabTargetItemInput.
    """
    desc = item.description.lower()
    unit = item.unit.lower().strip()

    weight_kg = item.unit_weight_kg
    per_pallet = item.units_per_pallet

    if weight_kg is None:
        if "térkő" in desc or "paving" in desc or "pflaster" in desc:
            if "10" in desc:
                weight_kg = 225.0
            elif "6" in desc:
                weight_kg = 135.0
            else:
                weight_kg = 180.0  # Default 8 cm paving block
        elif "kiemelt" in desc or "útszegély" in desc or "k-szegély" in desc or "15x30" in desc or "20x25" in desc:
            weight_kg = 110.0
        elif "kerti szegély" in desc or "süllyesztett" in desc or "10x25" in desc or "5x20" in desc:
            weight_kg = 45.0
        elif "folyóka" in desc or "mederlap" in desc:
            weight_kg = 75.0
        elif unit in ("t", "tonna", "tonne"):
            weight_kg = 1000.0
        elif unit in ("m3", "m³"):
            weight_kg = 1800.0
        elif "akna" in desc or "manhole" in desc:
            weight_kg = 650.0
        else:
            weight_kg = 25.0

    if per_pallet is None:
        if "térkő" in desc or "paving" in desc or "pflaster" in desc:
            if "10" in desc:
                per_pallet = 8.0
            elif "6" in desc:
                per_pallet = 10.8
            else:
                per_pallet = 9.6
        elif "kiemelt" in desc or "útszegély" in desc or "k-szegély" in desc:
            per_pallet = 12.0
        elif "kerti szegély" in desc or "5x20" in desc:
            per_pallet = 42.0
        elif "mederlap" in desc or "folyóka" in desc:
            per_pallet = 20.0

    return weight_kg, per_pallet


def _parse_quote_file_if_needed(
    quote: SupplierQuoteInput,
    target_items: list[BidTabTargetItemInput],
) -> tuple[list[SupplierQuoteLineInput], str]:
    """
    If `quote.quote_file_path` is provided (.xlsx or .pdf) and `quote.lines` is empty,
    extract unit prices matching target items and return (extracted_lines, extracted_notes).
    """
    existing_lines = list(quote.lines)
    notes_parts = [quote.notes_and_conditions] if quote.notes_and_conditions else []

    if not quote.quote_file_path:
        return existing_lines, "\n".join(notes_parts)

    fpath = Path(quote.quote_file_path)
    if not fpath.exists():
        return existing_lines, "\n".join(notes_parts)

    suffix = fpath.suffix.lower()
    raw_text_rows: list[str] = []

    if suffix in (".xlsx", ".xlsm"):
        wb = openpyxl.load_workbook(fpath, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    raw_text_rows.append(" | ".join(cells))
        wb.close()
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]

            reader = PdfReader(str(fpath))
            for page in reader.pages:
                txt = page.extract_text() or ""
                for line in txt.splitlines():
                    if line.strip():
                        raw_text_rows.append(line.strip())
        except ImportError:
            raw_bytes = fpath.read_bytes()
            text_fallback = raw_bytes.decode("latin-1", errors="ignore")
            for line in text_fallback.splitlines():
                if line.strip():
                    raw_text_rows.append(line.strip())
    elif suffix in (".txt", ".csv", ".md"):
        for line in fpath.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip():
                raw_text_rows.append(line.strip())

    full_text = "\n".join(raw_text_rows)
    notes_parts.append(full_text)

    if not existing_lines:
        matched_ids: set[str] = set()
        num_regex = re.compile(r"(?<!\d)(\d{1,3}(?:[\s\u00a0.]?\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\d)")
        for item in target_items:
            item_id_low = item.item_id.lower()
            desc_tokens = [
                w.lower()
                for w in re.split(r"\W+", item.description)
                if len(w) >= 4
            ]
            for row_str in raw_text_rows:
                row_low = row_str.lower()
                id_match = item_id_low in row_low
                tok_matches = sum(1 for t in desc_tokens if t in row_low)
                if id_match or (desc_tokens and tok_matches >= max(1, len(desc_tokens) // 2)):
                    # Extract candidate unit prices from the row
                    candidates: list[float] = []
                    for m in num_regex.finditer(row_str):
                        raw_n = m.group(1).replace(" ", "").replace("\u00a0", "")
                        if "," in raw_n and "." in raw_n:
                            raw_n = raw_n.replace(".", "").replace(",", ".")
                        elif "," in raw_n:
                            raw_n = raw_n.replace(",", ".")
                        try:
                            val = float(raw_n)
                            if val > 10.0 and abs(val - item.required_quantity) > 0.01:
                                candidates.append(val)
                        except ValueError:
                            continue
                    if candidates and item.item_id not in matched_ids:
                        existing_lines.append(
                            SupplierQuoteLineInput(
                                item_id=item.item_id,
                                quoted_description=row_str[:120],
                                quoted_unit_price=candidates[0],
                            )
                        )
                        matched_ids.add(item.item_id)
                        break

    return existing_lines, "\n".join(notes_parts)


def _scan_fine_print_warnings(text: str, quote: SupplierQuoteInput) -> list[str]:
    """Scan supplier notes/conditions and quote terms for commercial traps."""
    warnings: list[str] = []
    combined = f"{text}\n{quote.payment_terms}\n{quote.notes_and_conditions}"
    for pattern, message in FINE_PRINT_TRAPS:
        if pattern.search(combined) and message not in warnings:
            warnings.append(message)
    return warnings


def _write_bid_tab_excel(
    output_path: Path,
    package_name: str,
    currency: str,
    target_items: list[BidTabTargetItemInput],
    supplier_summaries: list[SupplierPackageSummary],
    item_comparisons: list[BidTabItemComparisonRow],
    best_single_supplier: str | None,
) -> None:
    """Generate a formatted, color-coded Excel Bid Tabulation (Ártükör.xlsx)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Ártükör (Bid Tab)"

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    subhdr_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
    winner_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    summary_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

    white_bold = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    dark_bold = Font(name="Calibri", size=10, bold=True, color="0F172A")
    winner_font = Font(name="Calibri", size=10, bold=True, color="166534")
    regular_font = Font(name="Calibri", size=10, color="1E293B")
    warn_font = Font(name="Calibri", size=9, italic=True, color="B91C1C")

    thin_side = Side(border_style="thin", color="CBD5E1")
    grid_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    # Title banner
    ws["A1"] = f"BESZÁLLÍTÓI ÁRTÜKÖR (BID TABULATION) – {package_name}"
    ws["A1"].font = Font(name="Calibri", size=14, bold=True, color="0F172A")
    ws["A2"] = (
        f"Pénznem: {currency} | Tételek száma: {len(target_items)} | "
        f"Versenyeztetett ajánlatok: {len(supplier_summaries)} | "
        f"Ajánlott csomag-nyertes: {best_single_supplier or 'N/A'}"
    )
    ws["A2"].font = Font(name="Calibri", size=10, italic=True, color="475569")

    # Table headers (Rows 4 & 5)
    base_headers = ["Tétel ID", "Megnevezés / Műszaki tartalom", "Me.", "Mennyiség"]
    for col_idx, text in enumerate(base_headers, start=1):
        ws.merge_cells(start_row=4, start_column=col_idx, end_row=5, end_column=col_idx)
        c = ws.cell(row=4, column=col_idx, value=text)
        c.fill = header_fill
        c.font = white_bold
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for s_idx, sup in enumerate(supplier_summaries):
        start_col = 5 + s_idx * 3
        ws.merge_cells(start_row=4, start_column=start_col, end_row=4, end_column=start_col + 2)
        top_cell = ws.cell(row=4, column=start_col, value=f"{sup.supplier_name} ({sup.quote_reference or 'Ajánlat'})")
        top_cell.fill = header_fill
        top_cell.font = white_bold
        top_cell.alignment = Alignment(horizontal="center", vertical="center")

        sub_labels = [
            f"Ajánlati ár ({currency})",
            f"Lerakott fajlagos ár ({currency})",
            f"Lerakott összesen ({currency})",
        ]
        for offset, lbl in enumerate(sub_labels):
            sc = ws.cell(row=5, column=start_col + offset, value=lbl)
            sc.fill = subhdr_fill
            sc.font = Font(name="Calibri", size=9, bold=True, color="FFFFFF")
            sc.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Item comparison rows
    curr_row = 6
    for comp in item_comparisons:
        ws.cell(row=curr_row, column=1, value=comp.item_id).font = dark_bold
        ws.cell(row=curr_row, column=2, value=comp.description).font = regular_font
        ws.cell(row=curr_row, column=3, value=comp.unit).alignment = Alignment(horizontal="center")
        q_cell = ws.cell(row=curr_row, column=4, value=comp.required_quantity)
        q_cell.number_format = "#,##0.00"

        for s_idx, sup in enumerate(supplier_summaries):
            start_col = 5 + s_idx * 3
            sname = sup.supplier_name
            q_price = comp.supplier_quoted_unit_prices.get(sname)
            l_price = comp.supplier_landed_unit_prices.get(sname)
            l_total = comp.supplier_landed_totals.get(sname)

            c_q = ws.cell(row=curr_row, column=start_col, value=q_price if q_price is not None else "-")
            c_l = ws.cell(row=curr_row, column=start_col + 1, value=l_price if l_price is not None else "-")
            c_t = ws.cell(row=curr_row, column=start_col + 2, value=l_total if l_total is not None else "-")

            for cell in (c_q, c_l, c_t):
                cell.border = grid_border
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "#,##0.00"
                    cell.font = regular_font

            if sname == comp.lowest_landed_supplier and l_price is not None:
                c_l.fill = winner_fill
                c_l.font = winner_font
                c_t.fill = winner_fill
                c_t.font = winner_font

        for col_i in range(1, 5):
            ws.cell(row=curr_row, column=col_i).border = grid_border

        curr_row += 1

    # Summary block
    curr_row += 1
    summary_rows_spec = [
        ("Telephelyi anyagár részösszeg (Ex-Works)", lambda s: s.ex_works_subtotal),
        ("Engedmény összege (-)", lambda s: -s.discount_amount),
        ("Nettó anyagár engedmény után", lambda s: s.net_material_subtotal),
        ("Fuvardíj összesen (Fuvar / Fordulók)", lambda s: s.freight_cost_total),
        ("Önrakodós daruzás / Lerakodás díja", lambda s: s.unloading_cost_total),
        ("Raklaphasználati / Csomagolási díj", lambda s: s.pallet_and_packaging_cost_total),
        ("HELYSZÍNRE LERAKOTT CSOMAGÁR ÖSSZESEN", lambda s: s.total_landed_package_cost),
        ("Fizetési feltétel", lambda s: s.payment_terms or "-"),
        ("Apróbetűs figyelmeztetések", lambda s: "; ".join(s.fine_print_warnings) if s.fine_print_warnings else "Nincs"),
    ]

    for label, getter in summary_rows_spec:
        is_grand = "CSOMAGÁR ÖSSZESEN" in label
        ws.cell(row=curr_row, column=2, value=label).font = dark_bold if is_grand else regular_font
        ws.cell(row=curr_row, column=2).fill = summary_fill

        for s_idx, sup in enumerate(supplier_summaries):
            start_col = 5 + s_idx * 3
            val = getter(sup)
            target_cell = ws.cell(row=curr_row, column=start_col + 2, value=val)
            target_cell.fill = summary_fill
            target_cell.border = grid_border
            if isinstance(val, (int, float)):
                target_cell.number_format = "#,##0.00"
                target_cell.font = dark_bold if is_grand else regular_font
                if is_grand and sup.supplier_name == best_single_supplier:
                    target_cell.fill = winner_fill
                    target_cell.font = winner_font
            else:
                target_cell.font = warn_font if "figyelmeztetés" in label.lower() and val != "Nincs" else regular_font

        curr_row += 1

    # Auto-adjust column widths
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 8
    ws.column_dimensions["D"].width = 14
    total_cols = 4 + len(supplier_summaries) * 3
    for col_idx in range(5, total_cols + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 18

    wb.save(output_path)
    wb.close()


def compare_supplier_quotes_and_build_bid_tab(
    target_items: list[BidTabTargetItemInput | dict],
    supplier_quotes: list[SupplierQuoteInput | dict],
    package_name: str = "Térburkolat és Szegély Beszerzési Csomag",
    currency: str = "HUF",
    output_xlsx_path: str | Path | None = None,
    contractor_markup_factor: float = 1.0,
    round_step: float = 10.0,
    award_strategy: Literal["BEST_SINGLE_SUPPLIER", "CHERRY_PICK_LOWEST"] = "BEST_SINGLE_SUPPLIER",
) -> SupplierBidTabulationReport:
    """
    Compare multiple supplier quotes (.xlsx, .pdf, or structured lines) against required target items,
    compute total weight, truckloads, pallets, freight, crane unloading, and pallet fees, allocate
    surcharges into True Landed Unit Prices, detect fine-print commercial traps, write a formatted
    Excel Bid Tabulation (`Ártükör.xlsx`), and produce ready-to-apply `BoQRowPricingInput` records.
    """
    parsed_targets: list[BidTabTargetItemInput] = [
        t if isinstance(t, BidTabTargetItemInput) else BidTabTargetItemInput.model_validate(t)
        for t in target_items
    ]
    parsed_quotes: list[SupplierQuoteInput] = [
        q if isinstance(q, SupplierQuoteInput) else SupplierQuoteInput.model_validate(q)
        for q in supplier_quotes
    ]

    if not parsed_targets:
        raise ValueError("target_items must contain at least one required item.")
    if not parsed_quotes:
        raise ValueError("supplier_quotes must contain at least one supplier quote.")

    warnings: list[str] = []

    # Pre-calculate weight and pallet count per target item
    item_weight_tonnes: dict[str, float] = {}
    item_pallets: dict[str, int] = {}
    total_pkg_weight_t = 0.0
    total_pkg_pallets = 0

    for item in parsed_targets:
        w_kg, per_pal = _estimate_unit_weight_and_pallet_capacity(item)
        wt_t = (item.required_quantity * w_kg) / 1000.0
        item_weight_tonnes[item.item_id] = wt_t
        total_pkg_weight_t += wt_t

        if per_pal and per_pal > 0:
            pals = math.ceil(item.required_quantity / per_pal)
        else:
            pals = 0
        item_pallets[item.item_id] = pals
        total_pkg_pallets += pals

    supplier_summaries: list[SupplierPackageSummary] = []
    # Map: supplier_name -> {item_id -> (quoted_unit_price, landed_unit_price, landed_total)}
    supplier_item_metrics: dict[str, dict[str, tuple[float, float, float]]] = {}

    for quote in parsed_quotes:
        lines, extracted_notes = _parse_quote_file_if_needed(quote, parsed_targets)
        line_map: dict[str, SupplierQuoteLineInput] = {ln.item_id: ln for ln in lines}

        fine_print_warns = _scan_fine_print_warnings(extracted_notes, quote)
        for fw in fine_print_warns:
            warnings.append(f"[{quote.supplier_name}] {fw}")

        quoted_count = 0
        sup_weight_t = 0.0
        sup_pallets = 0
        ex_works_sub = 0.0
        disc_mult = 1.0 - (quote.global_discount_percent / 100.0)

        net_item_subtotals: dict[str, float] = {}
        item_spec_surcharges: dict[str, float] = {}

        for item in parsed_targets:
            ln = line_map.get(item.item_id)
            if ln is None:
                continue
            quoted_count += 1
            sup_weight_t += item_weight_tonnes[item.item_id]
            sup_pallets += item_pallets[item.item_id]

            raw_line_tot = item.required_quantity * ln.quoted_unit_price
            ex_works_sub += raw_line_tot
            net_line_tot = raw_line_tot * disc_mult
            net_item_subtotals[item.item_id] = net_line_tot
            item_spec_surcharges[item.item_id] = item.required_quantity * ln.item_specific_surcharge_per_unit

        net_mat_sub = ex_works_sub * disc_mult
        disc_amount = ex_works_sub - net_mat_sub

        est_trucks = math.ceil(sup_weight_t / quote.truck_capacity_tonnes) if sup_weight_t > 0 else 0

        if quote.freight_mode == "INCLUDED":
            freight_tot = 0.0
        elif quote.freight_mode == "LUMP_SUM":
            freight_tot = quote.freight_rate
        elif quote.freight_mode == "PER_TRUCKLOAD":
            freight_tot = est_trucks * quote.freight_rate
        elif quote.freight_mode == "PER_TONNE":
            freight_tot = sup_weight_t * quote.freight_rate
        else:
            freight_tot = 0.0

        unloading_tot = sup_pallets * quote.unloading_fee_per_pallet
        pallet_fee_tot = sup_pallets * quote.non_refundable_pallet_fee_per_pallet
        shared_pool = freight_tot + unloading_tot + pallet_fee_tot
        total_surch = shared_pool + sum(item_spec_surcharges.values())
        landed_pkg_total = net_mat_sub + total_surch

        coverage_pct = (quoted_count / len(parsed_targets)) * 100.0 if parsed_targets else 0.0
        surcharge_ratio_pct = (total_surch / net_mat_sub * 100.0) if net_mat_sub > 0 else 0.0

        # Allocate shared surcharge pool proportionally across quoted items
        item_metrics: dict[str, tuple[float, float, float]] = {}
        for item in parsed_targets:
            ln = line_map.get(item.item_id)
            if ln is None:
                continue
            net_line = net_item_subtotals[item.item_id]
            share = (net_line / net_mat_sub) if net_mat_sub > 0 else (1.0 / max(1, quoted_count))
            allocated_surcharge = shared_pool * share + item_spec_surcharges[item.item_id]
            landed_line_total = net_line + allocated_surcharge
            landed_unit = landed_line_total / item.required_quantity
            item_metrics[item.item_id] = (
                round(ln.quoted_unit_price, 2),
                round(landed_unit, 2),
                round(landed_line_total, 2),
            )

        supplier_item_metrics[quote.supplier_name] = item_metrics

        supplier_summaries.append(
            SupplierPackageSummary(
                supplier_name=quote.supplier_name,
                quote_reference=quote.quote_reference,
                quoted_items_count=quoted_count,
                coverage_percent=round(coverage_pct, 1),
                total_weight_tonnes=round(sup_weight_t, 2),
                estimated_truckloads=est_trucks,
                estimated_pallets=sup_pallets,
                ex_works_subtotal=round(ex_works_sub, 2),
                discount_amount=round(disc_amount, 2),
                net_material_subtotal=round(net_mat_sub, 2),
                freight_cost_total=round(freight_tot, 2),
                unloading_cost_total=round(unloading_tot, 2),
                pallet_and_packaging_cost_total=round(pallet_fee_tot, 2),
                total_surcharges=round(total_surch, 2),
                surcharge_ratio_percent=round(surcharge_ratio_pct, 2),
                total_landed_package_cost=round(landed_pkg_total, 2),
                payment_terms=quote.payment_terms,
                delivery_lead_time_days=quote.delivery_lead_time_days,
                fine_print_warnings=fine_print_warns,
            )
        )

    # Determine best single-package supplier (100% coverage preferred)
    full_cov_suppliers = [s for s in supplier_summaries if s.quoted_items_count == len(parsed_targets)]
    ranked_suppliers = sorted(
        full_cov_suppliers if full_cov_suppliers else supplier_summaries,
        key=lambda s: s.total_landed_package_cost,
    )
    best_single = ranked_suppliers[0].supplier_name if ranked_suppliers else None
    best_single_tot = ranked_suppliers[0].total_landed_package_cost if ranked_suppliers else None
    second_best_tot = ranked_suppliers[1].total_landed_package_cost if len(ranked_suppliers) >= 2 else None
    savings_vs_second = (
        round(second_best_tot - best_single_tot, 2)
        if (best_single_tot is not None and second_best_tot is not None)
        else 0.0
    )

    # Build item-by-item comparisons and recommended BoQ pricings
    item_comparisons: list[BidTabItemComparisonRow] = []
    recommended_pricings: list[BoQRowPricingInput] = []
    cherry_pick_total = 0.0

    for item in parsed_targets:
        q_prices: dict[str, float] = {}
        l_prices: dict[str, float] = {}
        l_totals: dict[str, float] = {}

        for sup_name, metrics in supplier_item_metrics.items():
            if item.item_id in metrics:
                qp, lp, lt = metrics[item.item_id]
                q_prices[sup_name] = qp
                l_prices[sup_name] = lp
                l_totals[sup_name] = lt

        lowest_sup: str | None = None
        lowest_lp: float | None = None
        lowest_lt: float | None = None
        highest_lp: float | None = None
        spread_pct = 0.0

        if l_prices:
            lowest_sup = min(l_prices, key=lambda k: l_prices[k])
            lowest_lp = l_prices[lowest_sup]
            lowest_lt = l_totals[lowest_sup]
            highest_lp = max(l_prices.values())
            if lowest_lp > 0:
                spread_pct = round(((highest_lp - lowest_lp) / lowest_lp) * 100.0, 1)
            cherry_pick_total += lowest_lt

        item_comparisons.append(
            BidTabItemComparisonRow(
                item_id=item.item_id,
                description=item.description,
                unit=item.unit,
                required_quantity=item.required_quantity,
                supplier_quoted_unit_prices=q_prices,
                supplier_landed_unit_prices=l_prices,
                supplier_landed_totals=l_totals,
                lowest_landed_supplier=lowest_sup,
                lowest_landed_unit_price=lowest_lp,
                lowest_landed_total=lowest_lt,
                highest_landed_unit_price=highest_lp,
                price_spread_percent=spread_pct,
            )
        )

        # If target item is linked to a BoQ row, prepare ready-to-write BoQRowPricingInput
        if item.boq_sheet_name and item.boq_row_number is not None:
            chosen_sup = (
                best_single
                if (award_strategy == "BEST_SINGLE_SUPPLIER" and best_single and best_single in l_prices)
                else lowest_sup
            )
            if chosen_sup and chosen_sup in l_prices:
                raw_price = l_prices[chosen_sup] * contractor_markup_factor
                if round_step > 0:
                    final_price = round(raw_price / round_step) * round_step
                else:
                    final_price = round(raw_price, 2)
                recommended_pricings.append(
                    BoQRowPricingInput(
                        sheet_name=item.boq_sheet_name,
                        row_number=item.boq_row_number,
                        material_unit_price=round(final_price, 2),
                        note=f"Ártükör nyertes ({chosen_sup}): lerakott ár {l_prices[chosen_sup]:,.0f} {currency}/{item.unit}",
                    )
                )

    saved_xlsx_str: str | None = None
    if output_xlsx_path is not None:
        out_p = Path(output_xlsx_path)
        _write_bid_tab_excel(
            output_path=out_p,
            package_name=package_name,
            currency=currency,
            target_items=parsed_targets,
            supplier_summaries=supplier_summaries,
            item_comparisons=item_comparisons,
            best_single_supplier=best_single,
        )
        saved_xlsx_str = str(out_p.resolve())

    return SupplierBidTabulationReport(
        package_name=package_name,
        currency=currency,
        target_items_count=len(parsed_targets),
        suppliers_compared_count=len(supplier_summaries),
        total_package_weight_tonnes=round(total_pkg_weight_t, 2),
        total_package_pallets=total_pkg_pallets,
        best_single_supplier=best_single,
        best_single_supplier_landed_total=best_single_tot,
        second_best_supplier_landed_total=second_best_tot,
        single_supplier_savings_vs_second=savings_vs_second,
        cherry_pick_split_landed_total=round(cherry_pick_total, 2),
        output_xlsx_path=saved_xlsx_str,
        supplier_summaries=supplier_summaries,
        item_comparisons=item_comparisons,
        recommended_boq_pricings=recommended_pricings,
        warnings=warnings,
    )
