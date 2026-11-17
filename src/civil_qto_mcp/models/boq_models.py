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


# -----------------------------------------------------------------------------
# v0.5 MODELS: CAD & BoQ Revision / As-Built (Megvalósulási) Diff Comparator
# -----------------------------------------------------------------------------


class CadLayerDiffItem(BaseModel):
    """Geometric difference between Old (R0 / Design) and New (R1 / As-Built) CAD layers."""

    layer_name: str
    feature_kind: Literal["AREA_POLYGON", "LINEAR", "POINT_COUNT"]
    unit: Literal["m2", "m", "pcs"]
    old_quantity: float
    new_quantity: float
    net_delta: float
    delta_percent: float | None = None
    spatially_added_area_m2: float | None = Field(
        default=None, description="For AREA_POLYGON: area of newly added zones (New - Old) in m2."
    )
    spatially_removed_area_m2: float | None = Field(
        default=None, description="For AREA_POLYGON: area of removed/omitted zones (Old - New) in m2."
    )


class BoQRevisionDiffItem(BaseModel):
    """Row-level difference between two BoQ Excel workbooks (Old vs. New)."""

    sheet_name: str
    item_key: str
    change_type: Literal[
        "ADDED_ITEM",
        "REMOVED_ITEM",
        "QUANTITY_CHANGED",
        "PRICE_CHANGED",
        "QUANTITY_AND_PRICE_CHANGED",
        "TEXT_MODIFIED",
    ]
    old_row_number: int | None = None
    new_row_number: int | None = None
    description: str
    unit: str
    old_quantity: float = 0.0
    new_quantity: float = 0.0
    quantity_delta: float = 0.0
    old_unit_price: float = 0.0
    new_unit_price: float = 0.0
    old_total_price: float = 0.0
    new_total_price: float = 0.0
    total_price_delta: float = 0.0


class RevisionComparisonReport(BaseModel):
    """Output schema for Tool 17: compare_drawing_and_boq_revisions."""

    old_cad_path: str | None = None
    new_cad_path: str | None = None
    old_boq_path: str | None = None
    new_boq_path: str | None = None
    cad_layer_diffs: list[CadLayerDiffItem] = Field(default_factory=list)
    boq_item_diffs: list[BoQRevisionDiffItem] = Field(default_factory=list)
    boq_added_count: int = 0
    boq_removed_count: int = 0
    boq_modified_count: int = 0
    old_boq_grand_total: float = 0.0
    new_boq_grand_total: float = 0.0
    boq_net_total_delta: float = 0.0
    ready_variation_items: list[BoQSupplementalItemInput] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# v0.6 MODELS: Supplier Quote Reader & Bid Tabulation (Ártükör) Engine
# -----------------------------------------------------------------------------


class BidTabTargetItemInput(BaseModel):
    """A required material or work item in a procurement package to compare across suppliers."""

    item_id: str = Field(description="Short item identifier (e.g., 'TK-01', 'SZEG-01', 'CSO-01').")
    description: str = Field(description="Technical specification of the required item.")
    unit: str = Field(default="m2", description="Measurement unit (e.g., 'm2', 'm', 'fm', 'm3', 'db', 't').")
    required_quantity: float = Field(gt=0.0, description="Required project quantity.")
    unit_weight_kg: float | None = Field(
        default=None,
        gt=0.0,
        description="Optional unit weight in kg (e.g., 180.0 kg/m2 for 8cm paving block) for truckload calculation.",
    )
    units_per_pallet: float | None = Field(
        default=None,
        gt=0.0,
        description="Optional quantity per pallet (e.g., 9.6 m2/pallet or 36 db/pallet) for pallet/crane fee calculation.",
    )
    boq_sheet_name: str | None = Field(
        default=None, description="Optional target BoQ worksheet name for direct pricing write-back."
    )
    boq_row_number: int | None = Field(
        default=None, description="Optional target BoQ row number for direct pricing write-back."
    )


class SupplierQuoteLineInput(BaseModel):
    """A single quoted line item from a supplier."""

    item_id: str = Field(description="Matching `item_id` from `BidTabTargetItemInput`.")
    quoted_description: str = Field(default="", description="Supplier's product name or alternative note.")
    quoted_unit_price: float = Field(ge=0.0, description="Quoted net unit price before global discount and freight.")
    item_specific_surcharge_per_unit: float = Field(
        default=0.0, ge=0.0, description="Optional item-specific surcharge per unit."
    )


class SupplierQuoteInput(BaseModel):
    """Complete quote submission from a single supplier (structured lines and/or .xlsx/.pdf file)."""

    supplier_name: str = Field(description="Name of the supplier or manufacturer (e.g., 'Leier', 'Semmelrock', 'Szebeton').")
    quote_reference: str = Field(default="", description="Quote number or date.")
    quote_file_path: str | None = Field(
        default=None,
        description="Optional path to a supplier quote Excel (.xlsx) or PDF (.pdf) file to auto-extract prices and terms.",
    )
    lines: list[SupplierQuoteLineInput] = Field(
        default_factory=list, description="Quoted unit prices per target item_id."
    )
    global_discount_percent: float = Field(
        default=0.0, ge=0.0, le=90.0, description="Global percentage discount off quoted unit prices (0 to 90)."
    )
    freight_mode: Literal["INCLUDED", "LUMP_SUM", "PER_TRUCKLOAD", "PER_TONNE"] = Field(
        default="INCLUDED",
        description="How transport/freight is charged ('INCLUDED', 'LUMP_SUM', 'PER_TRUCKLOAD', 'PER_TONNE').",
    )
    freight_rate: float = Field(
        default=0.0,
        ge=0.0,
        description="Freight fee (total if LUMP_SUM, per truck if PER_TRUCKLOAD, per tonne if PER_TONNE).",
    )
    truck_capacity_tonnes: float = Field(
        default=24.0, gt=0.0, description="Payload capacity of one delivery truck in tonnes (default 24.0 t)."
    )
    unloading_fee_per_pallet: float = Field(
        default=0.0, ge=0.0, description="Crane/forklift unloading fee per pallet (önrakodós daruzási díj)."
    )
    non_refundable_pallet_fee_per_pallet: float = Field(
        default=0.0,
        ge=0.0,
        description="Non-refundable pallet usage/depreciation/packaging fee per pallet (raklaphasználati / csomagolási díj).",
    )
    payment_terms: str = Field(default="", description="Payment terms (e.g., '30 napos átutalás', 'Előre utalás').")
    delivery_lead_time_days: int | None = Field(default=None, description="Delivery lead time in calendar days.")
    notes_and_conditions: str = Field(
        default="", description="Footer text, exclusions, or fine-print conditions from the quote."
    )


class SupplierPackageSummary(BaseModel):
    """Normalized commercial and logistics summary for one supplier across the procurement package."""

    supplier_name: str
    quote_reference: str
    quoted_items_count: int
    coverage_percent: float
    total_weight_tonnes: float
    estimated_truckloads: int
    estimated_pallets: int
    ex_works_subtotal: float
    discount_amount: float
    net_material_subtotal: float
    freight_cost_total: float
    unloading_cost_total: float
    pallet_and_packaging_cost_total: float
    total_surcharges: float
    surcharge_ratio_percent: float
    total_landed_package_cost: float
    payment_terms: str
    delivery_lead_time_days: int | None = None
    fine_print_warnings: list[str] = Field(default_factory=list)


class BidTabItemComparisonRow(BaseModel):
    """Apples-to-apples landed unit price comparison for a single target item across all suppliers."""

    item_id: str
    description: str
    unit: str
    required_quantity: float
    supplier_quoted_unit_prices: dict[str, float] = Field(default_factory=dict)
    supplier_landed_unit_prices: dict[str, float] = Field(default_factory=dict)
    supplier_landed_totals: dict[str, float] = Field(default_factory=dict)
    lowest_landed_supplier: str | None = None
    lowest_landed_unit_price: float | None = None
    lowest_landed_total: float | None = None
    highest_landed_unit_price: float | None = None
    price_spread_percent: float = 0.0


class SupplierBidTabulationReport(BaseModel):
    """Output schema for Tool 19: compare_supplier_quotes_and_build_bid_tab."""

    package_name: str
    currency: str = "HUF"
    target_items_count: int
    suppliers_compared_count: int
    total_package_weight_tonnes: float
    total_package_pallets: int
    best_single_supplier: str | None = Field(
        default=None,
        description="Supplier with the lowest Total Landed Package Cost among suppliers covering 100% of items.",
    )
    best_single_supplier_landed_total: float | None = None
    second_best_supplier_landed_total: float | None = None
    single_supplier_savings_vs_second: float = 0.0
    cherry_pick_split_landed_total: float = Field(
        default=0.0,
        description="Theoretical minimum landed total if each item is bought from its lowest landed-price supplier.",
    )
    output_xlsx_path: str | None = Field(
        default=None,
        description="Path to the generated formatted Bid Tabulation (Ártükör) Excel workbook.",
    )
    supplier_summaries: list[SupplierPackageSummary] = Field(default_factory=list)
    item_comparisons: list[BidTabItemComparisonRow] = Field(default_factory=list)
    recommended_boq_pricings: list[BoQRowPricingInput] = Field(
        default_factory=list,
        description="Ready-to-pass `BoQRowPricingInput` list using the winning landed unit prices (plus optional contractor_markup_factor).",
    )
    warnings: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# v0.6 MODELS: Construction Measurement Sheet & Progress Billing (Teljesítésigazolás)
# -----------------------------------------------------------------------------


class ProgressItemEntryInput(BaseModel):
    """Progress / measurement update for a single contracted BoQ item."""

    item_key: str = Field(
        description="Row identifier or item code (e.g., 'IV.1.::12', '1.1', or exact item code/row number)."
    )
    description: str | None = Field(
        default=None, description="Optional item description (if standalone without `contract_boq_xlsx_path`)."
    )
    unit: str | None = Field(default=None, description="Optional measurement unit.")
    contracted_quantity: float | None = Field(
        default=None, gt=0.0, description="Optional contracted quantity (required if standalone without Excel file)."
    )
    contracted_unit_price: float | None = Field(
        default=None, ge=0.0, description="Optional contracted net unit price."
    )
    previous_cumulative_quantity: float = Field(
        default=0.0, ge=0.0, description="Previously certified cumulative quantity prior to this period."
    )
    current_period_quantity: float | None = Field(
        default=None,
        ge=0.0,
        description="Quantity completed in the current billing period (mutually supportive with `target_cumulative_percent`).",
    )
    target_cumulative_percent: float | None = Field(
        default=None,
        ge=0.0,
        description="Target cumulative completion percentage (e.g., 50.0 or 100.0) if `current_period_quantity` is not given.",
    )
    measurement_proof_formula: str = Field(
        default="",
        description="Measurement log formula / survey proof (e.g., 'Geodéziai felmérés: 25,0 m × 6,0 m = 150,0 m2').",
    )


class ProgressCertificateLineResult(BaseModel):
    """Computed financial and quantity progress record for a single contracted line item."""

    item_key: str
    sheet_name: str
    row_number: int | None = None
    description: str
    unit: str
    contracted_quantity: float
    contracted_unit_price: float
    contracted_total_amount: float
    previous_cumulative_quantity: float
    current_period_quantity: float
    new_cumulative_quantity: float
    completion_percent: float
    previous_cumulative_amount: float
    current_period_amount: float
    new_cumulative_amount: float
    remaining_contract_amount: float
    overrun_quantity: float = 0.0
    overrun_amount: float = 0.0
    measurement_proof_formula: str = ""


class ProgressCertificateReport(BaseModel):
    """Output schema for Tool 21: generate_progress_certificate."""

    project_name: str
    certificate_number: str
    billing_period: str
    currency: str = "HUF"
    items_processed_count: int
    overrun_items_count: int
    total_contract_amount: float
    previous_cumulative_amount: float
    current_period_gross_work_amount: float
    new_cumulative_amount: float
    overall_completion_percent: float
    remaining_contract_balance: float
    total_overrun_variation_amount: float
    advance_deduction_percent: float
    advance_deduction_amount: float
    retention_percent: float
    retention_deduction_amount: float
    net_invoicable_amount: float = Field(
        description="Current period net payable amount after advance repayment and retention deductions."
    )
    vat_percent: float = 27.0
    vat_amount: float = 0.0
    gross_invoicable_amount: float = 0.0
    output_xlsx_path: str | None = Field(
        default=None,
        description="Path to the generated 2-sheet Excel workbook ('1_Teljesítésigazolás' and '2_Felmérési_Napló').",
    )
    lines: list[ProgressCertificateLineResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)





