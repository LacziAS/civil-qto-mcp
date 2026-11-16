"""Pydantic v2 data models for Excel BoQ auditing and deterministic QTO <-> BoQ cross-checking."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BoQFormulaAnomaly(BaseModel):
    """Mathematical, formula, or structural anomaly found in an Excel BoQ sheet."""

    sheet_name: str
    row_number: int
    cell_coord: str
    anomaly_type: Literal[
        "MATH_MISMATCH",
        "HARDCODED_TOTAL",
        "SUM_RANGE_GAP",
        "SHARED_FORMULA_RISK",
        "ZERO_QUANTITY",
        "ZERO_UNIT_PRICE",
    ]
    message: str
    formula_str: str | None = None
    cached_value: float | None = None
    expected_value: float | None = None


class BoQItem(BaseModel):
    """Normalized Bill of Quantities line item extracted from an Excel worksheet."""

    sheet_name: str
    row_number: int
    item_number: str = ""
    code: str = ""
    description: str
    quantity: float
    raw_unit: str
    canonical_unit: str = Field(
        description="Normalized canonical unit (e.g., 'm', 'm2', 'm3', 'pcs', 't', 'lump_sum')."
    )
    unit_multiplier_to_canonical: float = Field(
        default=1.0,
        description="Factor multiplying quantity to reach canonical unit (e.g., 1000.0 for 'km' -> 'm').",
    )
    canonical_quantity: float = Field(
        description="Quantity converted to canonical engineering base unit."
    )
    material_unit_price: float | None = None
    labor_unit_price: float | None = None
    combined_unit_price: float | None = None
    total_price: float | None = None
    has_live_formula: bool = False


class BoQSheetSummary(BaseModel):
    """Summary of a visible worksheet in the BoQ workbook."""

    sheet_name: str
    layout_type: Literal["SINGLE_PRICE", "SPLIT_MATERIAL_LABOR", "SUMMARY_ONLY", "UNSTRUCTURED"]
    extracted_item_count: int
    hidden_rows_skipped: int
    calculated_sheet_subtotal: float = 0.0
    reported_sheet_subtotal: float | None = None


class BoQAuditReport(BaseModel):
    """Output schema for Tool 4: inspect_and_audit_boq_xlsx."""

    file_path: str
    visible_sheets_processed: list[str]
    skipped_hidden_sheets: list[str] = Field(
        default_factory=list,
        description="Sheets with state 'hidden' or 'veryHidden' that were strictly excluded.",
    )
    has_shared_formulas: bool = False
    total_items_extracted: int = 0
    total_matching_items: int = 0
    returned_items_count: int = 0
    sheet_summaries: list[BoQSheetSummary] = Field(default_factory=list)
    formula_anomalies: list[BoQFormulaAnomaly] = Field(default_factory=list)
    items: list[BoQItem] = Field(default_factory=list)


class QtoInputRecord(BaseModel):
    """Input measurement record from CAD DXF to be compared against a BoQ line item."""

    qto_id: str = Field(description="Identifier or layer name of the CAD measurement.")
    description: str = Field(default="", description="Human description of the measured feature.")
    measured_quantity: float = Field(description="Deterministic quantity measured from CAD.")
    measured_unit: str = Field(description="Unit of the CAD measurement (e.g., 'm', 'm2', 'm3', 'pcs').")
    target_item_number: str | None = Field(
        default=None,
        description="Exact BoQ item_number or code to match against, if known.",
    )
    target_row_number: int | None = Field(
        default=None,
        description="Exact Excel row_number to match against, if known.",
    )
    keyword_query: str | None = Field(
        default=None,
        description="Keyword query to match against BoQ item description if item_number is not specified.",
    )


class QtoBoQComparisonItem(BaseModel):
    """Result of comparing a single CAD QTO measurement with its matched Excel BoQ item."""

    qto_id: str
    matched_boq_sheet: str | None = None
    matched_boq_row: int | None = None
    matched_boq_item_number: str | None = None
    matched_boq_description: str | None = None
    canonical_unit: str
    cad_quantity_canonical: float
    boq_raw_quantity: float | None = None
    boq_raw_unit: str | None = None
    boq_quantity_canonical: float | None = None
    delta_canonical: float | None = Field(
        default=None, description="CAD quantity minus BoQ quantity in canonical unit."
    )
    delta_in_boq_unit: float | None = Field(
        default=None, description="CAD quantity minus BoQ quantity expressed in the BoQ's original unit."
    )
    delta_percent: float | None = None
    status: Literal[
        "EXACT_MATCH",
        "WITHIN_TOLERANCE",
        "MINOR_DISCREPANCY",
        "CRITICAL_DISCREPANCY",
        "UNIT_INCOMPATIBLE",
        "MISSING_IN_BOQ",
    ]
    engineering_note: str = ""


class CompareQtoBoQReport(BaseModel):
    """Output schema for Tool 5: compare_qto_with_boq."""

    total_comparisons: int
    exact_or_within_tolerance_count: int
    discrepancy_count: int
    missing_or_incompatible_count: int
    comparisons: list[QtoBoQComparisonItem] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# v0.3 MODELS: Safe BoQ Modification, K-tétel & Kiv. Észrevétel Writer + Pricer
# -----------------------------------------------------------------------------


class BoQRowPricingInput(BaseModel):
    """Unit pricing and optional quantity/note modification for an existing BoQ row."""

    sheet_name: str = Field(description="Target worksheet name.")
    row_number: int = Field(ge=1, description="1-based Excel row index of the item.")
    material_unit_price: float | None = Field(
        default=None, description="Base material unit price (before markup_factor)."
    )
    labor_unit_price: float | None = Field(
        default=None, description="Base labor unit price (before markup_factor)."
    )
    equipment_unit_price: float | None = Field(
        default=None, description="Base equipment unit price (before markup_factor), if 3-column layout."
    )
    single_unit_price: float | None = Field(
        default=None, description="Base combined unit price (before markup_factor), if 1-column layout."
    )
    modified_quantity: float | None = Field(
        default=None,
        description="If set, overwrites the row's quantity in red bold font and logs the change.",
    )
    technical_spec_note: str | None = Field(
        default=None, description="Optional technical specification text to write into the spec column."
    )
    justification_comment: str | None = Field(
        default=None, description="Engineering note/justification written into the comment column."
    )


class BoQSupplementalItemInput(BaseModel):
    """Input definition for appending a new supplemental item (K-tétel) or a '-' / '+' variation order pair."""

    sheet_name: str = Field(description="Target worksheet name.")
    item_mode: Literal[
        "SUPPLEMENTAL_ITEM", "VARIATION_PAIR", "K_TETEL", "KIV_ESZREVETEL_PAIR"
    ] = Field(
        default="SUPPLEMENTAL_ITEM",
        description=(
            "'SUPPLEMENTAL_ITEM' (or 'K_TETEL') appends a single new supplemental item row; "
            "'VARIATION_PAIR' (or 'KIV_ESZREVETEL_PAIR') creates a '-' deduction row copying `original_row_number` "
            "at its exact original unit price followed by a '+' replacement row."
        ),
    )
    original_row_number: int | None = Field(
        default=None,
        description="Required when item_mode='KIV_ESZREVETEL_PAIR': 1-based row of the original item to deduct.",
    )
    item_number: str | int | None = Field(
        default=None, description="Display item number / sorszám for the new row."
    )
    category_number: str | int | None = Field(
        default=None, description="Category number (for multi-column layouts like Petrolterv)."
    )
    category_name: str | None = Field(
        default=None, description="Category header text if starting a supplemental group."
    )
    code: str = Field(default="K-tétel", description="ÉNGY / TERC code or 'K-tétel' / 'kiv. észrevétel'.")
    description: str = Field(description="Full engineering description of the new/replacement item.")
    technical_spec: str = Field(default="-", description="Technical specification detail.")
    quantity: float = Field(description="Quantity of the new/replacement item.")
    unit: str = Field(description="Unit of measurement (e.g., 'fm', 'm2', 'm3', 'db', 'klt').")
    status_flag: str = Field(default="ALAP", description="Status flag ('ALAP' or 'OPCIÓ').")
    material_unit_price: float = Field(default=0.0, description="Base material unit price (before markup).")
    labor_unit_price: float = Field(default=0.0, description="Base labor unit price (before markup).")
    equipment_unit_price: float = Field(default=0.0, description="Base equipment unit price (before markup).")
    justification_note: str = Field(
        default="", description="Engineering justification written into the comment column."
    )


class BoQModificationReport(BaseModel):
    """Output schema for Tool 11: apply_boq_modifications_and_pricing."""

    source_xlsx_path: str
    output_xlsx_path: str
    markup_factor_applied: float
    priced_rows_count: int
    quantity_modified_rows_count: int
    inserted_k_items_count: int
    inserted_observation_pairs_count: int
    sheet_net_totals: dict[str, float] = Field(default_factory=dict)
    grand_total_net: float
    calc_chain_removed: bool
    cached_formula_values_updated: bool
    post_write_formula_anomalies: int
    warnings: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# v0.4 MODELS: Technological Completeness Auditor & Price/RFQ Engine
# -----------------------------------------------------------------------------


class MissingCompanionFinding(BaseModel):
    """A missing technological companion item detected by Tool 13."""

    rule_id: str
    chain_category: Literal[
        "ASPHALT_CHAIN",
        "PAVING_BLOCK_CHAIN",
        "GRAVITY_SEWER_CHAIN",
        "PRESSURE_WATER_CHAIN",
        "RC_FOUNDATION_CHAIN",
        "INDUSTRIAL_SLAB_CHAIN",
    ]
    severity: Literal["CRITICAL_OMISSION", "RECOMMENDED_COMPANION", "RATIO_WARNING"]
    trigger_sheet: str
    trigger_description: str
    missing_item_description: str
    recommended_quantity: float
    recommended_unit: str
    engineering_justification: str
    ready_supplemental_item: BoQSupplementalItemInput


class TechnologicalCompletenessReport(BaseModel):
    """Output schema for Tool 13: audit_technological_completeness."""

    file_path: str
    total_items_scanned: int
    active_chains_detected: list[str] = Field(default_factory=list)
    missing_companions_count: int
    findings: list[MissingCompanionFinding] = Field(default_factory=list)
    ready_supplemental_items: list[BoQSupplementalItemInput] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MatchedBoQPriceRecord(BaseModel):
    """Matched regional unit price for a single BoQ row produced by Tool 15."""

    sheet_name: str
    row_number: int
    item_number: str
    boq_description: str
    quantity: float
    unit: str
    matched_database_item: str | None = None
    match_source_tier: Literal[
        "TIER1_OWN_REGIONAL_DB",
        "TIER2_SUPPLIER_CATALOG",
        "TIER3_BUILT_IN_BENCHMARK",
        "UNMATCHED",
    ]
    region_used: str
    is_transport_sensitive: bool = False
    base_material_unit_price: float = 0.0
    base_labor_unit_price: float = 0.0
    base_equipment_unit_price: float = 0.0
    final_combined_unit_price: float = 0.0
    estimated_row_total: float = 0.0
    confidence_score: float = 0.0
    ready_row_pricing: BoQRowPricingInput


class SupplierPartnerContact(BaseModel):
    """Regional supplier or subcontractor contact from partner database."""

    company_name: str
    category: str = ""
    region: str = ""
    email: str = ""
    phone: str = ""


class RfqLineItem(BaseModel):
    """Material or work item included inside a supplier RFQ package."""

    sheet_name: str
    row_number: int
    description: str
    quantity: float
    unit: str


class SupplierRfqPackage(BaseModel):
    """Consolidated procurement RFQ package grouped by trade/material category."""

    package_code: Literal[
        "READYMIX_AND_CKT",
        "AGGREGATES_AND_SAND",
        "PAVING_AND_CURBS",
        "ASPHALT_MIX",
        "UTILITY_PIPES_AND_MANHOLES",
        "REBAR_AND_STEEL",
    ]
    package_title: str
    items_count: int
    items: list[RfqLineItem] = Field(default_factory=list)
    recommended_suppliers: list[SupplierPartnerContact] = Field(default_factory=list)


class PriceMatchAndRfqReport(BaseModel):
    """Output schema for Tool 15: match_prices_and_generate_rfq."""

    boq_path: str
    project_location: str
    resolved_region: str
    markup_factor_applied: float
    total_rows_processed: int
    matched_rows_count: int
    unmatched_rows_count: int
    transport_sensitive_rows_count: int
    estimated_grand_total_net: float
    matched_records: list[MatchedBoQPriceRecord] = Field(default_factory=list)
    ready_row_pricings: list[BoQRowPricingInput] = Field(default_factory=list)
    rfq_packages: list[SupplierRfqPackage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


