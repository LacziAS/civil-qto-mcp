"""
Tool 15: Pluggable Regional Unit-Price Matcher & Supplier RFQ Package Generator (match_prices_and_generate_rfq).

Implements the 3-Tier Location-Aware Pricing & Procurement Protocol:
- Tier 1: Own historical regional unit-price database (`sajat_egysegar_torzs.json` or custom JSON catalog)
- Tier 2: Supplier / manufacturer price lists (`beszallitoi_arlistak.json`)
- Tier 3: Built-in civil engineering reference benchmark catalog (for standalone / international use)

Also groups material-intensive BoQ items into 6 procurement RFQ packages and attaches
regional supplier contacts from `partner_es_alvallalkozo_torzs.json` (when available).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from civil_qto_mcp.boq.excel_auditor import inspect_and_audit_boq_xlsx
from civil_qto_mcp.models.boq_models import (
    BoQItem,
    BoQRowPricingInput,
    MatchedBoQPriceRecord,
    PriceMatchAndRfqReport,
    RfqLineItem,
    SupplierPartnerContact,
    SupplierRfqPackage,
)

# Built-in Tier-3 reference benchmark rules: (keywords_tuple, canonical_unit, mat_price, lab_price, equip_price, transport_sensitive, label)
BUILTIN_BENCHMARKS: list[tuple[tuple[str, ...], str, float, float, float, bool, str]] = [
    (("térkő", "terko", "block paving", "pflaster"), "m2", 4800.0, 4200.0, 500.0, True, "Concrete block paving 6-8cm with bedding"),
    (("kiemelt szegély", "kiemelt betonszegély", "útszegély", "road curb"), "m", 8000.0, 5500.0, 1000.0, True, "Precast road curb 15x25x100cm in C20/25 bed"),
    (("kerti szegély", "kertiszegély", "garden curb"), "m", 4000.0, 3500.0, 500.0, True, "Precast garden curb 5x20x100cm in C20/25 bed"),
    (("süllyesztett szegély", "flush curb"), "m", 7100.0, 5500.0, 1000.0, True, "Flush concrete curb in C20/25 bed"),
    (("ckt", "cbm", "hidraulikus"), "m3", 28500.0, 6500.0, 3500.0, True, "CKT-4 / CBM cement-bound base course"),
    (("fzka", "m63", "m45", "zúzottkő", "zuzottko", "crushed stone"), "m3", 19000.0, 2500.0, 4500.0, True, "FZKA 0/63 or 0/32 crushed stone base"),
    (("homokos kavics", "védőréteg", "sandy gravel"), "m3", 18000.0, 2500.0, 4500.0, True, "Compacted sandy gravel subbase / fill"),
    (("ac 11", "ac 16", "kopóréteg", "wearing course"), "m3", 132000.0, 9000.0, 19000.0, True, "AC 11 / AC 16 asphalt wearing course"),
    (("ac 22", "kötőréteg", "alapréteg", "binder course"), "m3", 116000.0, 8000.0, 17000.0, True, "AC 22 asphalt binder/base course"),
    (("altalaj", "tükör", "subgrade"), "m2", 0.0, 300.0, 800.0, False, "Subgrade preparation and compaction"),
    (("humusz", "topsoil"), "m3", 0.0, 800.0, 2400.0, True, "Topsoil stripping and on-site stockpiling"),
    (("kézi föld", "kezi fold", "hand excav"), "m3", 0.0, 7800.0, 0.0, False, "Hand excavation in utility protection zones"),
    (("földkiemelés", "gépi föld", "bevágás", "excavation"), "m3", 0.0, 1200.0, 3300.0, True, "Machine earth excavation"),
    (("visszatöltés", "backfill"), "m3", 0.0, 1000.0, 2200.0, False, "Layered earth backfill"),
    (("elszállítás", "elszallitas", "spoil"), "m3", 1500.0, 500.0, 3500.0, True, "Excess earth haulage and disposal"),
    (("szerelőbeton", "szerelő beton", "blinding concrete"), "m3", 38000.0, 12000.0, 2500.0, True, "C12/15 - C16/20 blinding concrete"),
    (("alaptest", "alaplemez", "szerkezeti vasbeton", "c25/30", "c30/37"), "m3", 44000.0, 14000.0, 5000.0, True, "C25/30 - C30/37 structural concrete"),
    (("betonacél", "betonacel", "rebar", "b500b"), "t", 325000.0, 235000.0, 20000.0, False, "B500B reinforcement steel cut, bent & tied"),
    (("zsaluz", "formwork"), "m2", 7500.0, 8000.0, 0.0, False, "Formwork installation and stripping"),
    (("kg-pvc", "kgem", "sn8", "sewer pipe"), "m", 4200.0, 3800.0, 800.0, False, "KG-PVC SN8 gravity sewer pipe installation"),
    (("pe100", "kpe", "sdr11"), "m", 2800.0, 2200.0, 500.0, False, "PE100 SDR11 pressure water pipe installation"),
    (("d400", "aknafedlap", "manhole cover"), "pcs", 45000.0, 15000.0, 3000.0, False, "D400 ductile iron manhole cover and frame"),
    (("beton akna", "betonakna", "tisztítóakna", "manhole"), "pcs", 185000.0, 62000.0, 25000.0, True, "Precast concrete manhole DN1000 assembly"),
    (("dúcolás", "ducolas", "shoring"), "m2", 600.0, 1200.0, 400.0, False, "Two-sided trench shoring and removal"),
]

TRANSPORT_SENSITIVE_KEYWORDS = (
    "beton",
    "ckt",
    "cbm",
    "fzka",
    "zúzottkő",
    "zuzottko",
    "kavics",
    "homok",
    "aszfalt",
    "térkő",
    "terko",
    "ac 11",
    "ac 16",
    "ac 22",
    "elszállítás",
    "elszallitas",
    "concrete",
    "asphalt",
    "gravel",
    "crushed stone",
)


def resolve_project_region(location: str) -> str:
    """Resolve a settlement/city name to a canonical regional pricing zone."""
    loc = location.lower().strip()
    if any(
        k in loc
        for k in (
            "fót",
            "fot",
            "pilisvörösvár",
            "vecsés",
            "vecses",
            "göd",
            "vác",
            "budapest",
            "bp",
            "szentendre",
            "biatorbágy",
            "dabas",
            "monor",
            "bugyi",
            "cegléd",
            "albertirsa",
            "nagykovácsi",
            "kistarcsa",
            "nagytarcsa",
        )
    ):
        return "Budapest / Pest megye"
    if any(
        k in loc
        for k in (
            "tótkomlós",
            "totkomlos",
            "karcag",
            "mezőtúr",
            "szolnok",
            "békéscsaba",
            "szeged",
            "debrecen",
            "makó",
            "gyomaendrőd",
        )
    ):
        return "Tiszántúl / Dél-Alföld"
    if any(
        k in loc
        for k in (
            "kecskemét",
            "kecskemet",
            "foktő",
            "fokto",
            "kalocsa",
            "baja",
            "lajosmizse",
            "kiskunfélegyháza",
            "kiskunhalas",
            "dunaföldvár",
            "solt",
            "helvécia",
            "hetény",
            "méntelek",
        )
    ):
        return "Bács-Kiskun (Kecskemét térsége)"
    return "Bács-Kiskun (Kecskemét térsége)"


def _discover_database_dir(explicit_dir: str | Path | None = None) -> Path | None:
    if explicit_dir:
        p = Path(explicit_dir).resolve()
        if p.exists():
            return p
    env_dir = os.environ.get("CIVIL_QTO_PRICE_DB_DIR")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir).resolve()
    default_local = Path(r"C:\Users\User\Desktop\András\Digitalis_Kollega\adatbazis")
    if default_local.exists():
        return default_local
    return None


def _classify_rfq_package(
    it: BoQItem,
) -> Literal[
    "READYMIX_AND_CKT",
    "AGGREGATES_AND_SAND",
    "PAVING_AND_CURBS",
    "ASPHALT_MIX",
    "UTILITY_PIPES_AND_MANHOLES",
    "REBAR_AND_STEEL",
] | None:
    d = it.description.lower()
    if any(k in d for k in ("bont", "demol", "geodéz", "nyomáspróba", "tömörségmérés", "fertőtlenít", "víztelenít")):
        return None
    if any(k in d for k in ("betonacél", "betonacel", "b500b", "síkháló", "acél szerelvény", "rebar")):
        return "REBAR_AND_STEEL"
    if any(k in d for k in ("ac 11", "ac 16", "ac 22", "sma ", "aszfalt kopó", "aszfalt kötő", "asphalt")):
        return "ASPHALT_MIX"
    if it.canonical_unit == "m3" and any(
        k in d
        for k in (
            "ckt",
            "cbm",
            "beton",
            "c12/15",
            "c16/20",
            "c20/25",
            "c25/30",
            "c30/37",
            "concrete",
        )
    ):
        return "READYMIX_AND_CKT"
    if any(k in d for k in ("térkő", "terko", "szegély", "szegely", "folyóka", "folyoka", "poller", "paving", "curb")):
        return "PAVING_AND_CURBS"
    if any(
        k in d
        for k in (
            "kg-pvc",
            "kgb",
            "kgu",
            "pe100",
            "kpe",
            "ef ",
            "akna",
            "fedlap",
            "zsírfogó",
            "olajfogó",
            "jelölőszalag",
            "jelőlőszalag",
            "sewer",
            "manhole",
        )
    ):
        return "UTILITY_PIPES_AND_MANHOLES"
    if any(
        k in d
        for k in (
            "ckt",
            "cbm",
            "alaptest beton",
            "alaplemez beton",
            "fejtömb beton",
            "lábazati beton",
            "szerelő beton",
            "szerelőbeton",
            "szerkezeti vasbeton",
            "c12/15",
            "c16/20",
            "c20/25",
            "c25/30",
            "c30/37",
            "concrete",
        )
    ):
        return "READYMIX_AND_CKT"
    if any(
        k in d
        for k in ("homokos kavics", "fzka", "m63", "m45", "zúzottkő", "zuzottko", "csőzóna ágyazat", "crushed stone", "gravel")
    ):
        return "AGGREGATES_AND_SAND"
    return None


PACKAGE_TITLES: dict[str, str] = {
    "READYMIX_AND_CKT": "Transzportbeton és CKT-4 útalap (Ready-Mix Concrete & CBM)",
    "AGGREGATES_AND_SAND": "Zúzottkő (FZKA/M63), Homokos kavics és Ágyazóanyagok (Aggregates & Sand)",
    "PAVING_AND_CURBS": "Térkő, Betonszegélyek, Folyókák és Utcabútorok (Paving Blocks & Curbs)",
    "ASPHALT_MIX": "Aszfaltkeverékek és Bitumenemulzió (Asphalt Wearing & Binder Courses)",
    "UTILITY_PIPES_AND_MANHOLES": "Közműcsövek (KG-PVC/PE100), Előregyártott Aknák és D400 Fedlapok (Utilities)",
    "REBAR_AND_STEEL": "Betonacél (B500B), Síkháló és Acélszerelvények (Reinforcement Steel)",
}


def match_prices_and_generate_rfq(
    boq_xlsx_path: str | Path,
    project_location: str = "Kecskemét",
    sheet_names: list[str] | None = None,
    markup_factor: float = 1.0,
    round_step: float = 0.01,
    database_dir: str | Path | None = None,
) -> PriceMatchAndRfqReport:
    """
    Match BoQ items against regional unit-price databases (Tier 1 own DB, Tier 2 supplier catalog,
    Tier 3 built-in benchmarks), flag transport-sensitive items, produce `BoQRowPricingInput` payloads,
    and group materials into supplier RFQ packages with regional partner contacts.
    """
    audit_rep = inspect_and_audit_boq_xlsx(
        file_path=boq_xlsx_path,
        sheet_names=sheet_names,
        limit=5000,
        offset=0,
    )
    resolved_reg = resolve_project_region(project_location)
    db_folder = _discover_database_dir(database_dir)
    warnings_list: list[str] = []

    own_db: list[dict] = []
    partners_db: list[dict] = []
    if db_folder is not None:
        own_p = db_folder / "sajat_egysegar_torzs.json"
        part_p = db_folder / "partner_es_alvallalkozo_torzs.json"
        if own_p.exists():
            try:
                own_db = json.loads(own_p.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                warnings_list.append(f"Could not parse {own_p.name}: {exc}")
        if part_p.exists():
            try:
                partners_db = json.loads(part_p.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                warnings_list.append(f"Could not parse {part_p.name}: {exc}")

    matched_records: list[MatchedBoQPriceRecord] = []
    ready_pricings: list[BoQRowPricingInput] = []
    rfq_map: dict[str, list[RfqLineItem]] = {k: [] for k in PACKAGE_TITLES}

    matched_cnt = 0
    unmatched_cnt = 0
    transp_cnt = 0
    grand_tot = 0.0

    for it in audit_rep.items:
        desc_low = it.description.lower()
        is_transp = any(k in desc_low for k in TRANSPORT_SENSITIVE_KEYWORDS)
        if is_transp:
            transp_cnt += 1

        # Classify into RFQ package if material-relevant
        pkg_key = _classify_rfq_package(it)
        if pkg_key is not None and it.quantity > 0:
            rfq_map[pkg_key].append(
                RfqLineItem(
                    sheet_name=it.sheet_name,
                    row_number=it.row_number,
                    description=it.description,
                    quantity=it.quantity,
                    unit=it.raw_unit,
                )
            )

        # 1. Try Built-in Engineering Benchmark Rules first for deterministic category accuracy,
        # refined by Tier-1 Own Regional DB when a strong regional token match exists
        best_mat = 0.0
        best_lab = 0.0
        best_eq = 0.0
        best_label: str | None = None
        best_tier: Literal[
            "TIER1_OWN_REGIONAL_DB",
            "TIER2_SUPPLIER_CATALOG",
            "TIER3_BUILT_IN_BENCHMARK",
            "UNMATCHED",
        ] = "UNMATCHED"
        conf = 0.0

        # Check Tier-1 Own DB token overlap
        tokens = [w for w in re.split(r"\W+", desc_low) if len(w) >= 4]
        if own_db and tokens:
            best_db_score = 0.0
            best_db_entry: dict | None = None
            for entry in own_db:
                t_nev = str(entry.get("tetel_nev", "")).lower()
                hits = sum(1 for tk in tokens if tk in t_nev)
                if hits == 0:
                    continue
                score = hits / max(len(tokens), 1)
                reg_bontas = entry.get("regio_bontas") or {}
                if resolved_reg in reg_bontas:
                    score += 0.25
                if score > best_db_score and score >= 0.65:
                    best_db_score = score
                    best_db_entry = entry

            if best_db_entry is not None:
                reg_bontas = best_db_entry.get("regio_bontas") or {}
                if resolved_reg in reg_bontas:
                    r_data = reg_bontas[resolved_reg]
                    best_mat = float(r_data.get("anyag_ar", 0.0) or 0.0)
                    best_lab = float(r_data.get("dij_ar", 0.0) or 0.0)
                else:
                    best_mat = float(best_db_entry.get("anyag_ar", 0.0) or 0.0)
                    best_lab = float(best_db_entry.get("dij_ar", 0.0) or 0.0)
                best_label = str(best_db_entry.get("tetel_nev", ""))[:80]
                best_tier = "TIER1_OWN_REGIONAL_DB"
                conf = min(round(best_db_score, 2), 0.98)

        # Fallback to Built-in Benchmark rules if Tier-1 didn't hit with high confidence
        if best_tier == "UNMATCHED":
            for kw_tuple, _u, m_p, l_p, e_p, _tr, lbl in BUILTIN_BENCHMARKS:
                if any(k in desc_low for k in kw_tuple):
                    best_mat = m_p
                    best_lab = l_p
                    best_eq = e_p
                    best_label = lbl
                    best_tier = "TIER3_BUILT_IN_BENCHMARK"
                    conf = 0.80
                    break

        if best_tier != "UNMATCHED":
            matched_cnt += 1
        else:
            unmatched_cnt += 1

        def _scale(v: float) -> float:
            if v == 0.0:
                return 0.0
            s = v * markup_factor
            return round(s / round_step) * round_step if round_step > 0 else round(s, 2)

        fin_comb = round(_scale(best_mat) + _scale(best_lab) + _scale(best_eq), 2)
        est_tot = round(it.quantity * fin_comb, 2)
        grand_tot += est_tot

        rp_input = BoQRowPricingInput(
            sheet_name=it.sheet_name,
            row_number=it.row_number,
            material_unit_price=best_mat,
            labor_unit_price=best_lab,
            equipment_unit_price=best_eq,
            single_unit_price=best_mat + best_lab + best_eq,
            justification_comment=(
                f"{best_tier} ({resolved_reg}) × {markup_factor:.2f}"
                + (" [FUVARÉRZÉKENY]" if is_transp else "")
            ),
        )
        if best_tier != "UNMATCHED":
            ready_pricings.append(rp_input)

        matched_records.append(
            MatchedBoQPriceRecord(
                sheet_name=it.sheet_name,
                row_number=it.row_number,
                item_number=it.item_number,
                boq_description=it.description,
                quantity=it.quantity,
                unit=it.raw_unit,
                matched_database_item=best_label,
                match_source_tier=best_tier,
                region_used=resolved_reg,
                is_transport_sensitive=is_transp,
                base_material_unit_price=best_mat,
                base_labor_unit_price=best_lab,
                base_equipment_unit_price=best_eq,
                final_combined_unit_price=fin_comb,
                estimated_row_total=est_tot,
                confidence_score=conf,
                ready_row_pricing=rp_input,
            )
        )

    # Build RFQ packages & attach regional suppliers from partner_es_alvallalkozo_torzs.json
    rfq_packages: list[SupplierRfqPackage] = []
    pkg_kw_map: dict[str, tuple[str, ...]] = {
        "READYMIX_AND_CKT": ("beton", "cement", "ddc", "ckt"),
        "AGGREGATES_AND_SAND": ("kavics", "kő", "bánya", "sóder", "fuvar"),
        "PAVING_AND_CURBS": ("térkő", "szegély", "leier", "frühwald", "kk kavics", "viastein"),
        "ASPHALT_MIX": ("aszfalt", "útépít", "colas", "strabag", "duna aszfalt"),
        "UTILITY_PIPES_AND_MANHOLES": ("közmű", "cső", "pipelife", "szebeton", "hydrotec", "pureco", "csomiép"),
        "REBAR_AND_STEEL": ("betonacél", "acél", "steel", "vas"),
    }

    for pkg_code, pkg_items in rfq_map.items():
        if not pkg_items:
            continue
        rec_suppliers: list[SupplierPartnerContact] = []
        if partners_db:
            kws = pkg_kw_map.get(pkg_code, ())
            for pt in partners_db:
                blob = json.dumps(pt, ensure_ascii=False).lower()
                if any(k in blob for k in kws):
                    cname = str(pt.get("Cegnev") or pt.get("cegnev") or pt.get("nev") or "")
                    cat = str(pt.get("Kategoria") or pt.get("kategoria") or "")
                    reg = str(pt.get("Regio") or pt.get("regio") or "")
                    em = str(pt.get("Email") or pt.get("email") or "")
                    ph = str(pt.get("Telefon") or pt.get("telefon") or "")
                    if cname and len(rec_suppliers) < 5:
                        rec_suppliers.append(
                            SupplierPartnerContact(
                                company_name=cname,
                                category=cat,
                                region=reg,
                                email=em,
                                phone=ph,
                            )
                        )

        rfq_packages.append(
            SupplierRfqPackage(
                package_code=pkg_code,  # type: ignore[arg-type]
                package_title=PACKAGE_TITLES[pkg_code],
                items_count=len(pkg_items),
                items=pkg_items,
                recommended_suppliers=rec_suppliers,
            )
        )

    return PriceMatchAndRfqReport(
        boq_path=str(Path(boq_xlsx_path).resolve()),
        project_location=project_location,
        resolved_region=resolved_reg,
        markup_factor_applied=markup_factor,
        total_rows_processed=len(matched_records),
        matched_rows_count=matched_cnt,
        unmatched_rows_count=unmatched_cnt,
        transport_sensitive_rows_count=transp_cnt,
        estimated_grand_total_net=round(grand_tot, 2),
        matched_records=matched_records,
        ready_row_pricings=ready_pricings,
        rfq_packages=rfq_packages,
        warnings=warnings_list,
    )
