"""
MCP Resources & One-Click Engineering Workflow Prompts for civil-qto-mcp (v0.7.0).

Exposes:
- 5 deterministic Engineering Reference MCP Resources (`civil-qto://standards/...` & `civil-qto://workflow/...`)
- 5 One-Click Engineering Workflow MCP Prompts (`tender_full_audit`, `roadworks_and_paving_qto`,
  `utility_network_and_trench_qto`, `supplier_bid_tabulation`, `monthly_progress_billing`)
"""

from __future__ import annotations

import json
from typing import Any


def register_resources_and_prompts(mcp: Any) -> None:
    """Register all 5 engineering reference resources and 5 workflow prompts on the MCPServer instance."""

    # =========================================================================
    # MCP RESOURCES (5 Engineering Reference Standards & Protocols)
    # =========================================================================

    @mcp.resource("civil-qto://standards/en1610-trench-widths")
    def get_en1610_trench_widths_resource() -> str:
        """MSZ EN 1610 / DIN 4124 utility trench bottom widths, pipe OD multipliers, bedding, and shoring thresholds."""
        data = {
            "standard": "MSZ EN 1610 / DIN 4124",
            "minimum_width_by_dn_mm": [
                {"dn_range": "<= 225", "formula": "OD + 0.40 m"},
                {"dn_range": "226 - 350", "formula": "OD + 0.50 m"},
                {"dn_range": "351 - 700", "formula": "OD + 0.70 m"},
                {"dn_range": "701 - 1200", "formula": "OD + 0.85 m"},
                {"dn_range": "> 1200", "formula": "OD + 1.00 m"},
            ],
            "minimum_width_by_depth_m": [
                {"depth_range": "< 1.00 m", "min_bottom_width_m": 0.60},
                {"depth_range": "1.00 - 1.75 m", "min_bottom_width_m": 0.80},
                {"depth_range": "1.76 - 4.00 m", "min_bottom_width_m": 0.90},
                {"depth_range": "> 4.00 m", "min_bottom_width_m": 1.00},
            ],
            "pipe_od_multipliers": {
                "KG-PVC / PVC-U": 1.04,
                "PP Pragma / Corrugated": 1.14,
                "KPE / PE100 / HDPE": 1.00,
                "Reinforced Concrete (VB)": 1.24,
                "Ductile Iron / Steel": 1.08,
            },
            "bedding_and_protection_defaults": {
                "lower_bedding_thickness_m": 0.10,
                "protective_cover_above_crown_m": 0.30,
                "shoring_depth_threshold_m": 1.25,
                "shallow_cover_concrete_protection_threshold_m": 1.00,
                "depth_bands_terc_vob": ["0.00 - 1.50 m", "1.50 - 3.00 m", "> 3.00 m"],
            },
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.resource("civil-qto://standards/pavement-densities-and-factors")
    def get_pavement_densities_resource() -> str:
        """EN 13108 / EN 13285 / EN 14227 pavement layer bulk densities, compaction multipliers, and pallet logistics norms."""
        data = {
            "standards": ["EN 13108 (Asphalt)", "EN 13285 (Unbound Granular)", "EN 14227 (CBM/CKT)", "EN 1338 (Paving Blocks)"],
            "layers": [
                {"material": "AC 11 / AC 16 / AC 22 / SMA Asphalt", "bulk_density_t_per_m3": 2.40, "compaction_factor": 1.00, "waste_factor": 1.02},
                {"material": "CKT-4 / CBM Cement-Bound Base", "bulk_density_t_per_m3": 2.25, "compaction_factor": 1.20, "waste_factor": 1.03},
                {"material": "FZKA 0/63 / M22 Crushed Stone Sub-base", "bulk_density_t_per_m3": 2.05, "compaction_factor": 1.15, "waste_factor": 1.03},
                {"material": "2/5 mm Crushed Stone Bedding (Ágyazó zúzalék)", "bulk_density_t_per_m3": 1.65, "compaction_factor": 1.15, "waste_factor": 1.05},
                {"material": "0/4 mm Sand / Bedding Gravel", "bulk_density_t_per_m3": 1.75, "compaction_factor": 1.15, "waste_factor": 1.05},
                {"material": "Separation Geotextile (GRK 200-300 g/m2)", "bulk_density_t_per_m3": None, "compaction_factor": 1.00, "waste_factor": 1.12},
            ],
            "pallet_and_truckload_logistics_defaults": {
                "standard_truck_payload_tonnes": 24.0,
                "paving_block_8cm": {"weight_kg_per_m2": 180.0, "m2_per_pallet": 9.6, "cutting_waste_factor": 1.03},
                "paving_block_6cm": {"weight_kg_per_m2": 135.0, "m2_per_pallet": 10.8, "cutting_waste_factor": 1.03},
                "paving_block_10cm": {"weight_kg_per_m2": 225.0, "m2_per_pallet": 8.0, "cutting_waste_factor": 1.03},
                "road_curb_15x30x100": {"weight_kg_per_m": 110.0, "m_per_pallet": 12.0},
                "garden_curb_5x20x100": {"weight_kg_per_m": 23.0, "m_per_pallet": 42.0},
            },
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.resource("civil-qto://standards/precast-manhole-catalogs")
    def get_precast_manhole_catalogs_resource() -> str:
        """DIN EN 1917, ASTM C478, and SzeBeton T2 precast manhole & road gully modular heights."""
        data = {
            "catalogs_supported": ["DIN_EN_1917_STD", "ASTM_C478_US", "SZEBETON_T2_HU"],
            "szebeton_t2_hu_rules": {
                "cover_deduction_h1_to_h2_cm": 17.0,
                "dn1000_base_afe_cm": 65.0,
                "dn1200_base_afe_cm": 75.0,
                "cone_asz_cm": 60.0,
                "shallow_flat_slab_aft_cm": 20.0,
                "shaft_rings_agy_cm": [100, 75, 50, 25],
                "adjustment_rings_szb_cm": [10, 8, 6],
                "spare_adjustment_ring_rule": "+1 pcs SZB 60/10 per manhole",
                "gully_50x50_standard": "VA-F 80 cm + VA-GY 50 cm + VA-GY 100 cm + DUNA 500x500 D400 grate",
                "paving_block_cover_rule": "600x600 mm square-frame circular D400 cover (HYDROTEC)",
            },
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.resource("civil-qto://standards/technological-completeness-chains")
    def get_completeness_chains_resource() -> str:
        """6 Civil Engineering Technological Completeness Chains + Lidl BBS Specification Checklist."""
        data = {
            "chains": {
                "ASPHALT_CHAIN": ["Bituminous tack coat (C40B)", "Existing asphalt full-depth saw-cutting & hot joint sealing", "Perimeter curbs", "Manhole cover leveling"],
                "PAVING_BLOCK_CHAIN": ["2/5 mm bedding layer", "Perimeter curbs + C20/25 concrete bed & haunching", "Separation geotextile", "3% cutting waste"],
                "GRAVITY_SEWER_CHAIN": ["Manhole vs. D400 cover count parity", "EN 1610 trench width & bedding", "Hand excavation at crossings", "Shallow (<1.0m) C12/15 concrete encasement", "Watertightness & CCTV test"],
                "PRESSURE_WATER_CHAIN": ["Concrete thrust blocks at bends/tees", "Hydrostatic pressure test, disinfection & accredited water sampling", "Warning tape"],
                "RC_FOUNDATION_CHAIN": ["10 cm C12/15 blinding concrete (+10 cm lateral overhang)", "Formwork", "Rebar", "Plinth XPS perimeter insulation"],
                "INDUSTRIAL_SLAB_CHAIN": ["Double PE sliding membrane", "Steel fiber / mesh + surface hardener", "Curing compound", "6x6 m saw-cut contraction joints & PU sealant"],
            },
            "lidl_bbs_profile_rules": [
                "Replace grass grid (gyeprács) in parking bays with 20x20x8 cm anthracite chamferless paving blocks + white parking marker blocks.",
                "Install Lidl-standard stainless steel L-angle separator profile at block paving / asphalt transitions.",
                "Use 600x600 mm square-frame circular D400 covers in block paving zones.",
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.resource("civil-qto://workflow/hitl-3-step-protocol")
    def get_hitl_protocol_resource() -> str:
        """3-Step Human-in-the-Loop (HITL) Civil Engineering QTO & BoQ Auditing Protocol."""
        return (
            "# 3-Step Human-in-the-Loop (HITL) Civil QTO & BoQ Protocol\n\n"
            "1. **Step 1 – Discovery & Conversion:** Convert `.dwg` (`convert_dwg_to_dxf`) or vector `.pdf` "
            "(`convert_vector_pdf_to_dxf`), then run `inspect_civil_dxf` to verify CRS/units (`EOV`/`UTM`/meters) "
            "and layer names before measuring.\n"
            "2. **Step 2 – Deterministic CAD Take-Off:** Run `measure_linear_and_area_features`, "
            "`extract_utility_network_and_levels`, `calculate_tin_earthwork_volumes`, "
            "`calculate_trench_earthwork_and_bedding`, `calculate_pavement_layer_quantities`, "
            "`calculate_cross_section_and_embankment_qto`, `optimize_precast_manhole_elements`, "
            "and `export_qto_visual_overlay`.\n"
            "3. **Step 3 – BoQ Audit, Pricing & Billing:** Run `inspect_and_audit_boq_xlsx` (excluding hidden sheets/rows), "
            "`compare_qto_with_boq`, `audit_technological_completeness`, `compare_supplier_quotes_and_build_bid_tab`, "
            "`apply_boq_modifications_and_pricing`, and `generate_progress_certificate`.\n\n"
            "## 5 Mandatory Executive Tender Summary Quantities\n"
            "1. Paving surfaces (`m2` internal vs. public road)\n"
            "2. Manholes & road gullies (`pcs` by DN)\n"
            "3. Major structures (oil separators, detention tanks, retaining walls)\n"
            "4. Utility pipe lengths (`m` by DN & material)\n"
            "5. Earthwork volumes (`m3` topsoil, cut, fill, spoil haul-off)\n"
        )

    # =========================================================================
    # MCP PROMPTS (5 One-Click Engineering Workflow Orchestrators)
    # =========================================================================

    @mcp.prompt()
    def tender_full_audit(
        cad_file_path: str,
        boq_xlsx_path: str,
        project_profile: str = "STANDARD",
        locale: str = "hu",
    ) -> str:
        """One-click full civil engineering tender audit: CAD discovery + BoQ trap audit + Technological completeness + 5-KPI summary + RFI questions."""
        return (
            f"Perform a complete 3-Step HITL Civil Engineering Tender Audit (locale='{locale}', "
            f"project_profile='{project_profile}') on:\n"
            f"- CAD drawing: `{cad_file_path}`\n"
            f"- BoQ Excel workbook: `{boq_xlsx_path}`\n\n"
            "Follow this exact deterministic sequence:\n"
            "1. Call `inspect_civil_dxf` on the CAD file to identify CRS/unit scale, layers, blocks, and CAD hygiene issues.\n"
            "2. Call `inspect_and_audit_boq_xlsx` on the BoQ Excel workbook to extract all visible items and flag hidden rows, "
            "formula anomalies, hardcoded totals, and SUM range gaps.\n"
            "3. Call `audit_technological_completeness` with `project_profile='{project_profile}'` and `locale='{locale}'` "
            "to detect omitted companion items (K-tételek) and generate numbered Technical Clarification Questions.\n"
            "4. Present the **5 Mandatory Executive Tender Summary Quantities** (Paving m2, Manholes/Gullies pcs, "
            "Major Structures, Utility Pipes m, Earthworks m3), a summary of all Excel & technological findings, "
            "and ask for confirmation on which CAD layers to measure next."
        )

    @mcp.prompt()
    def roadworks_and_paving_qto(
        cad_file_path: str,
        pavement_preset: str = "HEAVY_TRUCK_ASPHALT_HU",
        locale: str = "hu",
    ) -> str:
        """One-click roadworks & paving QTO workflow: CAD area/curb extraction + multi-layer pavement quantities + cross-section shoulders/ditches + visual QA overlay."""
        return (
            f"Execute the deterministic Roadworks & Paving Quantity Take-Off workflow (locale='{locale}') "
            f"for `{cad_file_path}` using pavement preset `{pavement_preset}`:\n"
            "1. Call `inspect_civil_dxf` to confirm the unit scale and identify paving boundary layers, island deduction layers, and curb layers.\n"
            "2. Call `measure_linear_and_area_features` to compute exact net paving areas (m2 with island deduction and cross-layer corner gap bridging) and deduplicated curb lengths (m).\n"
            "3. Call `calculate_pavement_layer_quantities` with the measured net area and active edge perimeter to compute compacted m3, order m3, and quarry tonnage (t).\n"
            "4. If shoulders, embankment slopes, or roadside ditches are present, call `calculate_cross_section_and_embankment_qto`.\n"
            "5. Call `export_qto_visual_overlay` to generate the interactive HTML/SVG verification map."
        )

    @mcp.prompt()
    def utility_network_and_trench_qto(
        cad_file_path: str,
        manhole_catalog: str = "SZEBETON_T2_HU",
        pavement_box_deduction_m: float = 0.0,
        locale: str = "hu",
    ) -> str:
        """One-click utility network QTO workflow: manhole/pipe extraction + precast manhole element BOM optimization + EN 1610 depth-banded trench earthwork & bedding."""
        return (
            f"Execute the deterministic Utility Network & Trench Earthwork workflow (locale='{locale}') "
            f"for `{cad_file_path}` (manhole catalog=`{manhole_catalog}`, pavement_box_deduction_m={pavement_box_deduction_m}):\n"
            "1. Call `inspect_civil_dxf` to verify node, pipe, and callout text/MLEADER layers.\n"
            "2. Call `extract_utility_network_and_levels` to extract manholes (cover/invert levels, h1/h2 depths) and pipe runs.\n"
            "3. Call `optimize_precast_manhole_elements` using the extracted manholes and catalog `{manhole_catalog}` to build the manufacturer RFQ Bill of Materials.\n"
            "4. Call `calculate_trench_earthwork_and_bedding` to compute EN 1610 / DIN 4124 depth-banded excavation (0-1.5m, 1.5-3.0m, >3.0m), "
            "machine vs. hand split, lower bedding, pipe-zone cover, pipe & manhole displacement, native backfill, spoil haul-off, and two-sided shoring (m2)."
        )

    @mcp.prompt()
    def supplier_bid_tabulation(
        package_name: str,
        quotes_description_or_paths: str,
        currency: str = "HUF",
    ) -> str:
        """One-click supplier quote normalization & Bid Tabulation (Ártükör.xlsx) workflow with True Landed Cost and fine-print trap detection."""
        return (
            f"Build an apples-to-apples Supplier Bid Tabulation (Ártükör) for package `{package_name}` ({currency}) "
            f"from: {quotes_description_or_paths}\n\n"
            "1. Call `compare_supplier_quotes_and_build_bid_tab` to normalize all supplier quotes (`.xlsx`, `.pdf`, or structured lines) "
            "onto a True Landed Unit Price basis (distributing truckload freight, crane unloading, and non-refundable pallet/packaging fees).\n"
            "2. Highlight the Best Single-Supplier Package Winner vs. Cherry-Pick split total, list all detected fine-print commercial traps "
            "(unloading exclusions, advance payment, broken pallet fees), and save the color-coded `Ártükör.xlsx`.\n"
            "3. Prepare the `recommended_boq_pricings` list so the winning landed unit prices can be written into the main BoQ via `apply_boq_modifications_and_pricing`."
        )

    @mcp.prompt()
    def monthly_progress_billing(
        contract_boq_xlsx_path: str,
        project_name: str,
        certificate_number: str = "1. Részszámla",
        retention_percent: float = 5.0,
        advance_deduction_percent: float = 0.0,
    ) -> str:
        """One-click Construction Measurement Sheet (Felmérési Napló) & Monthly Progress Certificate (Teljesítésigazolás) workflow."""
        return (
            f"Generate the Construction Measurement Sheet (Felmérési Napló) and Monthly Progress Payment Certificate "
            f"(`{certificate_number}`) for `{project_name}` from `{contract_boq_xlsx_path}`:\n"
            "1. Inspect the contracted BoQ with `inspect_and_audit_boq_xlsx` to confirm active items and contracted unit prices.\n"
            f"2. Call `generate_progress_certificate` with `retention_percent={retention_percent}` and "
            f"`advance_deduction_percent={advance_deduction_percent}` to compute previous, current, and cumulative quantities/amounts.\n"
            "3. Flag any item exceeding 100% of contracted quantity (Pótmunka / Variation Order required) and export the 2-sheet Excel workbook "
            "(`1_Teljesítésigazolás` and `2_Felmérési_Napló`) with live formulas and FMV/ME signature blocks."
        )
