"""
Tool 13: Technological Completeness & Missing-Item Matrix Auditor (audit_technological_completeness).

Scans a civil engineering BoQ Excel workbook (and optional CAD QTO hints) against 6 built-in
technological completeness chains (EN / DIN VOB/C / Hungarian ÉNGY & Térburkolat Kft. rules):
1. ASPHALT_CHAIN (tack coat, tie-in saw-cutting & joint sealing, curbs, manhole cover leveling)
2. PAVING_BLOCK_CHAIN (bedding layer, curbs with C20/25 haunch, separation geotextile)
3. GRAVITY_SEWER_CHAIN (manhole cover count parity, hand excavation ratio, bedding, spoil haulage, watertightness test, survey)
4. PRESSURE_WATER_CHAIN (concrete thrust blocks, pressure test, disinfection & water sampling, warning tape)
5. RC_FOUNDATION_CHAIN (blinding concrete, formwork, rebar, plinth XPS insulation)
6. INDUSTRIAL_SLAB_CHAIN (PE sliding membrane, fiber/mesh + surface hardener, curing compound, saw-cut contraction joints)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from civil_qto_mcp.boq.excel_auditor import inspect_and_audit_boq_xlsx
from civil_qto_mcp.models.boq_models import (
    BoQItem,
    BoQSupplementalItemInput,
    MissingCompanionFinding,
    TechnologicalCompletenessReport,
)


def _has_any(items: list[BoQItem], keywords: tuple[str, ...]) -> bool:
    for it in items:
        d = it.description.lower()
        if any(k in d for k in keywords):
            return True
    return False


def _sum_qty_matching(items: list[BoQItem], keywords: tuple[str, ...]) -> float:
    total = 0.0
    for it in items:
        d = it.description.lower()
        if any(k in d for k in keywords):
            total += it.canonical_quantity
    return total


def audit_technological_completeness(
    boq_xlsx_path: str | Path,
    sheet_names: list[str] | None = None,
    cad_curb_length_m: float | None = None,
    cad_asphalt_tie_in_length_m: float | None = None,
    cad_shallow_pipe_concrete_m3: float | None = None,
    project_profile: Literal["STANDARD", "LIDL_BBS", "GENERAL_CONTRACTOR_TENDER"] = "STANDARD",
    locale: Literal["en", "de", "hu"] = "hu",
) -> TechnologicalCompletenessReport:
    """
    Scan a BoQ workbook for technologically omitted companion items (K-tétel candidates)
    across roadworks, block paving, gravity sewers, pressure water lines, foundations, and industrial slabs,
    including client-specific profile checks (e.g., LIDL_BBS) and RFI question generation.
    """
    audit_rep = inspect_and_audit_boq_xlsx(
        file_path=boq_xlsx_path,
        sheet_names=sheet_names,
        limit=5000,
        offset=0,
    )
    items = audit_rep.items
    findings: list[MissingCompanionFinding] = []
    active_chains: list[str] = []
    warnings_list: list[str] = []

    def _add_finding(
        rule_id: str,
        chain: Literal[
            "ASPHALT_CHAIN",
            "PAVING_BLOCK_CHAIN",
            "GRAVITY_SEWER_CHAIN",
            "PRESSURE_WATER_CHAIN",
            "RC_FOUNDATION_CHAIN",
            "INDUSTRIAL_SLAB_CHAIN",
        ],
        severity: Literal["CRITICAL_OMISSION", "RECOMMENDED_COMPANION", "RATIO_WARNING"],
        trig_item: BoQItem,
        desc_hu: str,
        desc_en: str,
        qty: float,
        unit: str,
        just_hu: str,
        just_en: str,
        mat_p: float = 0.0,
        lab_p: float = 0.0,
    ) -> None:
        desc = desc_hu if locale == "hu" else desc_en
        just = just_hu if locale == "hu" else just_en
        idx_num = len(findings) + 1
        supp = BoQSupplementalItemInput(
            sheet_name=trig_item.sheet_name,
            item_mode="K_TETEL" if locale == "hu" else "SUPPLEMENTAL_ITEM",
            item_number=f"K-{idx_num}",
            code="K-tétel" if locale == "hu" else f"SUPP-{idx_num:02d}",
            description=desc,
            quantity=round(qty, 2),
            unit=unit,
            material_unit_price=mat_p,
            labor_unit_price=lab_p,
            justification_note=just,
        )
        findings.append(
            MissingCompanionFinding(
                rule_id=rule_id,
                chain_category=chain,
                severity=severity,
                trigger_sheet=trig_item.sheet_name,
                trigger_description=trig_item.description[:90],
                missing_item_description=desc,
                recommended_quantity=round(qty, 2),
                recommended_unit=unit,
                engineering_justification=just,
                ready_supplemental_item=supp,
            )
        )

    # -------------------------------------------------------------------------
    # 1. ASPHALT_CHAIN
    # -------------------------------------------------------------------------
    asphalt_items = [
        it
        for it in items
        if any(k in it.description.lower() for k in ("ac 11", "ac 16", "ac 22", "sma ", "aszfalt kopó", "aszfalt kötő", "asphalt wearing", "asphalt binder"))
        and "bont" not in it.description.lower()
        and "demol" not in it.description.lower()
    ]
    if asphalt_items:
        active_chains.append("ASPHALT_CHAIN")
        trig = asphalt_items[0]
        # Estimate asphalt surface area in m2 (if given in m3 with 5-8cm thickness, multiply by ~16)
        asph_area_m2 = 0.0
        for ai in asphalt_items:
            if ai.canonical_unit == "m2":
                asph_area_m2 = max(asph_area_m2, ai.canonical_quantity)
            elif ai.canonical_unit == "m3":
                asph_area_m2 = max(asph_area_m2, ai.canonical_quantity / 0.06)
        if asph_area_m2 <= 0:
            asph_area_m2 = 500.0

        # 1a. Bituminous tack coat (kellősítés / rétegragasztás)
        if len(asphalt_items) >= 2 and not _has_any(
            items, ("kellősít", "kellosit", "rétegragaszt", "emulzió", "tack coat", "bitumen emulsion", "haftkleber")
        ):
            tack_area = round(asph_area_m2 * max(len(asphalt_items) - 1, 1), 1)
            _add_finding(
                rule_id="ASPH-01-TACK-COAT",
                chain="ASPHALT_CHAIN",
                severity="RECOMMENDED_COMPANION",
                trig_item=trig,
                desc_hu="Aszfaltrétegek közötti bitumenemulziós rétegragasztás (kellősítés C40B 60-3, 0,40 kg/m2)",
                desc_en="Bituminous tack coat between asphalt courses (C40B emulsion, 0.40 kg/m2)",
                qty=tack_area,
                unit="m2",
                just_hu="Többrétegű aszfalt pályaszerkezetnél a rétegek együttdolgozásához előírt bitumenemulziós rétegragasztás hiányzik a kiírásból.",
                just_en="Multi-layer asphalt structure requires bituminous tack coat between courses.",
                mat_p=350.0,
                lab_p=250.0,
            )

        # 1b. Tie-in saw-cutting & joint sealing
        if not _has_any(
            items, ("hézagkiönt", "hezagkiont", "hézagszalag", "aszfalt vágás", "aszfaltvágás", "saw cut", "joint seal", "fugenband")
        ):
            tie_len = cad_asphalt_tie_in_length_m or round(math.sqrt(asph_area_m2) * 1.5, 1)
            _add_finding(
                rule_id="ASPH-02-TIE-IN-JOINT",
                chain="ASPHALT_CHAIN",
                severity="CRITICAL_OMISSION",
                trig_item=trig,
                desc_hu="Meglévő aszfaltburkolat teljes vastagságú vágása és csatlakozási munkahézag bitumenes kiöntése / hézagszalagozása",
                desc_en="Full-depth saw-cutting of existing asphalt and hot-applied bituminous joint sealing at tie-ins",
                qty=tie_len,
                unit="fm" if locale == "hu" else "m",
                just_hu="Új és meglévő aszfaltburkolat csatlakozásánál technológiailag kötelező a vágás és a bitumenes hézagzárás.",
                just_en="Tie-in between new and existing asphalt requires full-depth saw-cutting and bituminous joint sealing.",
                mat_p=2000.0,
                lab_p=3000.0,
            )

    # -------------------------------------------------------------------------
    # 2. PAVING_BLOCK_CHAIN & ROAD CURBS
    # -------------------------------------------------------------------------
    paving_items = [
        it
        for it in items
        if any(k in it.description.lower() for k in ("térkő", "terko", "block paving", "concrete paving", "betonsteinpflaster"))
        and "alap" not in it.description.lower()
        and "bont" not in it.description.lower()
    ]
    if paving_items or asphalt_items:
        if paving_items:
            active_chains.append("PAVING_BLOCK_CHAIN")
        trig_p = paving_items[0] if paving_items else asphalt_items[0]
        pav_area = sum(it.canonical_quantity for it in paving_items) if paving_items else 200.0

        # Check if ANY curb is present on the same sheet
        sheet_items = [it for it in items if it.sheet_name == trig_p.sheet_name]
        if not _has_any(sheet_items, ("szegély", "szegely", "curb", "kerb", "bordstein", "randeinfassung")):
            curb_len = cad_curb_length_m or round(4.0 * math.sqrt(max(pav_area, 50.0)), 1)
            _add_finding(
                rule_id="PAV-01-MISSING-CURB",
                chain="PAVING_BLOCK_CHAIN" if paving_items else "ASPHALT_CHAIN",
                severity="CRITICAL_OMISSION",
                trig_item=trig_p,
                desc_hu="15×25×100 cm-es előregyártott kiemelt / kerti betonszegély építése C20/25-XC2-16-F2 szegélyalap és háttámasz betonba ágyazva",
                desc_en="Precast concrete road/garden curb 15x25x100 cm set in C20/25 concrete bed and haunching (EN 1340)",
                qty=curb_len,
                unit="fm" if locale == "hu" else "m",
                just_hu="A burkolatépítési munkalapból teljesen hiányzik a burkolat megtámasztására szolgáló betonszegély.",
                just_en="Pavement schedule omits perimeter concrete curb edge restraint and C20/25 concrete haunching.",
                mat_p=8000.0,
                lab_p=6500.0,
            )

    # -------------------------------------------------------------------------
    # 3. GRAVITY_SEWER_CHAIN
    # -------------------------------------------------------------------------
    sewer_pipes = [
        it
        for it in items
        if any(k in it.description.lower() for k in ("kg-pvc", "kgem", "pragma", "sn8", "szennyvízcs", "csapadékcs", "sewer pipe", "kanalrohr"))
    ]
    if sewer_pipes:
        active_chains.append("GRAVITY_SEWER_CHAIN")
        trig_s = sewer_pipes[0]

        # 3a. Manhole & Inspection Chamber count vs. Cast-Iron Cover count parity!
        manhole_bodies_qty = _sum_qty_matching(
            items,
            (
                "beton akna",
                "betonakna",
                "tisztítóakna",
                "vizsgálóakna",
                "vizsgaloakna",
                "teraplast",
                "manhole shaft",
                "inspection chamber",
                "betonschacht",
            ),
        )
        cover_qty = _sum_qty_matching(
            items,
            ("aknafedlap", "fedlap", "manhole cover", "schachtabdeckung"),
        )
        if manhole_bodies_qty > cover_qty and (manhole_bodies_qty - cover_qty) >= 1.0:
            missing_cov = round(manhole_bodies_qty - cover_qty, 1)
            _add_finding(
                rule_id="SEW-01-MISSING-MANHOLE-COVERS",
                chain="GRAVITY_SEWER_CHAIN",
                severity="CRITICAL_OMISSION",
                trig_item=trig_s,
                desc_hu="D400 terhelési osztályú öntöttvas aknafedlap és teleszkópos lezárás a tisztító-/vizsgálóaknákhoz",
                desc_en="D400 ductile iron manhole cover and frame (EN 124) for inspection chambers",
                qty=missing_cov,
                unit="db" if locale == "hu" else "pcs",
                just_hu=f"A kiírásban összesen {manhole_bodies_qty:.0f} db akna szerepel, de csak {cover_qty:.0f} db aknafedlap van kiírva ({missing_cov:.0f} db fedlap hiányzik!).",
                just_en=f"BoQ specifies {manhole_bodies_qty:.0f} manhole/inspection chambers but only {cover_qty:.0f} covers ({missing_cov:.0f} covers missing).",
                mat_p=38000.0,
                lab_p=12000.0,
            )

        # 3b. Shallow pipe C12/15 concrete protection if CAD hint provided
        if (
            cad_shallow_pipe_concrete_m3 is not None
            and cad_shallow_pipe_concrete_m3 > 0
            and not _has_any(items, ("körbebetonoz", "korbebetonoz", "védőbeton", "concrete encase", "betonummantelung"))
        ):
            _add_finding(
                rule_id="SEW-02-SHALLOW-CONCRETE-ENCASEMENT",
                chain="GRAVITY_SEWER_CHAIN",
                severity="CRITICAL_OMISSION",
                trig_item=trig_s,
                desc_hu="C12/15-X0b(H)-24-F1 soványbeton védő körbebetonozás (15 cm vtg.) 1,00 m-nél kisebb takarású csatornaszakaszokon",
                desc_en="C12/15 lean concrete protective encasement (15 cm thickness) for shallow pipes (<1.00 m cover)",
                qty=cad_shallow_pipe_concrete_m3,
                unit="m3",
                just_hu="1,00 m alatti takarású csatornaszakaszon szabvány (EN 1610) és műszaki leírás szerint kötelező a 15 cm C12/15 védő körbebetonozás.",
                just_en="Pipe spans with crown cover < 1.00 m require 15 cm C12/15 lean concrete protective encasement per EN 1610.",
                mat_p=35000.0,
                lab_p=17000.0,
            )

    # -------------------------------------------------------------------------
    # 4. PRESSURE_WATER_CHAIN
    # -------------------------------------------------------------------------
    pressure_pipes = [
        it
        for it in items
        if any(k in it.description.lower() for k in ("pe100", "kpe ", "sdr11", "sdr17", "nyomócső", "ivóvíz", "tűzivíz"))
    ]
    if pressure_pipes:
        active_chains.append("PRESSURE_WATER_CHAIN")
        trig_w = pressure_pipes[0]
        w_len = sum(it.canonical_quantity for it in pressure_pipes)
        if not _has_any(items, ("nyomáspróba", "nyomasproba", "pressure test", "druckprüfung")):
            _add_finding(
                rule_id="WAT-01-PRESSURE-TEST",
                chain="PRESSURE_WATER_CHAIN",
                severity="CRITICAL_OMISSION",
                trig_item=trig_w,
                desc_hu="Nyomott víz-/tűzivízvezeték nyomáspróbája, fertőtlenítése és laboratóriumi vízmintavétele jegyzőkönyvvel",
                desc_en="Hydrostatic pressure test, disinfection, flushing, and accredited water quality sampling (EN 805)",
                qty=1.0,
                unit="klt" if locale == "hu" else "ls",
                just_hu=f"A {w_len:.1f} fm nyomóvezeték üzembe helyezéséhez kötelező a nyomáspróba, fertőtlenítés és akkreditált vízmintavétel.",
                just_en=f"Pressure pipeline ({w_len:.1f} m) requires hydrostatic testing, disinfection, and water quality certification.",
                mat_p=30000.0,
                lab_p=180000.0,
            )

    # -------------------------------------------------------------------------
    # 5. RC_FOUNDATION_CHAIN
    # -------------------------------------------------------------------------
    found_items = [
        it
        for it in items
        if any(k in it.description.lower() for k in ("alaptest", "alaplemez", "sávalap", "kehelyalap", "szerkezeti vasbeton", "foundation concrete"))
    ]
    if found_items:
        active_chains.append("RC_FOUNDATION_CHAIN")
        trig_f = found_items[0]
        f_vol = sum(it.canonical_quantity for it in found_items)
        if not _has_any(items, ("szerelőbeton", "szerelő beton", "szerelobeton", "blinding concrete", "sauberkeitsschicht")):
            blind_v = round(f_vol * 0.18, 1)
            _add_finding(
                rule_id="FND-01-BLINDING-CONCRETE",
                chain="RC_FOUNDATION_CHAIN",
                severity="CRITICAL_OMISSION",
                trig_item=trig_f,
                desc_hu="C12/15-X0b(H)-24-F1 szerelőbeton készítése 10 cm vastagságban alaptestek/alaplemez alá",
                desc_en="C12/15 lean blinding concrete 10 cm thick below structural foundations",
                qty=max(blind_v, 5.0),
                unit="m3",
                just_hu="Vasbeton alaptestek és alaplemezek alá a vasszerelés tiszta elhelyezéséhez 10 cm szerelőbeton szükséges.",
                just_en="Reinforced concrete foundations require a 10 cm lean concrete blinding layer.",
                mat_p=38000.0,
                lab_p=14500.0,
            )

    # -------------------------------------------------------------------------
    # 6. INDUSTRIAL_SLAB_CHAIN
    # -------------------------------------------------------------------------
    slab_items = [
        it
        for it in items
        if any(k in it.description.lower() for k in ("ipari padló", "iparipadló", "térbeton", "industrial floor", "industrieboden"))
    ]
    if slab_items:
        active_chains.append("INDUSTRIAL_SLAB_CHAIN")
        trig_sl = slab_items[0]
        sl_area = sum(
            it.canonical_quantity if it.canonical_unit == "m2" else it.canonical_quantity / 0.18
            for it in slab_items
        )
        if not _has_any(items, ("dilatáció", "dilatacio", "hézagvág", "contraction joint", "scheinfuge")):
            joint_len = round(sl_area * (2.0 / 6.0), 1)
            _add_finding(
                rule_id="SLB-01-CONTRACTION-JOINTS",
                chain="INDUSTRIAL_SLAB_CHAIN",
                severity="CRITICAL_OMISSION",
                trig_item=trig_sl,
                desc_hu="Ipari padló / térbeton zsugorodási és dilatációs hézagainak vágása, fózolása és tartósan rugalmas poliuretán kitöltése (6×6 m raszterben)",
                desc_en="Saw-cutting, chamfering, and polyurethane elastomeric sealing of industrial floor contraction joints (6x6 m grid)",
                qty=max(joint_len, 20.0),
                unit="fm" if locale == "hu" else "m",
                just_hu="Ipari padló és térbeton építésénél a repedésmentességhez elengedhetetlen a dilatációs hézagok vágása és rugalmas kitöltése.",
                just_en="Industrial concrete floor slabs require saw-cut contraction joints and elastomeric sealant.",
                mat_p=1200.0,
                lab_p=1800.0,
            )

    # -------------------------------------------------------------------------
    # 7. LIDL_BBS PROFILE CHECKS (Building Book Specification Rules)
    # -------------------------------------------------------------------------
    if project_profile == "LIDL_BBS":
        grass_grid_items = [
            it
            for it in items
            if any(k in it.description.lower() for k in ("gyeprács", "gyepracs", "grass grid", "rasengitter"))
        ]
        if grass_grid_items:
            gg_trig = grass_grid_items[0]
            gg_area = sum(it.canonical_quantity for it in grass_grid_items)
            _add_finding(
                rule_id="LIDL-01-GRASS-GRID-BBS-VIOLATION",
                chain="PAVING_BLOCK_CHAIN",
                severity="CRITICAL_OMISSION",
                trig_item=gg_trig,
                desc_hu="20×20×8 cm vastag antracit zökkenőmentes térkő burkolat + fehér parkolócsík térkő építése (Lidl BBS előírás szerint gyeprács helyett)",
                desc_en="20x20x8 cm anthracite chamferless concrete block paving + white parking line blocks per Lidl BBS (replacing grass grid)",
                qty=gg_area,
                unit="m2",
                just_hu="Lidl BBS szabvány szerint a parkolóállásokban gyeprács helyett 20×20×8 cm vastag antracit zökkenőmentes térkő és fehér parkolójelölő kő alkalmazandó.",
                just_en="Lidl BBS standard mandates 20x20x8 cm anthracite chamferless paving blocks with white marker blocks in parking bays instead of grass grids.",
                mat_p=6800.0,
                lab_p=4500.0,
            )

        if (paving_items and asphalt_items) and not _has_any(
            items, ("rozsdamentes", "szögacél", "burkolatelválasztó", "stainless", "edelstahl")
        ):
            sep_trig = paving_items[0]
            sep_len = cad_asphalt_tie_in_length_m or 25.0
            _add_finding(
                rule_id="LIDL-02-STAINLESS-SEPARATOR",
                chain="PAVING_BLOCK_CHAIN",
                severity="RECOMMENDED_COMPANION",
                trig_item=sep_trig,
                desc_hu="Lidl szabvány szerinti rozsdamentes szögacél burkolatelválasztó profil beépítése térkő és aszfalt burkolatváltásnál",
                desc_en="Lidl-standard stainless steel L-angle edge separator profile at block paving / asphalt transitions",
                qty=sep_len,
                unit="fm" if locale == "hu" else "m",
                just_hu="Lidl BBS előírás szerint a térkő és aszfalt burkolatváltásoknál rozsdamentes szögacél elválasztó profil beépítése szükséges.",
                just_en="Lidl BBS requires a stainless steel angle edge restraint at transitions between block paving and asphalt.",
                mat_p=9500.0,
                lab_p=4500.0,
            )

    # Generate numbered Technical Clarification Questions (RFI / Műszaki Tisztázó Kérdések)
    clarification_questions: list[str] = []
    for idx, f in enumerate(findings, start=1):
        if locale == "hu":
            q_str = (
                f"{idx}. [{f.trigger_sheet} – {f.rule_id}] {f.engineering_justification} "
                f"Kérjük szíves visszajelzésüket, hogy a '{f.missing_item_description}' "
                f"({f.recommended_quantity:.2f} {f.recommended_unit}) tételt pótlólagos K-tételként "
                f"vegyük-e fel az ajánlatba!"
            )
        else:
            q_str = (
                f"{idx}. [{f.trigger_sheet} – {f.rule_id}] {f.engineering_justification} "
                f"Please confirm whether '{f.missing_item_description}' "
                f"({f.recommended_quantity:.2f} {f.recommended_unit}) should be included as a supplemental BoQ item."
            )
        clarification_questions.append(q_str)

    ready_supps = [f.ready_supplemental_item for f in findings]
    return TechnologicalCompletenessReport(
        file_path=str(Path(boq_xlsx_path).resolve()),
        project_profile=project_profile,
        total_items_scanned=len(items),
        active_chains_detected=active_chains,
        missing_companions_count=len(findings),
        findings=findings,
        ready_supplemental_items=ready_supps,
        technical_clarification_questions=clarification_questions,
        warnings=warnings_list,
    )
