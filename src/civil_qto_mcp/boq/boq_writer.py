"""
Tool 11: Safe BoQ Modification, K-tétel & Kiv. Észrevétel Writer + Pricer (apply_boq_modifications_and_pricing).

Safely writes unit prices (with optional `markup_factor`), quantity modifications,
`K-tétel` supplemental items, and `-` / `+` `kiv. észrevétel` observation pairs into
1-column, 2-column (TERC Anyag+Díj), or 3-column (Munka+Anyag+Gép) Excel BoQ workbooks.
Automatically updates SUM ranges, populates XML `<v>` cached values, removes `xl/calcChain.xml`,
sets `fullCalcOnLoad="1"`, and audits the output file.
"""

from __future__ import annotations

import shutil
import warnings
import xml.etree.ElementTree as ET
import zipfile
from copy import copy
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from civil_qto_mcp.boq.excel_auditor import (
    SUM_FORMULA_REGEX,
    _detect_sheet_columns,
    _to_float,
    inspect_and_audit_boq_xlsx,
)
from civil_qto_mcp.models.boq_models import (
    BoQModificationReport,
    BoQRowPricingInput,
    BoQSupplementalItemInput,
)


def _apply_markup(val: float | None, markup_factor: float, round_step: float) -> float:
    if val is None or val == 0.0:
        return 0.0
    scaled = val * markup_factor
    if round_step > 0:
        return round(scaled / round_step) * round_step
    return round(scaled, 2)


def _copy_row_style(ws: openpyxl.worksheet.worksheet.Worksheet, src_r: int, dst_r: int, max_c: int) -> None:
    ws.row_dimensions[dst_r].height = ws.row_dimensions[src_r].height or 22
    for c in range(1, max_c + 1):
        sc = ws.cell(src_r, c)
        dc = ws.cell(dst_r, c)
        if sc.has_style:
            dc.font = copy(sc.font)
            dc.fill = copy(sc.fill)
            dc.border = copy(sc.border)
            dc.alignment = copy(sc.alignment)
            dc.number_format = sc.number_format


def apply_boq_modifications_and_pricing(
    source_xlsx_path: str | Path,
    output_xlsx_path: str | Path,
    row_pricings: list[BoQRowPricingInput] | list[dict] | None = None,
    supplemental_items: list[BoQSupplementalItemInput] | list[dict] | None = None,
    markup_factor: float = 1.0,
    round_step: float = 0.01,
) -> BoQModificationReport:
    """
    Apply unit prices, quantity modifications, K-tételek, and '-'/'+' kiv. észrevétel pairs
    to a BoQ Excel workbook while preserving 100% formula and XML cache integrity.
    """
    src_path = Path(source_xlsx_path).resolve()
    dst_path = Path(output_xlsx_path).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"Source Excel workbook not found: {src_path}")

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    warnings_list: list[str] = []

    parsed_pricings: list[BoQRowPricingInput] = []
    if row_pricings:
        for rp in row_pricings:
            parsed_pricings.append(
                rp if isinstance(rp, BoQRowPricingInput) else BoQRowPricingInput.model_validate(rp)
            )

    parsed_supps: list[BoQSupplementalItemInput] = []
    if supplemental_items:
        for si in supplemental_items:
            parsed_supps.append(
                si
                if isinstance(si, BoQSupplementalItemInput)
                else BoQSupplementalItemInput.model_validate(si)
            )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb_f = openpyxl.load_workbook(str(src_path), data_only=False)
        wb_v = openpyxl.load_workbook(str(src_path), data_only=True)

    red_bold = Font(name="Arial Narrow", size=11, bold=True, color="C00000")
    red_note = Font(name="Arial Narrow", size=10, italic=True, color="C00000")
    k_fill = PatternFill(fill_type="solid", start_color="FFF2CC", end_color="FFF2CC")

    # Map sheet column structures
    sheet_col_maps: dict[str, tuple[int, dict[str, int]]] = {}
    for ws in wb_f.worksheets:
        if ws.sheet_state != "visible":
            continue
        ws_val = wb_v[ws.title]
        max_r = ws.max_row or 0
        max_c = min(ws.max_column or 0, 30)
        top_rows: list[tuple[int, list[str]]] = []
        for r in range(1, min(max_r + 1, 41)):
            if ws.row_dimensions[r].hidden:
                continue
            row_strs = [str(ws_val.cell(r, c).value or "").strip() for c in range(1, max_c + 1)]
            if any(row_strs):
                top_rows.append((r, row_strs))
        h_row, c_map = _detect_sheet_columns(top_rows)
        if "desc" in c_map and "qty" in c_map:
            sheet_col_maps[ws.title] = (h_row, c_map)

    priced_count = 0
    qty_mod_count = 0
    k_inserted_count = 0
    obs_pairs_count = 0

    # 1. Apply row pricings and quantity modifications
    for rp in parsed_pricings:
        if rp.sheet_name not in sheet_col_maps:
            warnings_list.append(f"Sheet '{rp.sheet_name}' not found or unstructured; skipped row {rp.row_number}.")
            continue
        ws = wb_f[rp.sheet_name]
        _, c_map = sheet_col_maps[rp.sheet_name]
        r = rp.row_number

        if rp.modified_quantity is not None:
            c_qty = c_map["qty"] + 1
            ws.cell(r, c_qty, rp.modified_quantity).font = red_bold
            qty_mod_count += 1

        c_mat_u = c_map.get("mat_unit")
        c_lab_u = c_map.get("lab_unit")
        c_equip_u = c_map.get("equip_unit")
        c_up = c_map.get("unit_price")

        row_priced = False
        if c_mat_u is not None and rp.material_unit_price is not None:
            ws.cell(r, c_mat_u + 1, _apply_markup(rp.material_unit_price, markup_factor, round_step))
            row_priced = True
        if c_lab_u is not None and rp.labor_unit_price is not None:
            ws.cell(r, c_lab_u + 1, _apply_markup(rp.labor_unit_price, markup_factor, round_step))
            row_priced = True
        if c_equip_u is not None and rp.equipment_unit_price is not None:
            ws.cell(r, c_equip_u + 1, _apply_markup(rp.equipment_unit_price, markup_factor, round_step))
            row_priced = True
        if c_up is not None and (
            rp.single_unit_price is not None
            or (
                c_mat_u is None
                and (rp.material_unit_price is not None or rp.labor_unit_price is not None)
            )
        ):
            eff_single = (
                rp.single_unit_price
                if rp.single_unit_price is not None
                else (rp.material_unit_price or 0.0)
                + (rp.labor_unit_price or 0.0)
                + (rp.equipment_unit_price or 0.0)
            )
            ws.cell(r, c_up + 1, _apply_markup(eff_single, markup_factor, round_step))
            if "total" in c_map:
                qty_let = get_column_letter(c_map["qty"] + 1)
                up_let = get_column_letter(c_up + 1)
                ws.cell(r, c_map["total"] + 1, f"={qty_let}{r}*{up_let}{r}")
            row_priced = True
        if row_priced:
            priced_count += 1

        # Optional technical note / justification comment
        if rp.justification_comment:
            # Find comment column ('Megjegyzés' or column immediately after total)
            c_tot = c_map.get("total", c_map["qty"])
            comment_col = c_tot + 2
            for col_i in range(1, (ws.max_column or 25) + 1):
                hdr_txt = str(ws.cell(sheet_col_maps[rp.sheet_name][0], col_i).value or "").lower()
                if any(
                    k in hdr_txt
                    for k in ("megjegyz", "comment", "indok", "remark", "note", "bemerkung", "hinweis")
                ):
                    comment_col = col_i
                    break
            ws.cell(r, comment_col, rp.justification_comment).font = red_note

    # 2. Append supplemental items (K_TETEL or KIV_ESZREVETEL_PAIR)
    supps_by_sheet: dict[str, list[BoQSupplementalItemInput]] = {}
    for si in parsed_supps:
        supps_by_sheet.setdefault(si.sheet_name, []).append(si)

    for sname, s_items in supps_by_sheet.items():
        if sname not in sheet_col_maps:
            warnings_list.append(f"Sheet '{sname}' not found for supplemental items.")
            continue
        ws = wb_f[sname]
        ws_val = wb_v[sname]
        h_row, c_map = sheet_col_maps[sname]
        max_c = min(ws.max_column or 23, 25)
        c_tot = c_map.get("total")

        # Locate if there is a bottom SUM(...) total row
        sum_row_idx: int | None = None
        last_item_row = h_row + 1
        for r in range(h_row + 1, (ws.max_row or h_row) + 1):
            if c_tot is not None:
                f_str = str(ws.cell(r, c_tot + 1).value or "").strip()
                if f_str.startswith("=") and SUM_FORMULA_REGEX.match(f_str):
                    sum_row_idx = r
                    break
            if ws.cell(r, c_map["desc"] + 1).value is not None and ws.cell(r, c_map["qty"] + 1).value is not None:
                last_item_row = r

        def _populate_item_row(
            target_r: int,
            template_r: int,
            item_no_val: str | int | None,
            code_val: str,
            desc_val: str,
            qty_val: float,
            unit_val: str,
            mat_u: float,
            lab_u: float,
            equip_u: float,
            note_val: str,
            status_val: str = "ALAP",
            _ws: openpyxl.worksheet.worksheet.Worksheet = ws,
            _c_map: dict[str, int] = c_map,
            _h_row: int = h_row,
            _max_c: int = max_c,
            _c_tot: int | None = c_tot,
        ) -> None:
            _copy_row_style(_ws, template_r, target_r, _max_c)
            for c_i in range(1, _max_c + 1):
                _ws.cell(target_r, c_i).fill = k_fill

            if "item_no" in _c_map and item_no_val is not None:
                _ws.cell(target_r, _c_map["item_no"] + 1, item_no_val).font = red_bold
            if "code" in _c_map:
                _ws.cell(target_r, _c_map["code"] + 1, code_val).font = red_bold
            _ws.cell(target_r, _c_map["desc"] + 1, desc_val).font = red_bold
            _ws.cell(target_r, _c_map["qty"] + 1, qty_val).font = red_bold
            if "unit" in _c_map:
                _ws.cell(target_r, _c_map["unit"] + 1, unit_val).font = red_bold

            qty_let = get_column_letter(_c_map["qty"] + 1)
            c_mat = _c_map.get("mat_unit")
            c_lab = _c_map.get("lab_unit")
            c_eq = _c_map.get("equip_unit")
            c_up = _c_map.get("unit_price")

            if c_mat is not None and c_lab is not None:
                _ws.cell(target_r, c_mat + 1, mat_u)
                _ws.cell(target_r, c_lab + 1, lab_u)
                mat_let = get_column_letter(c_mat + 1)
                lab_let = get_column_letter(c_lab + 1)
                if c_eq is not None:
                    _ws.cell(target_r, c_eq + 1, equip_u)
                    eq_let = get_column_letter(c_eq + 1)
                    for col_scan in range(1, _max_c + 1):
                        hdr_s = str(_ws.cell(_h_row, col_scan).value or "").upper()
                        if "ALAP" in hdr_s:
                            _ws.cell(target_r, col_scan, status_val)
                    if "lab_total" in _c_map:
                        _ws.cell(target_r, _c_map["lab_total"] + 1, f"=${qty_let}{target_r}*{lab_let}{target_r}")
                    if "mat_total" in _c_map:
                        _ws.cell(target_r, _c_map["mat_total"] + 1, f"=${qty_let}{target_r}*{mat_let}{target_r}")
                    if "equip_total" in _c_map:
                        _ws.cell(target_r, _c_map["equip_total"] + 1, f"=${qty_let}{target_r}*{eq_let}{target_r}")
                    if _c_tot is not None:
                        q_let = get_column_letter(_c_map.get("lab_total", c_eq + 1) + 1)
                        s_let = get_column_letter(_c_map.get("equip_total", c_eq + 3) + 1)
                        _ws.cell(target_r, _c_tot + 1, f"=SUM({q_let}{target_r}:{s_let}{target_r})").font = red_bold
                else:
                    if "mat_total" in _c_map and "lab_total" in _c_map and _c_tot is not None:
                        mt_col = _c_map["mat_total"] + 1
                        lt_col = _c_map["lab_total"] + 1
                        mt_let = get_column_letter(mt_col)
                        lt_let = get_column_letter(lt_col)
                        _ws.cell(target_r, mt_col, f"={qty_let}{target_r}*{mat_let}{target_r}")
                        _ws.cell(target_r, lt_col, f"={qty_let}{target_r}*{lab_let}{target_r}")
                        _ws.cell(target_r, _c_tot + 1, f"={mt_let}{target_r}+{lt_let}{target_r}").font = red_bold
                    elif _c_tot is not None:
                        _ws.cell(
                            target_r,
                            _c_tot + 1,
                            f"={qty_let}{target_r}*({mat_let}{target_r}+{lab_let}{target_r})",
                        ).font = red_bold
            elif c_up is not None and _c_tot is not None:
                comb_u = mat_u + lab_u + equip_u
                _ws.cell(target_r, c_up + 1, comb_u)
                up_let = get_column_letter(c_up + 1)
                _ws.cell(target_r, _c_tot + 1, f"={qty_let}{target_r}*{up_let}{target_r}").font = red_bold

            if note_val:
                for col_i in range(1, _max_c + 1):
                    hdr_txt = str(_ws.cell(_h_row, col_i).value or "").lower()
                    if any(
                        k in hdr_txt
                        for k in ("megjegyz", "comment", "indok", "remark", "note", "bemerkung", "hinweis")
                    ):
                        _ws.cell(target_r, col_i, note_val).font = red_note
                        break

        for si in s_items:
            is_variation_pair = si.item_mode in ("VARIATION_PAIR", "KIV_ESZREVETEL_PAIR")
            if sum_row_idx is not None:
                ws.insert_rows(sum_row_idx, amount=2 if is_variation_pair else 1)
                ins_r = sum_row_idx
                sum_row_idx += 2 if is_variation_pair else 1
            else:
                ins_r = last_item_row + 1

            if is_variation_pair:
                if si.original_row_number is None:
                    raise ValueError(
                        "original_row_number is required when item_mode is 'VARIATION_PAIR' or 'KIV_ESZREVETEL_PAIR'."
                    )
                orig_r = si.original_row_number
                orig_item_no = ws_val.cell(orig_r, c_map["item_no"] + 1).value if "item_no" in c_map else ""
                orig_code = str(ws_val.cell(orig_r, c_map["code"] + 1).value or "") if "code" in c_map else ""
                orig_desc = str(ws_val.cell(orig_r, c_map["desc"] + 1).value or "")
                orig_qty = abs(_to_float(ws_val.cell(orig_r, c_map["qty"] + 1).value) or si.quantity)
                orig_unit = str(ws_val.cell(orig_r, c_map["unit"] + 1).value or si.unit) if "unit" in c_map else si.unit
                orig_mat = _to_float(ws_val.cell(orig_r, c_map["mat_unit"] + 1).value) or 0.0 if "mat_unit" in c_map else 0.0
                orig_lab = _to_float(ws_val.cell(orig_r, c_map["lab_unit"] + 1).value) or 0.0 if "lab_unit" in c_map else 0.0
                orig_eq = _to_float(ws_val.cell(orig_r, c_map["equip_unit"] + 1).value) or 0.0 if "equip_unit" in c_map else 0.0
                if "unit_price" in c_map and "mat_unit" not in c_map:
                    orig_mat = _to_float(ws_val.cell(orig_r, c_map["unit_price"] + 1).value) or 0.0

                ded_note = (
                    "kiv. észrevétel (-): elmaradó eredeti tétel"
                    if si.item_mode == "KIV_ESZREVETEL_PAIR"
                    else "Variation order (-): omitted original item"
                )
                plus_code = (
                    "kiv. észrevétel"
                    if si.item_mode == "KIV_ESZREVETEL_PAIR"
                    else (si.code if si.code != "K-tétel" else "VO+")
                )

                # 1) '-' Deduction row: exact original text & exact original unit price
                _populate_item_row(
                    target_r=ins_r,
                    template_r=last_item_row,
                    item_no_val=orig_item_no,
                    code_val=orig_code,
                    desc_val=orig_desc,
                    qty_val=-orig_qty,
                    unit_val=orig_unit,
                    mat_u=orig_mat,
                    lab_u=orig_lab,
                    equip_u=orig_eq,
                    note_val=ded_note,
                    status_val=si.status_flag,
                )
                # 2) '+' Replacement row directly below
                _populate_item_row(
                    target_r=ins_r + 1,
                    template_r=last_item_row,
                    item_no_val=si.item_number or f"{orig_item_no}+",
                    code_val=plus_code,
                    desc_val=si.description,
                    qty_val=abs(si.quantity),
                    unit_val=si.unit,
                    mat_u=_apply_markup(si.material_unit_price, markup_factor, round_step),
                    lab_u=_apply_markup(si.labor_unit_price, markup_factor, round_step),
                    equip_u=_apply_markup(si.equipment_unit_price, markup_factor, round_step),
                    note_val=si.justification_note,
                    status_val=si.status_flag,
                )
                last_item_row = ins_r + 1
                obs_pairs_count += 1
            else:
                _populate_item_row(
                    target_r=ins_r,
                    template_r=last_item_row,
                    item_no_val=si.item_number or f"K-{k_inserted_count + 1}",
                    code_val=si.code,
                    desc_val=si.description,
                    qty_val=si.quantity,
                    unit_val=si.unit,
                    mat_u=_apply_markup(si.material_unit_price, markup_factor, round_step),
                    lab_u=_apply_markup(si.labor_unit_price, markup_factor, round_step),
                    equip_u=_apply_markup(si.equipment_unit_price, markup_factor, round_step),
                    note_val=si.justification_note,
                    status_val=si.status_flag,
                )
                last_item_row = ins_r
                k_inserted_count += 1

        # Update bottom SUM formula range so all inserted rows are included
        if sum_row_idx is not None and c_tot is not None:
            tot_let = get_column_letter(c_tot + 1)
            ws.cell(sum_row_idx, c_tot + 1, f"=SUM({tot_let}{h_row + 1}:{tot_let}{sum_row_idx - 1})")

    wb_f.save(str(dst_path))

    # 3. Compute deterministic cached <v> values for all formula cells & strip xl/calcChain.xml
    cell_cache: dict[tuple[str, str], float] = {}
    sheet_net_totals: dict[str, float] = {}

    wb_saved = openpyxl.load_workbook(str(dst_path), data_only=False)
    for sname, (h_row, c_map) in sheet_col_maps.items():
        ws_s = wb_saved[sname]
        c_qty = c_map["qty"]
        c_mat = c_map.get("mat_unit")
        c_lab = c_map.get("lab_unit")
        c_eq = c_map.get("equip_unit")
        c_up = c_map.get("unit_price")
        c_tot = c_map.get("total")
        sh_sum = 0.0

        for r in range(h_row + 1, (ws_s.max_row or h_row) + 1):
            if ws_s.row_dimensions[r].hidden:
                continue
            if c_tot is not None:
                f_raw = str(ws_s.cell(r, c_tot + 1).value or "").strip()
                if f_raw.startswith("=") and SUM_FORMULA_REGEX.match(f_raw):
                    tot_coord = f"{get_column_letter(c_tot + 1)}{r}"
                    cell_cache[(sname, tot_coord)] = round(sh_sum, 2)
                    continue

            q_val = _to_float(ws_s.cell(r, c_qty + 1).value)
            if q_val is None:
                continue
            m_val = (_to_float(ws_s.cell(r, c_mat + 1).value) or 0.0) if c_mat is not None else 0.0
            l_val = (_to_float(ws_s.cell(r, c_lab + 1).value) or 0.0) if c_lab is not None else 0.0
            e_val = (_to_float(ws_s.cell(r, c_eq + 1).value) or 0.0) if c_eq is not None else 0.0
            u_val = (_to_float(ws_s.cell(r, c_up + 1).value) or 0.0) if c_up is not None else 0.0

            comb = (m_val + l_val + e_val) if (c_mat is not None and c_lab is not None) else u_val
            row_tot = round(q_val * comb, 2)
            sh_sum += row_tot

            if "mat_total" in c_map:
                cell_cache[(sname, f"{get_column_letter(c_map['mat_total'] + 1)}{r}")] = round(q_val * m_val, 2)
            if "lab_total" in c_map:
                cell_cache[(sname, f"{get_column_letter(c_map['lab_total'] + 1)}{r}")] = round(q_val * l_val, 2)
            if "equip_total" in c_map:
                cell_cache[(sname, f"{get_column_letter(c_map['equip_total'] + 1)}{r}")] = round(q_val * e_val, 2)
            if c_tot is not None:
                cell_cache[(sname, f"{get_column_letter(c_tot + 1)}{r}")] = row_tot

        sheet_net_totals[sname] = round(sh_sum, 2)

    # Patch XML archive
    tmp_zip = Path(str(dst_path) + ".tmp.zip")
    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ET.register_namespace("", ns_main)
    ET.register_namespace("r", ns_rel)

    with zipfile.ZipFile(dst_path, "r") as zin:
        wb_xml = ET.fromstring(zin.read("xl/workbook.xml"))
        rels_xml = ET.fromstring(zin.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {rel.attrib.get("Id"): rel.attrib.get("Target") for rel in rels_xml}
        xmlpath_to_sheet: dict[str, str] = {}
        for sh in wb_xml.findall(f".//{{{ns_main}}}sheet"):
            name = sh.attrib.get("name", "")
            rid = sh.attrib.get(f"{{{ns_rel}}}id", "")
            target = (rid_to_target.get(rid, "") or "").lstrip("/")
            if target and not target.startswith("xl/"):
                target = "xl/" + target
            xmlpath_to_sheet[target] = name

        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "xl/calcChain.xml":
                    continue
                data = zin.read(item.filename)
                if item.filename == "xl/workbook.xml":
                    root = ET.fromstring(data)
                    calc_pr = root.find(f"{{{ns_main}}}calcPr")
                    if calc_pr is None:
                        calc_pr = ET.SubElement(root, f"{{{ns_main}}}calcPr")
                    calc_pr.set("fullCalcOnLoad", "1")
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                elif item.filename in xmlpath_to_sheet:
                    sname = xmlpath_to_sheet[item.filename]
                    root = ET.fromstring(data)
                    for c_elem in root.findall(f".//{{{ns_main}}}c"):
                        coord = c_elem.attrib.get("r", "")
                        if (sname, coord) in cell_cache:
                            val = cell_cache[(sname, coord)]
                            v_elem = c_elem.find(f"{{{ns_main}}}v")
                            if v_elem is None:
                                v_elem = ET.SubElement(c_elem, f"{{{ns_main}}}v")
                            if "t" in c_elem.attrib:
                                del c_elem.attrib["t"]
                            v_elem.text = str(int(val) if float(val).is_integer() else val)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                zout.writestr(item, data)

    shutil.move(str(tmp_zip), str(dst_path))

    # 4. Run post-write audit
    audit_rep = inspect_and_audit_boq_xlsx(dst_path, sheet_names=list(sheet_net_totals.keys()))
    non_zero_anomalies = [
        a for a in audit_rep.formula_anomalies if a.anomaly_type not in ("ZERO_UNIT_PRICE",)
    ]

    grand_tot = round(sum(sheet_net_totals.values()), 2)
    return BoQModificationReport(
        source_xlsx_path=str(src_path),
        output_xlsx_path=str(dst_path),
        markup_factor_applied=markup_factor,
        priced_rows_count=priced_count,
        quantity_modified_rows_count=qty_mod_count,
        inserted_k_items_count=k_inserted_count,
        inserted_observation_pairs_count=obs_pairs_count,
        sheet_net_totals=sheet_net_totals,
        grand_total_net=grand_tot,
        calc_chain_removed=True,
        cached_formula_values_updated=True,
        post_write_formula_anomalies=len(non_zero_anomalies),
        warnings=warnings_list,
    )
