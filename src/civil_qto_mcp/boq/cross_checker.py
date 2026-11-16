"""
Tool 5 implementation: compare_qto_with_boq
Deterministic cross-checker between CAD QTO measurements and Excel BoQ line items.
Handles construction unit synonyms and scale conversions (e.g., 450 m CAD vs. 0.45 km BoQ,
db/akna/pcs, m2/nm/ha, m3/lm3/bm3) and classifies discrepancies.
"""

from __future__ import annotations

import re

from civil_qto_mcp.boq.unit_normalizer import normalize_engineering_unit
from civil_qto_mcp.models.boq_models import (
    BoQItem,
    CompareQtoBoQReport,
    QtoBoQComparisonItem,
    QtoInputRecord,
)


def _find_matching_boq_item(
    qto: QtoInputRecord, boq_items: list[BoQItem], target_canon_unit: str
) -> BoQItem | None:
    """Match a QtoInputRecord to a BoQItem by row_number, item_number/code, or keyword_query."""
    if qto.target_row_number is not None:
        for it in boq_items:
            if it.row_number == qto.target_row_number:
                return it

    if qto.target_item_number:
        target_clean = qto.target_item_number.strip().lower()
        for it in boq_items:
            if (
                it.item_number.strip().lower() == target_clean
                or it.code.strip().lower() == target_clean
            ):
                return it

    query = qto.keyword_query or qto.description or qto.qto_id
    if query:
        tokens = [
            tok.lower()
            for tok in re.split(r"[\s,;/_()-]+", query)
            if len(tok) >= 2
        ]
        best_item: BoQItem | None = None
        best_score = 0
        for it in boq_items:
            hay = f"{it.item_number} {it.code} {it.description}".lower()
            hits = sum(1 for tok in tokens if tok in hay)
            if hits == 0:
                continue
            score = hits * 10
            if it.canonical_unit == target_canon_unit:
                score += 5
            if score > best_score:
                best_score = score
                best_item = it
        if best_item is not None:
            return best_item

    return None


def compare_qto_with_boq(
    qto_records: list[QtoInputRecord],
    boq_items: list[BoQItem],
    tolerance_percent: float = 2.0,
    critical_percent: float = 10.0,
) -> CompareQtoBoQReport:
    """
    Deterministically compare CAD QTO measurements against extracted Excel BoQ items
    with automatic construction unit synonym and scale conversion.
    """
    comparisons: list[QtoBoQComparisonItem] = []
    exact_or_tol_count = 0
    disc_count = 0
    missing_or_incomp_count = 0

    for qto in qto_records:
        cad_canon_unit, cad_mult = normalize_engineering_unit(qto.measured_unit)
        cad_canon_qty = round(qto.measured_quantity * cad_mult, 4)

        matched = _find_matching_boq_item(qto, boq_items, cad_canon_unit)
        if matched is None:
            missing_or_incomp_count += 1
            comparisons.append(
                QtoBoQComparisonItem(
                    qto_id=qto.qto_id,
                    canonical_unit=cad_canon_unit,
                    cad_quantity_canonical=cad_canon_qty,
                    status="MISSING_IN_BOQ",
                    engineering_note=(
                        f"Feature '{qto.qto_id}' ({qto.measured_quantity} {qto.measured_unit}) "
                        "found on CAD drawing has no matching line item in the BoQ."
                    ),
                )
            )
            continue

        boq_canon_unit, boq_mult = normalize_engineering_unit(matched.raw_unit)
        boq_canon_qty = round(matched.quantity * boq_mult, 4)

        if cad_canon_unit != boq_canon_unit and boq_canon_unit != "lump_sum":
            missing_or_incomp_count += 1
            comparisons.append(
                QtoBoQComparisonItem(
                    qto_id=qto.qto_id,
                    matched_boq_sheet=matched.sheet_name,
                    matched_boq_row=matched.row_number,
                    matched_boq_item_number=matched.item_number,
                    matched_boq_description=matched.description,
                    canonical_unit=cad_canon_unit,
                    cad_quantity_canonical=cad_canon_qty,
                    boq_raw_quantity=matched.quantity,
                    boq_raw_unit=matched.raw_unit,
                    boq_quantity_canonical=boq_canon_qty,
                    status="UNIT_INCOMPATIBLE",
                    engineering_note=(
                        f"Unit dimension mismatch: CAD measured in '{qto.measured_unit}' ({cad_canon_unit}), "
                        f"while BoQ row {matched.row_number} is in '{matched.raw_unit}' ({boq_canon_unit})."
                    ),
                )
            )
            continue

        delta_canon = round(cad_canon_qty - boq_canon_qty, 4)
        delta_boq_unit = round(delta_canon / boq_mult, 4) if boq_mult != 0 else delta_canon

        if abs(boq_canon_qty) > 1e-9:
            delta_pct = round((delta_canon / boq_canon_qty) * 100.0, 2)
        else:
            delta_pct = 100.0 if abs(cad_canon_qty) > 1e-9 else 0.0

        abs_pct = abs(delta_pct)
        if abs(delta_canon) <= 1e-3:
            status = "EXACT_MATCH"
            exact_or_tol_count += 1
            note = (
                f"Exact match ({cad_canon_qty} {cad_canon_unit} == "
                f"{matched.quantity} {matched.raw_unit})."
            )
        elif abs_pct <= tolerance_percent:
            status = "WITHIN_TOLERANCE"
            exact_or_tol_count += 1
            note = (
                f"Within {tolerance_percent}% tolerance: CAD={cad_canon_qty} {cad_canon_unit} vs "
                f"BoQ={matched.quantity} {matched.raw_unit} (delta={delta_pct:+.2f}%)."
            )
        elif abs_pct <= critical_percent:
            status = "MINOR_DISCREPANCY"
            disc_count += 1
            note = (
                f"Minor discrepancy ({delta_pct:+.2f}%): CAD={cad_canon_qty} {cad_canon_unit} vs "
                f"BoQ={matched.quantity} {matched.raw_unit} (diff={delta_boq_unit:+.2f} {matched.raw_unit})."
            )
        else:
            status = "CRITICAL_DISCREPANCY"
            disc_count += 1
            note = (
                f"CRITICAL discrepancy ({delta_pct:+.2f}%): CAD={cad_canon_qty} {cad_canon_unit} vs "
                f"BoQ={matched.quantity} {matched.raw_unit} (diff={delta_boq_unit:+.2f} {matched.raw_unit}). "
                "Recommend contractor observation / BoQ revision."
            )

        comparisons.append(
            QtoBoQComparisonItem(
                qto_id=qto.qto_id,
                matched_boq_sheet=matched.sheet_name,
                matched_boq_row=matched.row_number,
                matched_boq_item_number=matched.item_number,
                matched_boq_description=matched.description,
                canonical_unit=cad_canon_unit,
                cad_quantity_canonical=cad_canon_qty,
                boq_raw_quantity=matched.quantity,
                boq_raw_unit=matched.raw_unit,
                boq_quantity_canonical=boq_canon_qty,
                delta_canonical=delta_canon,
                delta_in_boq_unit=delta_boq_unit,
                delta_percent=delta_pct,
                status=status,  # type: ignore[arg-type]
                engineering_note=note,
            )
        )

    return CompareQtoBoQReport(
        total_comparisons=len(comparisons),
        exact_or_within_tolerance_count=exact_or_tol_count,
        discrepancy_count=disc_count,
        missing_or_incompatible_count=missing_or_incomp_count,
        comparisons=comparisons,
    )
