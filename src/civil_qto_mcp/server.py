"""
FastMCP Server entry point for civil-qto-mcp.

Enforces strict stdio JSON-RPC safety (all logs, warnings, and third-party library stdout
are routed to sys.stderr) and exposes 5 deterministic civil engineering QTO & BoQ tools
governed by the 3-step Human-in-the-Loop (HITL) engineering workflow.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import warnings
from typing import Annotated, Any, Literal

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef]
from pydantic import Field

from civil_qto_mcp.boq.boq_writer import (
    apply_boq_modifications_and_pricing as _apply_boq_modifications_and_pricing,
)
from civil_qto_mcp.boq.completeness_auditor import (
    audit_technological_completeness as _audit_technological_completeness,
)
from civil_qto_mcp.boq.cross_checker import compare_qto_with_boq as _compare_qto_with_boq
from civil_qto_mcp.boq.excel_auditor import (
    inspect_and_audit_boq_xlsx as _inspect_and_audit_boq_xlsx,
)
from civil_qto_mcp.boq.price_and_rfq_engine import (
    match_prices_and_generate_rfq as _match_prices_and_generate_rfq,
)
from civil_qto_mcp.boq.progress_billing_generator import (
    generate_progress_certificate as _generate_progress_certificate,
)
from civil_qto_mcp.boq.supplier_bid_tabulator import (
    compare_supplier_quotes_and_build_bid_tab as _compare_supplier_quotes_and_build_bid_tab,
)
from civil_qto_mcp.cad.cross_section_calculator import (
    calculate_cross_section_and_embankment_qto as _calculate_cross_section_and_embankment_qto,
)
from civil_qto_mcp.cad.dwg_loader import convert_dwg_to_dxf as _convert_dwg_to_dxf
from civil_qto_mcp.cad.foundation_calculator import (
    calculate_foundation_and_slab_qto as _calculate_foundation_and_slab_qto,
)
from civil_qto_mcp.cad.inspector import inspect_civil_dxf as _inspect_civil_dxf
from civil_qto_mcp.cad.manhole_optimizer import (
    optimize_precast_manhole_elements as _optimize_precast_manhole_elements,
)
from civil_qto_mcp.cad.pavement_calculator import (
    calculate_pavement_layer_quantities as _calculate_pavement_layer_quantities,
)
from civil_qto_mcp.cad.pdf_importer import (
    convert_vector_pdf_to_dxf as _convert_vector_pdf_to_dxf,
)
from civil_qto_mcp.cad.profile_extractor import (
    extract_longitudinal_profile_data as _extract_longitudinal_profile_data,
)
from civil_qto_mcp.cad.qto_measurer import (
    measure_linear_and_area_features as _measure_linear_and_area_features,
)
from civil_qto_mcp.cad.revision_comparator import (
    compare_drawing_and_boq_revisions as _compare_drawing_and_boq_revisions,
)
from civil_qto_mcp.cad.tin_earthwork_engine import (
    calculate_tin_earthwork_volumes as _calculate_tin_earthwork_volumes,
)
from civil_qto_mcp.cad.trench_earthwork_calculator import (
    calculate_trench_earthwork_and_bedding as _calculate_trench_earthwork_and_bedding,
)
from civil_qto_mcp.cad.utility_extractor import (
    extract_utility_network_and_levels as _extract_utility_network_and_levels,
)
from civil_qto_mcp.cad.visual_overlay import (
    export_qto_visual_overlay as _export_qto_visual_overlay,
)
from civil_qto_mcp.models.boq_models import (
    BidTabTargetItemInput,
    BoQAuditReport,
    BoQModificationReport,
    BoQRowPricingInput,
    BoQSupplementalItemInput,
    CompareQtoBoQReport,
    PriceMatchAndRfqReport,
    ProgressCertificateReport,
    ProgressItemEntryInput,
    QtoInputRecord,
    RevisionComparisonReport,
    SupplierBidTabulationReport,
    SupplierQuoteInput,
    TechnologicalCompletenessReport,
)
from civil_qto_mcp.models.dxf_models import (
    CrossSectionAndEmbankmentReport,
    DwgConversionReport,
    FoundationAndSlabQtoReport,
    FoundationElementInput,
    InspectCivilDxfReport,
    LongitudinalProfileReport,
    ManholeOptimizationReport,
    ManholeSpecInput,
    MeasureFeaturesReport,
    PavementCalculationReport,
    PavementLayerInput,
    PdfConversionReport,
    ProfileCrossingInput,
    ProfileStationInput,
    RoadCrossSectionSegmentInput,
    SpotElevationPoint,
    TinEarthworkReport,
    TrenchEarthworkAndBeddingReport,
    TrenchManholePitInput,
    TrenchPipeSegmentInput,
    UtilityNetworkReport,
    VisualOverlayReport,
)
from civil_qto_mcp.resources_and_prompts import register_resources_and_prompts

# -----------------------------------------------------------------------------
# STRICT STDIO PROTECTION: Force all logs and Python warnings to sys.stderr
# so JSON-RPC frames on sys.stdout are never corrupted.
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
    force=True,
)


def _stderr_showwarning(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: Any = None,
    line: str | None = None,
) -> None:
    sys.stderr.write(warnings.formatwarning(message, category, filename, lineno, line))


warnings.showwarning = _stderr_showwarning


SERVER_INSTRUCTIONS = """
civil-qto-mcp: Deterministic Civil Engineering Quantity Take-Off (DXF) & BoQ Audit (XLSX/XLSM) Server.

MANDATORY 3-STEP HUMAN-IN-THE-LOOP (HITL) WORKFLOW:
1. DISCOVERY & CAD HYGIENE: Always call `inspect_civil_dxf` FIRST on any DXF drawing.
   Never guess layer names or drawing units.
2. HUMAN CONFIRMATION: Present the discovered layers (with sample texts and block names),
   the detected coordinate system (e.g., Hungarian EOV HD72 vs. local coordinates),
   the recommended `unit_scale_to_meter` (1.0 for m, 0.01 for cm, 0.001 for mm), and any
   CAD health warnings to the engineer for confirmation.
3. DETERMINISTIC QTO & BOQ CROSS-CHECK: Once confirmed, call `measure_linear_and_area_features`,
   `extract_utility_network_and_levels`, `inspect_and_audit_boq_xlsx`, and `compare_qto_with_boq`.
   Never perform mental arithmetic when comparing quantities or auditing totals.
""".strip()

mcp = MCPServer("civil-qto-mcp", instructions=SERVER_INSTRUCTIONS)


@mcp.tool()
def inspect_civil_dxf(
    file_path: Annotated[
        str,
        Field(description="Absolute or relative path to the .dxf drawing file."),
    ],
    ignore_empty_layers: Annotated[
        bool,
        Field(description="If True (default), omits layers that contain 0 Modelspace entities."),
    ] = True,
    layer_filter_regex: Annotated[
        str | None,
        Field(
            description="Optional case-insensitive regular expression to filter layer names (useful on drawings with 200+ layers)."
        ),
    ] = None,
    max_sample_texts: Annotated[
        int,
        Field(
            description="Maximum number of sample cleaned TEXT/MTEXT/ATTRIB strings to return per layer.",
            ge=1,
            le=25,
        ),
    ] = 5,
) -> InspectCivilDxfReport:
    """
    [STEP 1 of HITL Workflow] Inspect a civil engineering DXF drawing before any measurement.

    Returns:
    - Modelspace vs. Paperspace entity counts and 3D bounding box
    - 3-factor CRS & Drawing Unit estimation (auto-detects Hungarian EOV HD72 metric coordinates,
      checks $INSUNITS header mismatches, and analyzes median text heights)
    - Per-layer entity type breakdown, closed/open/nearly-closed polyline counts, HATCH counts,
      distinct INSERT block names, block ATTRIB tag names, and cleaned sample texts
    - CAD Hygiene Warnings (Civil 3D proxy objects, external XREFs, 3D 'Flying Z' elevation jumps,
      nearly-closed polylines, and duplicate segments)

    After calling this tool, present the layer mapping and `recommended_unit_scale_to_meter`
    to the user for confirmation before calling `measure_linear_and_area_features` or
    `extract_utility_network_and_levels`.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _inspect_civil_dxf(
            file_path=file_path,
            ignore_empty_layers=ignore_empty_layers,
            layer_filter_regex=layer_filter_regex,
            max_sample_texts=max_sample_texts,
        )


@mcp.tool()
def measure_linear_and_area_features(
    file_path: Annotated[
        str,
        Field(description="Path to the .dxf drawing file."),
    ],
    unit_scale_to_meter: Annotated[
        float,
        Field(
            description="Confirmed multiplier converting 1.0 CAD unit to meters (e.g., 1.0 for m, 0.01 for cm, 0.001 for mm).",
            gt=0.0,
        ),
    ] = 1.0,
    linear_layers: Annotated[
        list[str] | None,
        Field(
            description="Layer names to measure for linear features (curbs, edges, utility lines, fences) in meters (m)."
        ),
    ] = None,
    boundary_layers: Annotated[
        list[str] | None,
        Field(
            description="Layer names containing outer closed boundaries or HATCHes for surface/pavement area measurement in square meters (m2)."
        ),
    ] = None,
    island_hole_layers: Annotated[
        list[str] | None,
        Field(
            description="Layer names containing internal islands/cutouts (e.g., green islands, tree pits) to subtract from `boundary_layers`."
        ),
    ] = None,
    include_hatches: Annotated[
        bool,
        Field(description="Whether to extract area polygons from HATCH entities on boundary/island layers."),
    ] = True,
    deduplicate_linear_overlaps: Annotated[
        bool,
        Field(
            description="If True (default), uses 1D Shapely topological union to deduct exact and partial overlapping segments."
        ),
    ] = True,
    snap_tolerance_m: Annotated[
        float,
        Field(
            description="Snap tolerance in meters for colinear overlap deduplication and exploded line polygonization.",
            ge=0.0,
            le=1.0,
        ),
    ] = 0.01,
    auto_close_gap_tolerance_m: Annotated[
        float,
        Field(
            description="Maximum start-to-end gap (in meters) to automatically close 'almost closed' polylines on area layers.",
            ge=0.0,
            le=2.0,
        ),
    ] = 0.10,
    min_island_deduction_m2: Annotated[
        float,
        Field(
            description="VOB/C (DIN 18318) threshold: islands smaller than this area (m2) are NOT deducted from pavement area (0.0 = deduct all).",
            ge=0.0,
        ),
    ] = 0.0,
    explode_inserts: Annotated[
        bool,
        Field(description="If True, virtually explodes INSERT block references on target layers."),
    ] = True,
) -> MeasureFeaturesReport:
    """
    [STEP 3A of HITL Workflow] Deterministically measure linear lengths (m) and net surface areas (m2).

    Engineering guarantees:
    - Enforces 2D horizontal projection (Z=0) so 3D 'Flying Z' vertices never distort lengths
    - Analytical circular arc (`bulge`) length calculation + sagitta-bounded spline/ellipse discretization
    - 1D topological deduplication of exact and partially overlapping lines
    - 2D Shapely boolean `.difference()` for island subtraction (guaranteed immunity against
      double-deduction even when HATCH entities already contain internal hole loops)
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _measure_linear_and_area_features(
            file_path=file_path,
            unit_scale_to_meter=unit_scale_to_meter,
            linear_layers=linear_layers,
            boundary_layers=boundary_layers,
            island_hole_layers=island_hole_layers,
            include_hatches=include_hatches,
            deduplicate_linear_overlaps=deduplicate_linear_overlaps,
            snap_tolerance_m=snap_tolerance_m,
            auto_close_gap_tolerance_m=auto_close_gap_tolerance_m,
            min_island_deduction_m2=min_island_deduction_m2,
            explode_inserts=explode_inserts,
        )


@mcp.tool()
def extract_utility_network_and_levels(
    file_path: Annotated[
        str,
        Field(description="Path to the .dxf drawing file."),
    ],
    unit_scale_to_meter: Annotated[
        float,
        Field(
            description="Confirmed multiplier converting 1.0 CAD unit to meters (1.0 for m, 0.01 for cm, 0.001 for mm).",
            gt=0.0,
        ),
    ] = 1.0,
    node_layers: Annotated[
        list[str] | None,
        Field(
            description="Layers containing manholes, gullies, or chambers (INSERT blocks, CIRCLEs, small closed box polylines)."
        ),
    ] = None,
    pipe_layers: Annotated[
        list[str] | None,
        Field(description="Layers containing utility pipe/conduit centerlines."),
    ] = None,
    text_layers: Annotated[
        list[str] | None,
        Field(
            description="Layers containing callout texts (TEXT, MTEXT) and leader lines (LEADER, LINE). If None, scans all layers."
        ),
    ] = None,
    search_radius_m: Annotated[
        float,
        Field(
            description="Maximum spatial proximity radius (m) to associate callout texts with manholes or pipes.",
            gt=0.0,
        ),
    ] = 8.0,
    cover_thickness_deduction_m: Annotated[
        float,
        Field(
            description="Cast-iron cover/frame thickness (m) deducted from total depth h1 to get net shaft height h2 (default 0.17m for D400).",
            ge=0.0,
        ),
    ] = 0.17,
    calculate_trench_earthworks: Annotated[
        bool,
        Field(description="Whether to calculate DIN 4124 / MSZ EN 1610 trench earthwork and bedding volumes."),
    ] = True,
    fallback_trench_depth_m: Annotated[
        float | None,
        Field(
            description="Optional fallback invert depth (m) to use for trench earthworks when pipe endpoint manholes lack level annotations."
        ),
    ] = None,
    trench_wall_angle_deg: Annotated[
        float,
        Field(
            description="Trench wall angle in degrees per DIN 4124 (90.0 = vertical shored trench, 60.0 or 45.0 = sloped trench).",
            ge=20.0,
            le=90.0,
        ),
    ] = 90.0,
    bedding_thickness_m: Annotated[
        float,
        Field(description="Bedding layer thickness below pipe barrel in meters (default 0.10m per EN 1610)."),
    ] = 0.10,
    side_cover_above_crown_m: Annotated[
        float,
        Field(description="Pipe-zone protective cover above pipe crown in meters (default 0.30m per EN 1610)."),
    ] = 0.30,
    default_pipe_dn_mm: Annotated[
        int,
        Field(description="Default nominal pipe diameter DN (mm) when no text callout specifies DN."),
    ] = 200,
) -> UtilityNetworkReport:
    """
    [STEP 3B of HITL Workflow] Extract utility manholes/gullies, levels, pipe segments, and DIN 4124 / EN 1610 trench earthworks.

    Priority order for manhole data extraction:
    1. `INSERT` block attributes (`insert.attribs` / `ATTRIB` tags for ID, Cover/Rim level, Invert level, DN)
    2. Leader-aware spatial matching (`LEADER` / pointer line arrow tips pointing to manholes)
    3. `STRtree` spatial proximity matching within `search_radius_m` (supporting HU, DE, and EN callout syntax)
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _extract_utility_network_and_levels(
            file_path=file_path,
            unit_scale_to_meter=unit_scale_to_meter,
            node_layers=node_layers,
            pipe_layers=pipe_layers,
            text_layers=text_layers,
            search_radius_m=search_radius_m,
            cover_thickness_deduction_m=cover_thickness_deduction_m,
            calculate_trench_earthworks=calculate_trench_earthworks,
            fallback_trench_depth_m=fallback_trench_depth_m,
            trench_wall_angle_deg=trench_wall_angle_deg,
            bedding_thickness_m=bedding_thickness_m,
            side_cover_above_crown_m=side_cover_above_crown_m,
            default_pipe_dn_mm=default_pipe_dn_mm,
        )


@mcp.tool()
def inspect_and_audit_boq_xlsx(
    file_path: Annotated[
        str,
        Field(description="Path to the .xlsx or .xlsm Bill of Quantities workbook."),
    ],
    sheet_names: Annotated[
        list[str] | None,
        Field(description="Optional list of specific visible worksheet names to inspect."),
    ] = None,
    keyword_filter: Annotated[
        str | None,
        Field(
            description="Optional case-insensitive regex/keyword filter on item description, item number, or code to reduce returned rows."
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            description="Maximum number of BoQ line items to return in `items` (protects LLM context window on 1000+ row BoQs).",
            ge=1,
            le=2000,
        ),
    ] = 200,
    offset: Annotated[
        int,
        Field(description="Pagination offset for returned BoQ line items.", ge=0),
    ] = 0,
) -> BoQAuditReport:
    """
    [STEP 3C of HITL Workflow] Safely parse and mathematically audit an Excel (.xlsx / .xlsm) Bill of Quantities.

    Safety & Auditing Features:
    - Strictly skips `hidden` and `veryHidden` worksheets (listing them in `skipped_hidden_sheets`)
    - Strictly skips hidden rows (`row_dimensions[r].hidden`)
    - Automatically detects Single-Price and Hungarian TERC Split (Material + Labor) column layouts
    - Audits 100% of rows on selected visible sheets for `MATH_MISMATCH`, `HARDCODED_TOTAL`,
      `SUM_RANGE_GAP`, `SHARED_FORMULA_RISK`, `ZERO_QUANTITY`, and `ZERO_UNIT_PRICE`
      (even when `keyword_filter` or `limit` paginates the returned `items` list)
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _inspect_and_audit_boq_xlsx(
            file_path=file_path,
            sheet_names=sheet_names,
            keyword_filter=keyword_filter,
            limit=limit,
            offset=offset,
        )


@mcp.tool()
def compare_qto_with_boq(
    file_path_xlsx: Annotated[
        str,
        Field(description="Path to the .xlsx or .xlsm Bill of Quantities file."),
    ],
    qto_records: Annotated[
        list[QtoInputRecord],
        Field(
            description="List of deterministic CAD QTO measurements (with qto_id, measured_quantity, measured_unit, and target item_number/row/keyword)."
        ),
    ],
    sheet_names: Annotated[
        list[str] | None,
        Field(description="Optional list of visible worksheet names to search within the BoQ."),
    ] = None,
    tolerance_percent: Annotated[
        float,
        Field(
            description="Percentage threshold within which a difference is classified as WITHIN_TOLERANCE (default 2.0%).",
            ge=0.0,
        ),
    ] = 2.0,
    critical_percent: Annotated[
        float,
        Field(
            description="Percentage threshold above which a difference is classified as CRITICAL_DISCREPANCY (default 10.0%).",
            ge=0.0,
        ),
    ] = 10.0,
) -> CompareQtoBoQReport:
    """
    [STEP 3D of HITL Workflow] Deterministically cross-check CAD QTO measurements against Excel BoQ items.

    Automatically normalizes construction unit synonyms and scale factors:
    - Length: `m` == `fm` == `lm` == `folyóméter`, `km` == `1000 m`, `cm` == `0.01 m`, `mm` == `0.001 m`
    - Area: `m2` == `m²` == `nm` == `sqm`, `ha` == `10,000 m2`
    - Volume: `m3` == `m³` == `lm3` == `bm3` == `tm3` == `cum`
    - Count: `pcs` == `db` == `darab` == `akna` == `klt` == `készlet` == `nr` == `ea`
    """
    with contextlib.redirect_stdout(sys.stderr):
        boq_report = _inspect_and_audit_boq_xlsx(
            file_path=file_path_xlsx,
            sheet_names=sheet_names,
            limit=10_000,
            offset=0,
        )
        return _compare_qto_with_boq(
            qto_records=qto_records,
            boq_items=boq_report.items,
            tolerance_percent=tolerance_percent,
            critical_percent=critical_percent,
        )


@mcp.tool()
def convert_dwg_to_dxf(
    source_path: Annotated[
        str,
        Field(description="Path to a .dwg file or a directory containing .dwg files."),
    ],
    output_dir: Annotated[
        str | None,
        Field(
            description="Optional output directory for converted .dxf files. If None, writes side-by-side with source .dwg."
        ),
    ] = None,
    target_dxf_version: Annotated[
        str,
        Field(description="Target AutoCAD DXF version (default 'R2018')."),
    ] = "R2018",
    use_cache: Annotated[
        bool,
        Field(description="If True (default), reuses SHA-256 cached conversions."),
    ] = True,
) -> DwgConversionReport:
    """
    [v0.2 Tool 6] Convert a single .dwg file or an entire folder of .dwg files into .dxf format.
    Note: `inspect_civil_dxf`, `measure_linear_and_area_features`, and `extract_utility_network_and_levels`
    also accept `.dwg` paths directly via transparent background caching.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _convert_dwg_to_dxf(
            source_path=source_path,
            output_dir=output_dir,
            target_dxf_version=target_dxf_version,
            use_cache=use_cache,
        )


@mcp.tool()
def optimize_precast_manhole_elements(
    manholes: Annotated[
        list[ManholeSpecInput] | None,
        Field(
            description="Explicit list of manhole/gully specifications (node_id, cover_level_m, invert_level_m or total_depth_h1_m, nominal_diameter_mm, surface_type)."
        ),
    ] = None,
    file_path_cad: Annotated[
        str | None,
        Field(
            description="Optional path to a .dxf or .dwg file to automatically extract manholes and levels before optimizing precast rings."
        ),
    ] = None,
    node_layers: Annotated[
        list[str] | None,
        Field(description="When file_path_cad is used, layers containing manholes/gullies."),
    ] = None,
    text_layers: Annotated[
        list[str] | None,
        Field(description="When file_path_cad is used, layers containing level callout texts."),
    ] = None,
    unit_scale_to_meter: Annotated[
        float,
        Field(description="When file_path_cad is used, CAD unit scale to meters (default 1.0).", gt=0.0),
    ] = 1.0,
    default_surface_type: Annotated[
        Literal["ASPHALT_OR_ROAD", "PAVING_BLOCK", "GREEN_OR_UNPAVED"],
        Field(
            description="Default surface finish for CAD-extracted manholes (PAVING_BLOCK selects 600x600mm D400 HYDROTEC square-frame cover)."
        ),
    ] = "ASPHALT_OR_ROAD",
    catalog_profile: Annotated[
        Literal["DIN_EN_1917_STD", "ASTM_C478_US", "SZEBETON_T2_HU"],
        Field(
            description=(
                "Precast element catalog: 'DIN_EN_1917_STD' (European EN 1917 / DIN 4034), "
                "'ASTM_C478_US' (North American ASTM C478), or 'SZEBETON_T2_HU' (Hungarian SzeBeton T2)."
            )
        ),
    ] = "SZEBETON_T2_HU",
    cover_frame_height_cm: Annotated[
        float,
        Field(
            description="Cast-iron cover frame height in cm deducted from total depth h1 (default 17.0 cm: h2 = h1 - 17 cm).",
            ge=0.0,
        ),
    ] = 17.0,
    spare_adjustment_ring_per_manhole: Annotated[
        bool,
        Field(
            description="If True (default), adds +1 pc of SZB 60/10 adjustment ring per circular manhole to the order BOM."
        ),
    ] = True,
) -> ManholeOptimizationReport:
    """
    [v0.2 Tool 7] Decompose manholes and road gullies into optimal precast concrete elements
    (base AFE, cone ASZ or shallow flat slab AFT, shaft rings AGY 100/75/50/25 cm, adjustment rings SZB 60/10, 60/8, 60/6 cm,
    and D400 covers/grates) and generate a consolidated manufacturer RFQ Bill of Materials (BOM).
    """
    with contextlib.redirect_stdout(sys.stderr):
        spec_list: list[ManholeSpecInput] = list(manholes or [])
        if file_path_cad:
            net_rep = _extract_utility_network_and_levels(
                file_path=file_path_cad,
                unit_scale_to_meter=unit_scale_to_meter,
                node_layers=node_layers,
                text_layers=text_layers,
                cover_thickness_deduction_m=cover_frame_height_cm / 100.0,
                calculate_trench_earthworks=False,
            )
            for n in net_rep.nodes:
                if n.depth_h1_m is not None and n.depth_h1_m > 0:
                    n_type: Literal["MANHOLE", "GULLY_50X50"] = (
                        "GULLY_50X50"
                        if (
                            n.source_type == "POLYLINE_BOX"
                            or (n.nominal_diameter_mm is not None and n.nominal_diameter_mm <= 600)
                        )
                        else "MANHOLE"
                    )
                    spec_list.append(
                        ManholeSpecInput(
                            node_id=n.node_id,
                            cover_level_m=n.cover_level_m,
                            invert_level_m=n.invert_level_m,
                            total_depth_h1_m=n.depth_h1_m,
                            nominal_diameter_mm=n.nominal_diameter_mm or 1000,
                            node_type=n_type,
                            surface_type=default_surface_type,
                        )
                    )

        return _optimize_precast_manhole_elements(
            manholes=spec_list,
            catalog_profile=catalog_profile,
            cover_frame_height_cm=cover_frame_height_cm,
            spare_adjustment_ring_per_manhole=spare_adjustment_ring_per_manhole,
        )


@mcp.tool()
def calculate_pavement_layer_quantities(
    net_surface_area_m2: Annotated[
        float,
        Field(description="Net surface area of the top pavement course in m2.", gt=0.0),
    ],
    active_edge_perimeter_m: Annotated[
        float,
        Field(
            description="Outer boundary perimeter (m) along which base courses widen laterally (step-out).",
            ge=0.0,
        ),
    ] = 0.0,
    preset_name: Annotated[
        Literal[
            "LIDL_BBS_PARKING_PAVING", "HEAVY_INDUSTRIAL_PAVING", "URBAN_ASPHALT_ROAD"
        ]
        | None,
        Field(
            description="Standard pavement structure preset (used when `custom_layers` is not provided)."
        ),
    ] = "LIDL_BBS_PARKING_PAVING",
    custom_layers: Annotated[
        list[PavementLayerInput] | None,
        Field(
            description="Optional custom list of structural layers from top to bottom (overrides `preset_name`)."
        ),
    ] = None,
) -> PavementCalculationReport:
    """
    [v0.2 Tool 8] Calculate structural layer quantities (m2, m3, metric tons) for road and paving works,
    accounting for lateral step-out widening beyond curb lines, compaction multipliers (e.g., CKT-4 x1.20),
    and cutting/overlap waste.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _calculate_pavement_layer_quantities(
            net_surface_area_m2=net_surface_area_m2,
            active_edge_perimeter_m=active_edge_perimeter_m,
            preset_name=preset_name,
            custom_layers=custom_layers,
        )


# =============================================================================
# v0.3 TOOLS: Vector PDF Importer, Longitudinal Profile Parser, BoQ Writer & Overlay
# =============================================================================


@mcp.tool()
def convert_vector_pdf_to_dxf(
    pdf_path: Annotated[
        str,
        Field(description="Absolute path to the vector PDF plan (or already imported DXF) to convert and calibrate."),
    ],
    page_number: Annotated[
        int,
        Field(description="1-based PDF page number to import.", ge=1),
    ] = 1,
    output_dxf_path: Annotated[
        str | None,
        Field(description="Optional output DXF path. Defaults to system temp cache."),
    ] = None,
    paper_scale_denominator: Annotated[
        int | None,
        Field(description="Optional explicit paper scale denominator (e.g., 250 for 1:250, 500 for 1:500)."),
    ] = None,
    reference_cad_length: Annotated[
        float | None,
        Field(description="Optional measured CAD length of a known dimension line for exact calibration."),
    ] = None,
    reference_real_meters: Annotated[
        float | None,
        Field(description="Optional real-world length in meters corresponding to `reference_cad_length`."),
    ] = None,
    pdf_import_base_unit: Annotated[
        Literal["inch", "mm", "m"],
        Field(description="Base unit produced by PDF import (AutoCAD _-PDFIMPORT defaults to 'inch' = 0.0254m)."),
    ] = "inch",
) -> PdfConversionReport:
    """
    [v0.3 Tool 9] Convert a vector PDF civil engineering plan into a layered DXF drawing via AutoCAD
    Core Console (`_-PDFIMPORT`) and automatically calibrate `recommended_scale_factor_to_meters`
    from reference dimensions, explicit scale denominator, or title-block scale text ('M=1:250').
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _convert_vector_pdf_to_dxf(
            pdf_path=pdf_path,
            page_number=page_number,
            output_dxf_path=output_dxf_path,
            paper_scale_denominator=paper_scale_denominator,
            reference_cad_length=reference_cad_length,
            reference_real_meters=reference_real_meters,
            pdf_import_base_unit=pdf_import_base_unit,
        )


@mcp.tool()
def extract_longitudinal_profile_data(
    profile_name: Annotated[
        str,
        Field(description="Identifier or title of the longitudinal profile (hossz-szelvény)."),
    ] = "Utility Longitudinal Profile",
    nominal_diameter_dn_mm: Annotated[
        int,
        Field(description="Nominal pipe diameter DN in mm (e.g., 160, 200, 300).", gt=0),
    ] = 200,
    stations: Annotated[
        list[ProfileStationInput] | None,
        Field(description="Ordered list of profile station points (chainage, ground level, invert level)."),
    ] = None,
    crossings: Annotated[
        list[ProfileCrossingInput] | None,
        Field(description="List of utility crossings along the profile requiring hand excavation."),
    ] = None,
    dxf_path: Annotated[
        str | None,
        Field(description="Optional path to a longitudinal profile DXF to auto-extract station bands."),
    ] = None,
    trench_width_override_m: Annotated[
        float | None,
        Field(description="Optional explicit trench width in meters (overrides DIN 4124 / EN 1610 auto-width)."),
    ] = None,
    bedding_thickness_m: Annotated[
        float,
        Field(description="Bedding thickness below pipe invert in meters (default 0.15m).", ge=0.0),
    ] = 0.15,
    side_cover_above_crown_m: Annotated[
        float,
        Field(description="Pipe-zone sand cover above pipe crown in meters (default 0.30m).", ge=0.0),
    ] = 0.30,
    min_cover_threshold_m: Annotated[
        float,
        Field(
            description="Minimum crown cover threshold in meters below which C12/15 concrete encasement is required (default 1.00m)."
        ),
    ] = 1.00,
    concrete_encase_thickness_m: Annotated[
        float,
        Field(description="Concrete encasement wall thickness around pipe in meters (default 0.15m).", gt=0.0),
    ] = 0.15,
    shoring_depth_threshold_m: Annotated[
        float,
        Field(description="Trench depth threshold in meters above which two-sided shoring is calculated (default 1.25m)."),
    ] = 1.25,
) -> LongitudinalProfileReport:
    """
    [v0.3 Tool 10] Analyze a utility longitudinal profile (hossz-szelvény) to compute trench excavation,
    machine vs. hand excavation split at utility crossings, two-sided shoring area, and shallow-cover
    (<1.00 m) C12/15 concrete encasement volumes.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _extract_longitudinal_profile_data(
            profile_name=profile_name,
            nominal_diameter_dn_mm=nominal_diameter_dn_mm,
            stations=stations,
            crossings=crossings,
            dxf_path=dxf_path,
            trench_width_override_m=trench_width_override_m,
            bedding_thickness_m=bedding_thickness_m,
            side_cover_above_crown_m=side_cover_above_crown_m,
            min_cover_threshold_m=min_cover_threshold_m,
            concrete_encase_thickness_m=concrete_encase_thickness_m,
            shoring_depth_threshold_m=shoring_depth_threshold_m,
        )


@mcp.tool()
def apply_boq_modifications_and_pricing(
    source_xlsx_path: Annotated[
        str,
        Field(description="Absolute path to the source BoQ Excel file (.xlsx)."),
    ],
    output_xlsx_path: Annotated[
        str,
        Field(description="Absolute path where the modified and priced BoQ Excel file will be written."),
    ],
    row_pricings: Annotated[
        list[BoQRowPricingInput] | None,
        Field(description="List of unit prices and optional quantity/note modifications for existing rows."),
    ] = None,
    supplemental_items: Annotated[
        list[BoQSupplementalItemInput] | None,
        Field(description="List of new K-tétel items or '-'/'+' kiv. észrevétel observation pairs to insert."),
    ] = None,
    markup_factor: Annotated[
        float,
        Field(description="Global multiplier applied to unit prices (e.g., 1.0 for base, 1.20 for +20% markup).", gt=0.0),
    ] = 1.0,
    round_step: Annotated[
        float,
        Field(
            description="Rounding step for unit prices after markup (default 0.01 for EUR/USD/GBP; pass 10.0 for HUF).",
            ge=0.0,
        ),
    ] = 0.01,
) -> BoQModificationReport:
    """
    [v0.3 Tool 11] Safely write unit prices (with optional markup_factor), red-bold quantity modifications,
    supplemental K-tételek, and '-'/'+' kiv. észrevétel pairs into an Excel BoQ workbook while updating
    SUM ranges, XML `<v>` cached values, and stripping `xl/calcChain.xml`.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _apply_boq_modifications_and_pricing(
            source_xlsx_path=source_xlsx_path,
            output_xlsx_path=output_xlsx_path,
            row_pricings=row_pricings,
            supplemental_items=supplemental_items,
            markup_factor=markup_factor,
            round_step=round_step,
        )


@mcp.tool()
def export_qto_visual_overlay(
    file_path: Annotated[
        str,
        Field(description="Absolute path to the DXF or DWG file."),
    ],
    unit_scale_to_meter: Annotated[
        float,
        Field(description="Multiplier converting CAD drawing units to meters.", gt=0.0),
    ] = 1.0,
    boundary_layers: Annotated[
        list[str] | None,
        Field(description="Polygon boundary/hatch layers to render as filled net area surfaces."),
    ] = None,
    island_hole_layers: Annotated[
        list[str] | None,
        Field(description="Island/deduction layers to subtract and highlight in dashed red."),
    ] = None,
    linear_layers: Annotated[
        list[str] | None,
        Field(description="Linear layers (curbs, pipes, edges) to render and label with length (m)."),
    ] = None,
    point_layers: Annotated[
        list[str] | None,
        Field(description="Point/circle/insert layers (manholes, bollards) to render as markers."),
    ] = None,
    output_dir: Annotated[
        str | None,
        Field(description="Optional directory for the generated .html and .svg files."),
    ] = None,
    locale: Annotated[
        Literal["en", "de", "hu"],
        Field(description="UI language for the generated HTML and SVG report ('en', 'de', or 'hu')."),
    ] = "en",
) -> VisualOverlayReport:
    """
    [v0.3 Tool 12] Export an interactive HTML and SVG visual verification map showing all measured
    polygons, deducted islands, linear features, and point nodes labeled with their IDs and m2/m quantities.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _export_qto_visual_overlay(
            file_path=file_path,
            unit_scale_to_meter=unit_scale_to_meter,
            boundary_layers=boundary_layers,
            island_hole_layers=island_hole_layers,
            linear_layers=linear_layers,
            point_layers=point_layers,
            output_dir=output_dir,
            locale=locale,
        )


# =============================================================================
# v0.4 TOOLS: Technological Completeness Auditor, Foundation/Slab QTO & Price/RFQ Engine
# =============================================================================


@mcp.tool()
def audit_technological_completeness(
    boq_xlsx_path: Annotated[
        str,
        Field(description="Absolute path to the BoQ Excel workbook (.xlsx / .xlsm) to audit."),
    ],
    sheet_names: Annotated[
        list[str] | None,
        Field(description="Optional list of visible worksheet names to check."),
    ] = None,
    cad_curb_length_m: Annotated[
        float | None,
        Field(description="Optional CAD-measured curb length (m) to populate if curbs are missing from BoQ."),
    ] = None,
    cad_asphalt_tie_in_length_m: Annotated[
        float | None,
        Field(description="Optional CAD-measured asphalt tie-in length (m) for saw-cutting and joint sealing."),
    ] = None,
    cad_shallow_pipe_concrete_m3: Annotated[
        float | None,
        Field(description="Optional C12/15 concrete encasement volume (m3) for shallow pipes (<1.0m cover)."),
    ] = None,
    project_profile: Annotated[
        Literal["STANDARD", "LIDL_BBS", "GENERAL_CONTRACTOR_TENDER"],
        Field(
            description=(
                "Client/project specification profile ('STANDARD', 'LIDL_BBS' for Lidl Building Book "
                "Specification rules, or 'GENERAL_CONTRACTOR_TENDER')."
            )
        ),
    ] = "STANDARD",
    locale: Annotated[
        Literal["en", "de", "hu"],
        Field(description="Language for generated supplemental item descriptions ('en', 'de', or 'hu')."),
    ] = "hu",
) -> TechnologicalCompletenessReport:
    """
    [v0.4 Tool 13] Audit a BoQ workbook against 6 civil engineering technological completeness chains
    (asphalt, block paving, gravity sewers, pressure water lines, RC foundations, industrial slabs)
    plus client profiles (`LIDL_BBS`), generating ready-to-insert `BoQSupplementalItemInput` (K-tétel)
    payloads and numbered Technical Clarification Questions (`technical_clarification_questions`).
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _audit_technological_completeness(
            boq_xlsx_path=boq_xlsx_path,
            sheet_names=sheet_names,
            cad_curb_length_m=cad_curb_length_m,
            cad_asphalt_tie_in_length_m=cad_asphalt_tie_in_length_m,
            cad_shallow_pipe_concrete_m3=cad_shallow_pipe_concrete_m3,
            project_profile=project_profile,
            locale=locale,
        )


@mcp.tool()
def calculate_foundation_and_slab_qto(
    elements: Annotated[
        list[FoundationElementInput] | None,
        Field(
            description=(
                "List of structural foundation or slab elements ('PAD_FOOTING', 'CUP_FOOTING', "
                "'STRIP_FOUNDATION', 'PLINTH_WALL', 'RAFT_SLAB', 'INDUSTRIAL_FLOOR')."
            )
        ),
    ] = None,
    file_path_cad: Annotated[
        str | None,
        Field(description="Optional DXF/DWG path to extract foundation footprint polygons directly."),
    ] = None,
    foundation_layers: Annotated[
        list[str] | None,
        Field(description="When `file_path_cad` is provided, CAD layers containing foundation footprints."),
    ] = None,
    default_cad_element_type: Annotated[
        Literal[
            "PAD_FOOTING",
            "CUP_FOOTING",
            "STRIP_FOUNDATION",
            "PLINTH_WALL",
            "RAFT_SLAB",
            "INDUSTRIAL_FLOOR",
        ],
        Field(description="Element type assigned to CAD-extracted footprints."),
    ] = "PAD_FOOTING",
    default_cad_height_m: Annotated[
        float,
        Field(description="Structural height/thickness in meters for CAD-extracted footprints.", gt=0.0),
    ] = 0.80,
    unit_scale_to_meter: Annotated[
        float,
        Field(description="CAD drawing unit scale to meters (default 1.0).", gt=0.0),
    ] = 1.0,
    blinding_thickness_m: Annotated[
        float,
        Field(description="Blinding concrete thickness in meters (default 0.10m).", ge=0.0),
    ] = 0.10,
    blinding_overhang_m: Annotated[
        float,
        Field(description="Lateral overhang of blinding concrete on each side in meters (default 0.10m).", ge=0.0),
    ] = 0.10,
    working_space_m: Annotated[
        float,
        Field(description="Bottom pit working space around foundations in meters (default 0.50m per DIN 4124).", ge=0.0),
    ] = 0.50,
    batter_angle_deg: Annotated[
        float,
        Field(description="Pit excavation side slope angle in degrees (default 60.0 deg; 90.0 for vertical).", gt=0.0, le=90.0),
    ] = 60.0,
) -> FoundationAndSlabQtoReport:
    """
    [v0.4 Tool 14] Calculate the full structural and earthwork quantity chain for foundations, plinths,
    and industrial floor slabs: structural concrete (m3), blinding concrete with lateral overhang (m3),
    lateral formwork (m2), rebar/fiber tonnage (t), DIN 4124 prismoidal excavation (m3), plinth XPS (m2),
    and saw-cut contraction joints (m).
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _calculate_foundation_and_slab_qto(
            elements=elements,
            file_path_cad=file_path_cad,
            foundation_layers=foundation_layers,
            default_cad_element_type=default_cad_element_type,
            default_cad_height_m=default_cad_height_m,
            unit_scale_to_meter=unit_scale_to_meter,
            blinding_thickness_m=blinding_thickness_m,
            blinding_overhang_m=blinding_overhang_m,
            working_space_m=working_space_m,
            batter_angle_deg=batter_angle_deg,
        )


@mcp.tool()
def match_prices_and_generate_rfq(
    boq_xlsx_path: Annotated[
        str,
        Field(description="Absolute path to the BoQ Excel workbook (.xlsx / .xlsm)."),
    ],
    project_location: Annotated[
        str,
        Field(description="Project city/settlement for regional price routing (e.g., 'Foktő', 'Kecskemét', 'Vecsés', 'Tótkomlós')."),
    ] = "Kecskemét",
    sheet_names: Annotated[
        list[str] | None,
        Field(description="Optional list of visible worksheet names to price."),
    ] = None,
    markup_factor: Annotated[
        float,
        Field(description="Global multiplier applied to matched unit prices (e.g., 1.0 for base, 1.20 for +20% markup).", gt=0.0),
    ] = 1.0,
    round_step: Annotated[
        float,
        Field(description="Rounding step for unit prices (0.01 default for EUR/USD; pass 10.0 for HUF).", ge=0.0),
    ] = 0.01,
    database_dir: Annotated[
        str | None,
        Field(description="Optional directory containing `sajat_egysegar_torzs.json` and `partner_es_alvallalkozo_torzs.json`."),
    ] = None,
) -> PriceMatchAndRfqReport:
    """
    [v0.4 Tool 15] Match BoQ items against regional unit-price databases (Tier 1 own regional DB,
    Tier 2 supplier catalogs, Tier 3 built-in benchmarks), flag transport-sensitive items, produce
    ready-to-apply `BoQRowPricingInput` payloads, and group materials into 6 supplier RFQ packages.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _match_prices_and_generate_rfq(
            boq_xlsx_path=boq_xlsx_path,
            project_location=project_location,
            sheet_names=sheet_names,
            markup_factor=markup_factor,
            round_step=round_step,
            database_dir=database_dir,
        )


# =============================================================================
# v0.5 TOOLS: 3D Delaunay TIN Earthwork Engine & CAD/BoQ Revision Comparator
# =============================================================================


@mcp.tool()
def calculate_tin_earthwork_volumes(
    existing_points: Annotated[
        list[SpotElevationPoint] | None,
        Field(description="List of existing ground spot elevation points (X, Y, Z in meters)."),
    ] = None,
    design_points: Annotated[
        list[SpotElevationPoint] | None,
        Field(description="List of designed surface or subgrade spot elevation points (X, Y, Z in meters)."),
    ] = None,
    constant_design_elevation_m: Annotated[
        float | None,
        Field(description="Optional constant horizontal design elevation [mBf] if `design_points` is not provided."),
    ] = None,
    file_path_cad: Annotated[
        str | None,
        Field(description="Optional DXF/DWG path to extract spot elevation points and boundary polygons."),
    ] = None,
    existing_elevation_layers: Annotated[
        list[str] | None,
        Field(description="CAD layers containing existing ground elevation POINT/INSERT/TEXT entities."),
    ] = None,
    design_elevation_layers: Annotated[
        list[str] | None,
        Field(description="CAD layers containing designed surface elevation POINT/INSERT/TEXT entities."),
    ] = None,
    boundary_layers: Annotated[
        list[str] | None,
        Field(description="CAD layers defining the earthwork/grading boundary polygon."),
    ] = None,
    unit_scale_to_meter: Annotated[
        float,
        Field(description="CAD drawing unit scale to meters (default 1.0).", gt=0.0),
    ] = 1.0,
    topsoil_strip_thickness_m: Annotated[
        float,
        Field(description="Topsoil stripping thickness in meters (e.g., 0.10m or 0.20m).", ge=0.0),
    ] = 0.15,
    pavement_structure_thickness_m: Annotated[
        float,
        Field(
            description=(
                "Total pavement structure thickness (m) deducted from finished design surface "
                "to reach the subgrade mirror level (tükörszint). Pass 0.0 if design_points are already subgrade levels."
            ),
            ge=0.0,
        ),
    ] = 0.0,
    fill_compaction_factor: Annotated[
        float,
        Field(description="Compaction multiplier for required borrow fill (default 1.15).", gt=0.0),
    ] = 1.15,
) -> TinEarthworkReport:
    """
    [v0.5 Tool 16] Compute 3D Delaunay TIN surface earthwork volumes (topsoil stripping, cut, fill,
    and net earth balance) between existing terrain and designed subgrade level directly from
    CAD spot-elevation layers or 3D point clouds (without Civil 3D).
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _calculate_tin_earthwork_volumes(
            existing_points=existing_points,
            design_points=design_points,
            constant_design_elevation_m=constant_design_elevation_m,
            file_path_cad=file_path_cad,
            existing_elevation_layers=existing_elevation_layers,
            design_elevation_layers=design_elevation_layers,
            boundary_layers=boundary_layers,
            unit_scale_to_meter=unit_scale_to_meter,
            topsoil_strip_thickness_m=topsoil_strip_thickness_m,
            pavement_structure_thickness_m=pavement_structure_thickness_m,
            fill_compaction_factor=fill_compaction_factor,
        )


@mcp.tool()
def compare_drawing_and_boq_revisions(
    old_cad_path: Annotated[
        str | None,
        Field(description="Optional path to the Old / Design (R0) DXF or DWG drawing."),
    ] = None,
    new_cad_path: Annotated[
        str | None,
        Field(description="Optional path to the New / Revised (R1) or As-Built (Megvalósulási) DXF or DWG drawing."),
    ] = None,
    boundary_layers: Annotated[
        list[str] | None,
        Field(description="Polygon area layers to compare between Old and New CAD drawings."),
    ] = None,
    linear_layers: Annotated[
        list[str] | None,
        Field(description="Linear layers to compare between Old and New CAD drawings."),
    ] = None,
    point_layers: Annotated[
        list[str] | None,
        Field(description="Point/circle/insert layers to compare between Old and New CAD drawings."),
    ] = None,
    unit_scale_to_meter: Annotated[
        float,
        Field(description="CAD drawing unit scale to meters (default 1.0).", gt=0.0),
    ] = 1.0,
    old_boq_xlsx_path: Annotated[
        str | None,
        Field(description="Optional path to the Old / Baseline BoQ Excel workbook (.xlsx)."),
    ] = None,
    new_boq_xlsx_path: Annotated[
        str | None,
        Field(description="Optional path to the New / Revised BoQ Excel workbook (.xlsx)."),
    ] = None,
    sheet_names: Annotated[
        list[str] | None,
        Field(description="Optional list of visible BoQ worksheet names to compare."),
    ] = None,
    locale: Annotated[
        Literal["en", "de", "hu"],
        Field(description="Language for generated variation order notes ('en', 'de', or 'hu')."),
    ] = "hu",
) -> RevisionComparisonReport:
    """
    [v0.5 Tool 17] Compare Old vs. New (or Design vs. As-Built) CAD drawings and/or BoQ Excel workbooks,
    calculating spatial area additions/removals (`New - Old` and `Old - New`), linear/point deltas,
    row-level BoQ diffs, and ready-to-apply variation order (`kiv. észrevétel` / `K-tétel`) payloads.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _compare_drawing_and_boq_revisions(
            old_cad_path=old_cad_path,
            new_cad_path=new_cad_path,
            boundary_layers=boundary_layers,
            linear_layers=linear_layers,
            point_layers=point_layers,
            unit_scale_to_meter=unit_scale_to_meter,
            old_boq_xlsx_path=old_boq_xlsx_path,
            new_boq_xlsx_path=new_boq_xlsx_path,
            sheet_names=sheet_names,
            locale=locale,
        )


# =============================================================================
# v0.6 TOOLS: Detailed Utility Trench Earthwork, Bedding, Backfill & Shoring
# =============================================================================


@mcp.tool()
def calculate_trench_earthwork_and_bedding(
    pipe_segments: Annotated[
        list[TrenchPipeSegmentInput] | None,
        Field(
            description=(
                "List of utility pipe segments with nominal diameter (DN), material, length, "
                "start/end depth to invert, trench wall mode ('VERTICAL_SHORED' or 'SLOPED_UNSHORED'), "
                "lower bedding thickness, and protective cover zone."
            )
        ),
    ] = None,
    manhole_pits: Annotated[
        list[TrenchManholePitInput] | None,
        Field(
            description=(
                "Optional list of manhole or road gully pits along the trench to compute pit excavation, "
                "under-base bedding, structure volume displacement, and pit shoring."
            )
        ),
    ] = None,
    dxf_path: Annotated[
        str | None,
        Field(
            description=(
                "Optional DXF/DWG path: if `pipe_segments` is omitted, automatically extracts utility "
                "pipe spans and manholes from the CAD drawing."
            )
        ),
    ] = None,
    unit_scale_to_meter: Annotated[
        float,
        Field(description="CAD drawing unit scale to meters when `dxf_path` is used (default 1.0).", gt=0.0),
    ] = 1.0,
    standard_profile: Annotated[
        Literal["EN_1610_DIN_4124", "HU_MSZ_EN_1610_TERC"],
        Field(description="Trench geometry and depth-banding standard ('EN_1610_DIN_4124' or 'HU_MSZ_EN_1610_TERC')."),
    ] = "HU_MSZ_EN_1610_TERC",
    shoring_depth_threshold_m: Annotated[
        float,
        Field(description="Trench depth threshold in meters above which two-sided shoring is calculated (default 1.25m).", ge=0.0),
    ] = 1.25,
    hand_excavation_ratio: Annotated[
        float,
        Field(description="Base fraction of trench excavation performed by hand (e.g., 0.15 for 85%/15% machine/hand split).", ge=0.0, le=1.0),
    ] = 0.15,
    crossing_hand_zone_length_m: Annotated[
        float,
        Field(description="Total length of 100% hand-excavated protection zone per utility crossing in meters (default 2.0m).", ge=0.0),
    ] = 2.0,
    bedding_compaction_factor: Annotated[
        float,
        Field(description="Compaction and trench-wall loss factor for ordering sand/gravel bedding (default 1.15).", gt=0.0),
    ] = 1.15,
    bedding_bulk_density_t_per_m3: Annotated[
        float,
        Field(description="Bulk density (t/m3) of sand/gravel bedding for quarry tonnage conversion (default 1.75 t/m3).", gt=0.0),
    ] = 1.75,
    default_pavement_box_deduction_m: Annotated[
        float,
        Field(
            description="Default pavement structure box thickness (m) deducted from top of trench when auto-extracting from DXF.",
            ge=0.0,
        ),
    ] = 0.0,
) -> TrenchEarthworkAndBeddingReport:
    """
    [v0.6 Tool 18] Calculate complete MSZ EN 1610 / DIN 4124 utility trench and manhole-pit earthworks:
    depth-banded excavation (`0-1.5m`, `1.5-3.0m`, `>3.0m`), machine vs. hand excavation split,
    lower bedding (`m3`), pipe-zone protective cover (`m3`), exact pipe & manhole volume displacement (`m3`),
    native soil backfill (`m3`), excess displaced spoil haul-off (`m3`), two-sided trench shoring (`m2`),
    warning tape (`m`), and geotextile trench wrapping (`m2`).
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _calculate_trench_earthwork_and_bedding(
            pipe_segments=pipe_segments,
            manhole_pits=manhole_pits,
            dxf_path=dxf_path,
            unit_scale_to_meter=unit_scale_to_meter,
            standard_profile=standard_profile,
            shoring_depth_threshold_m=shoring_depth_threshold_m,
            hand_excavation_ratio=hand_excavation_ratio,
            crossing_hand_zone_length_m=crossing_hand_zone_length_m,
            bedding_compaction_factor=bedding_compaction_factor,
            bedding_bulk_density_t_per_m3=bedding_bulk_density_t_per_m3,
            default_pavement_box_deduction_m=default_pavement_box_deduction_m,
        )


@mcp.tool()
def compare_supplier_quotes_and_build_bid_tab(
    target_items: Annotated[
        list[BidTabTargetItemInput],
        Field(
            description=(
                "List of required procurement items (`item_id`, `description`, `unit`, `required_quantity`, "
                "and optional `unit_weight_kg`, `units_per_pallet`, `boq_sheet_name`, `boq_row_number`)."
            )
        ),
    ],
    supplier_quotes: Annotated[
        list[SupplierQuoteInput],
        Field(
            description=(
                "List of supplier quote submissions (`supplier_name`, `lines` or `quote_file_path`, "
                "`global_discount_percent`, `freight_mode`, `freight_rate`, `unloading_fee_per_pallet`, "
                "`non_refundable_pallet_fee_per_pallet`, `payment_terms`, `notes_and_conditions`)."
            )
        ),
    ],
    package_name: Annotated[
        str,
        Field(description="Procurement package name displayed on the Bid Tabulation header."),
    ] = "Térburkolat és Szegély Beszerzési Csomag",
    currency: Annotated[
        str,
        Field(description="Currency code (e.g., 'HUF', 'EUR', 'USD')."),
    ] = "HUF",
    output_xlsx_path: Annotated[
        str | None,
        Field(description="Optional path where the formatted Excel Bid Tabulation (`Ártükör.xlsx`) will be saved."),
    ] = None,
    contractor_markup_factor: Annotated[
        float,
        Field(description="Multiplier applied to winning landed unit prices when generating `recommended_boq_pricings` (default 1.0).", gt=0.0),
    ] = 1.0,
    round_step: Annotated[
        float,
        Field(description="Rounding step for `recommended_boq_pricings` (default 10.0 for HUF; 0.01 for EUR).", ge=0.0),
    ] = 10.0,
    award_strategy: Annotated[
        Literal["BEST_SINGLE_SUPPLIER", "CHERRY_PICK_LOWEST"],
        Field(
            description=(
                "Whether `recommended_boq_pricings` should use prices from `BEST_SINGLE_SUPPLIER` "
                "(one package order) or `CHERRY_PICK_LOWEST` (lowest landed supplier per item)."
            )
        ),
    ] = "BEST_SINGLE_SUPPLIER",
) -> SupplierBidTabulationReport:
    """
    [v0.6 Tool 19] Compare multiple supplier quotes (.xlsx, .pdf, or structured lines) on an
    apples-to-apples True Landed Cost basis (distributing truckload freight, crane unloading,
    and pallet/packaging fees into per-unit landed prices), scan for commercial fine-print traps,
    generate a color-coded Excel Bid Tabulation (`Ártükör.xlsx`), and output ready-to-apply
    `BoQRowPricingInput` payloads.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _compare_supplier_quotes_and_build_bid_tab(
            target_items=target_items,
            supplier_quotes=supplier_quotes,
            package_name=package_name,
            currency=currency,
            output_xlsx_path=output_xlsx_path,
            contractor_markup_factor=contractor_markup_factor,
            round_step=round_step,
            award_strategy=award_strategy,
        )


@mcp.tool()
def calculate_cross_section_and_embankment_qto(
    segments: Annotated[
        list[RoadCrossSectionSegmentInput] | None,
        Field(
            description=(
                "List of road typical cross-section segments (`segment_id`, `length_m`, "
                "`shoulder_width_left_m`, `shoulder_width_right_m`, `shoulder_thickness_cm`, "
                "`embankment_height_left_m`, `embankment_height_right_m`, `slope_ratio_h_to_v`, "
                "`slope_topsoil_thickness_m`, `ditch_sides`, `ditch_bottom_width_m`, `ditch_depth_m`, "
                "`ditch_lining_type`, `infiltration_gravel_depth_m`)."
            )
        ),
    ] = None,
    dxf_path: Annotated[
        str | None,
        Field(description="Optional DXF/DWG path to auto-measure road alignment length from `alignment_layers`."),
    ] = None,
    alignment_layers: Annotated[
        list[str] | None,
        Field(description="CAD layers containing road centerline or edge lines when `dxf_path` is used."),
    ] = None,
    unit_scale_to_meter: Annotated[
        float,
        Field(description="CAD drawing unit scale to meters (default 1.0).", gt=0.0),
    ] = 1.0,
    shoulder_compaction_factor: Annotated[
        float,
        Field(description="Compaction multiplier for ordering granular shoulder material (default 1.15).", gt=0.0),
    ] = 1.15,
    shoulder_bulk_density_t_per_m3: Annotated[
        float,
        Field(description="Bulk density (t/m3) of crushed stone shoulder material (default 2.05 t/m3).", gt=0.0),
    ] = 2.05,
) -> CrossSectionAndEmbankmentReport:
    """
    [v0.6 Tool 20] Calculate road typical cross-section (mintakeresztszelvény) quantities:
    crushed-stone road shoulders (`m2`, compacted `m3`, order `m3`, and tonnes `t` including outer
    batter-slope wedge), inclined embankment/cut slope surface area (`m2`), slope topsoil spreading (`m3`),
    hydroseeding area (`m2`), and trapezoidal roadside drainage/infiltration ditch excavation (`m3`),
    lining/grassing (`m2`), washed gravel fill (`m3`), and geotextile wrap (`m2`).
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _calculate_cross_section_and_embankment_qto(
            segments=segments,
            dxf_path=dxf_path,
            alignment_layers=alignment_layers,
            unit_scale_to_meter=unit_scale_to_meter,
            shoulder_compaction_factor=shoulder_compaction_factor,
            shoulder_bulk_density_t_per_m3=shoulder_bulk_density_t_per_m3,
        )


@mcp.tool()
def generate_progress_certificate(
    progress_entries: Annotated[
        list[ProgressItemEntryInput] | None,
        Field(
            description=(
                "List of item progress records (`item_key`, `previous_cumulative_quantity`, "
                "`current_period_quantity` or `target_cumulative_percent`, `measurement_proof_formula`)."
            )
        ),
    ] = None,
    contract_boq_xlsx_path: Annotated[
        str | None,
        Field(description="Optional path to the contracted BoQ Excel workbook (.xlsx) to read contracted items and prices."),
    ] = None,
    sheet_names: Annotated[
        list[str] | None,
        Field(description="Optional list of visible BoQ worksheet names to include when `contract_boq_xlsx_path` is provided."),
    ] = None,
    default_completion_percent: Annotated[
        float | None,
        Field(
            description=(
                "Optional default cumulative completion percentage (0 to 100) applied to contracted items "
                "not explicitly listed in `progress_entries`."
            ),
            ge=0.0,
        ),
    ] = None,
    project_name: Annotated[
        str,
        Field(description="Project title displayed on the Payment Certificate and Measurement Sheet."),
    ] = "Mélyépítési és Útépítési Projekt",
    certificate_number: Annotated[
        str,
        Field(description="Certificate number/title (e.g., '1. Részszámla', '2. sz. Teljesítésigazolás', 'Végszámla')."),
    ] = "1. Részszámla (1. sz. Teljesítésigazolás)",
    billing_period: Annotated[
        str,
        Field(description="Billing period string (e.g., '2026. november')."),
    ] = "2026. november",
    client_name: Annotated[
        str,
        Field(description="Employer / Client company name."),
    ] = "Megrendelő Kft.",
    contractor_name: Annotated[
        str,
        Field(description="Contractor company name."),
    ] = "Térburkolat Kft.",
    currency: Annotated[
        str,
        Field(description="Currency code (e.g., 'HUF', 'EUR', 'USD')."),
    ] = "HUF",
    advance_deduction_percent: Annotated[
        float,
        Field(description="Proportional advance payment repayment percentage deducted from current period work (default 0.0%).", ge=0.0, le=100.0),
    ] = 0.0,
    retention_percent: Annotated[
        float,
        Field(description="Performance guarantee retention percentage deducted from current period work (default 5.0%).", ge=0.0, le=50.0),
    ] = 5.0,
    vat_percent: Annotated[
        float,
        Field(description="VAT percentage (default 27.0%; pass 0.0 for domestic reverse-charge / fordított adózás).", ge=0.0),
    ] = 27.0,
    output_xlsx_path: Annotated[
        str | None,
        Field(
            description=(
                "Optional path where the formatted 2-sheet Excel workbook "
                "(`1_Teljesítésigazolás` and `2_Felmérési_Napló`) will be saved."
            )
        ),
    ] = None,
) -> ProgressCertificateReport:
    """
    [v0.6 Tool 21] Generate a Construction Measurement Sheet (Felmérési Napló) and Monthly Progress
    Payment Certificate (Teljesítésigazolás / FIDIC IPC) from a contracted BoQ workbook and/or
    progress entries: computes previous, current, and cumulative quantities/amounts, flags >100%
    overruns (Pótmunka), applies advance repayment and retention deductions, and exports a 2-sheet
    Excel workbook with live formulas and FMV/ME signature blocks.
    """
    with contextlib.redirect_stdout(sys.stderr):
        return _generate_progress_certificate(
            progress_entries=progress_entries,
            contract_boq_xlsx_path=contract_boq_xlsx_path,
            sheet_names=sheet_names,
            default_completion_percent=default_completion_percent,
            project_name=project_name,
            certificate_number=certificate_number,
            billing_period=billing_period,
            client_name=client_name,
            contractor_name=contractor_name,
            currency=currency,
            advance_deduction_percent=advance_deduction_percent,
            retention_percent=retention_percent,
            vat_percent=vat_percent,
            output_xlsx_path=output_xlsx_path,
        )


# Register 5 Engineering Reference MCP Resources & 5 One-Click Workflow MCP Prompts
register_resources_and_prompts(mcp)


def main() -> None:
    """Run the civil-qto-mcp server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
