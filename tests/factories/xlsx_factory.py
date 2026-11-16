"""
Programmatic Excel (.xlsx) BoQ test generator using openpyxl.
Creates multi-sheet civil engineering BoQ fixtures with:
- Visible Single-Price sheet ('01_Ut_es_Burkolat')
- Visible TERC Split Material+Labor sheet ('02_Csatorna_TERC')
- Hidden internal calculation sheet ('Kalkulacio_Belsos_Rejtett' -> must be skipped!)
- Hidden row inside visible sheet (row 7 -> must be skipped!)
- Unit synonym and multiplier test rows ('0.125708 km' = 125.708 m, 'nm' = m2, 'akna' = pcs, 'fm' = m)
- Deliberate formula traps:
  - Hardcoded total + math mismatch on row 8 (25 * 42,000 = 1,050,000 vs hardcoded 900,000)
  - Zero unit price item on row 9 ('Vasbeton vízmérőakna')
  - Incomplete SUM range on row 11 ('=SUM(G5:G9)' missing row 10!)
"""

from __future__ import annotations

from pathlib import Path

import openpyxl


def create_boq_with_traps_xlsx(file_path: str | Path) -> Path:
    """Create a synthetic Excel BoQ workbook containing realistic civil engineering audit traps."""
    out_path = Path(file_path)
    wb = openpyxl.Workbook()

    # -------------------------------------------------------------------------
    # Sheet 1: '01_Ut_es_Burkolat' (Visible, SINGLE_PRICE layout)
    # -------------------------------------------------------------------------
    ws1 = wb.active
    assert ws1 is not None
    ws1.title = "01_Ut_es_Burkolat"

    ws1["A1"] = "Projekt: 103/2026 Mintaprojekt – Út- és térburkolat építés"
    headers_1 = [
        "Tételszám",
        "ÉNGY Kód",
        "Munka megnevezése",
        "Mennyiség",
        "M.e.",
        "Egységár (Ft)",
        "Összesen (Ft)",
    ]
    for col_idx, h_text in enumerate(headers_1, start=1):
        ws1.cell(row=4, column=col_idx, value=h_text)

    # Row 5: Curb in 'km' (0.125708 km == 125.708 m CAD net length!)
    ws1.append(["1.1", "ÉNGY-101", "Kiemelt szegély 15x25x100 cm építése", 0.125708, "km", 12_200_000.0, "=D5*F5"])
    # Row 6: Paving in 'nm' (820.0 nm vs 908.18 m2 CAD net area -> CRITICAL_DISCREPANCY)
    ws1.append(["1.2", "ÉNGY-102", "Térkő burkolat 8 cm antracit", 820.0, "nm", 9_500.0, "=D6*F6"])
    # Row 7: Hidden row (must be skipped!)
    ws1.append(["1.2.b", "ÉNGY-102B", "Alternatív gyeprács (REJTETT SOR)", 500.0, "m2", 7_000.0, "=D7*F7"])
    ws1.row_dimensions[7].hidden = True
    # Row 8: Hardcoded total AND math mismatch (25 * 42,000 = 1,050,000 != 900,000)
    ws1.append(["1.3", "ÉNGY-103", "C12/15 szegélyalap beton", 25.0, "m3", 42_000.0, 900_000.0])
    # Row 9: Zero unit price trap
    ws1.append(["1.4", "ÉNGY-104", "Vasbeton vízmérőhely (0 Ft-os tétel)", 1.0, "db", 0.0, "=D9*F9"])
    # Row 10: Valid item excluded from SUM(G5:G9)
    ws1.append(["1.5", "ÉNGY-105", "Taktilis figyelmeztető burkolat", 12.0, "m2", 15_000.0, "=D10*F10"])
    # Row 11: Subtotal with SUM_RANGE_GAP (misses row 10!)
    ws1.append(["", "", "Munkanem összesen:", None, "", None, "=SUM(G5:G9)"])

    # -------------------------------------------------------------------------
    # Sheet 2: '02_Csatorna_TERC' (Visible, SPLIT_MATERIAL_LABOR layout)
    # -------------------------------------------------------------------------
    ws2 = wb.create_sheet(title="02_Csatorna_TERC")
    headers_2 = [
        "Sorszám",
        "TERC Kód",
        "Tétel szövege",
        "Mennyiség",
        "Egység",
        "Anyag egységár",
        "Díj egységár",
        "Összesen",
    ]
    for col_idx, h_text in enumerate(headers_2, start=1):
        ws2.cell(row=3, column=col_idx, value=h_text)

    ws2.append(["2.1", "TERC-201", "Tisztítóakna DN1000-DN1200 komplett", 3.0, "akna", 210_000.0, 85_000.0, "=D4*(F4+G4)"])
    ws2.append(["2.2", "TERC-202", "KG-PVC DN300 csatornacső fektetés", 40.0, "fm", 14_000.0, 9_000.0, "=D5*(F5+G5)"])
    ws2.append(["", "", "Csatorna összesen:", None, "", None, None, "=SUM(H4:H5)"])

    # -------------------------------------------------------------------------
    # Sheet 3: 'Kalkulacio_Belsos_Rejtett' (HIDDEN sheet -> must be strictly ignored!)
    # -------------------------------------------------------------------------
    ws3 = wb.create_sheet(title="Kalkulacio_Belsos_Rejtett")
    ws3.sheet_state = "hidden"
    for col_idx, h_text in enumerate(headers_1, start=1):
        ws3.cell(row=1, column=col_idx, value=h_text)
    ws3.append(["99.1", "TITKOS-1", "Belső titkos tartalékkeret", 1.0, "db", 50_000_000.0, 50_000_000.0])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))

    # Also write cached values so openpyxl data_only=True reads evaluated numbers
    wb_val = openpyxl.load_workbook(str(out_path), data_only=False)
    # In openpyxl, a freshly saved file without Excel opening it has formula strings in cells;
    # let's ensure our test or auditor handles both cached values and formula-only workbooks cleanly!
    wb_val.close()
    return out_path
