"""Pydantic v2 data models for DXF CAD inspection, QTO measurements, and utility network extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BoundingBox2D(BaseModel):
    """2D planar bounding box in native CAD coordinates."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    width: float
    height: float


class BoundingBox3D(BoundingBox2D):
    """3D bounding box including Z-elevation range in native CAD coordinates."""

    min_z: float = 0.0
    max_z: float = 0.0


class CrsAndUnitsEstimate(BaseModel):
    """3-factor coordinate reference system (CRS) and drawing unit estimation."""

    dxf_insunits_code: int = Field(
        description="Raw AutoCAD $INSUNITS header integer (e.g., 0=Unitless, 4=mm, 5=cm, 6=Meters)."
    )
    dxf_insunits_name: str = Field(
        description="Human-readable name of the $INSUNITS header value."
    )
    detected_crs_type: Literal[
        "HU_EOV_HD72", "PROJECTED_METRIC", "LOCAL_CARTESIAN", "UNKNOWN"
    ] = Field(
        description="Detected coordinate system family based on modelspace coordinate bounds."
    )
    eov_axis_convention: str | None = Field(
        default=None,
        description="If Hungarian EOV (HD72 / EPSG:23700) is detected, indicates axis mapping.",
    )
    median_text_height_raw: float | None = Field(
        default=None,
        description="Median height of TEXT/MTEXT entities in raw drawing units.",
    )
    inferred_drawing_unit: Literal["m", "cm", "mm", "ft", "in", "unknown"] = Field(
        description="Estimated real-world drawing unit of 1.0 CAD unit."
    )
    recommended_unit_scale_to_meter: float = Field(
        description="Multiplier to convert 1.0 CAD drawing unit into meters (e.g., 1.0 for m, 0.01 for cm, 0.001 for mm)."
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="Confidence level of the automatic unit estimation."
    )
    reasoning: list[str] = Field(
        default_factory=list,
        description="Step-by-step engineering justification for the CRS and unit scale recommendation.",
    )


class LayerInfo(BaseModel):
    """Detailed statistical and semantic summary of a single DXF layer."""

    layer_name: str
    total_entities: int
    entity_types: dict[str, int] = Field(
        default_factory=dict,
        description="Count of entities per DXF type (LINE, LWPOLYLINE, HATCH, INSERT, TEXT, MTEXT, etc.).",
    )
    closed_polyline_count: int = 0
    open_polyline_count: int = 0
    nearly_closed_polyline_count: int = Field(
        default=0,
        description="Open polylines whose start and end vertices are within a small gap threshold.",
    )
    hatch_count: int = 0
    distinct_block_names: list[str] = Field(
        default_factory=list,
        description="Distinct INSERT block names referenced on this layer.",
    )
    block_attribute_tags: list[str] = Field(
        default_factory=list,
        description="Distinct ATTRIB tags found inside INSERT block references on this layer.",
    )
    sample_texts: list[str] = Field(
        default_factory=list,
        description="Sample cleaned strings from TEXT, MTEXT, or ATTRIB entities on this layer.",
    )
    bbox_2d: BoundingBox2D | None = None


class CadHealthWarning(BaseModel):
    """CAD hygiene and blind-spot warning detected during inspection."""

    severity: Literal["INFO", "WARNING", "CRITICAL"]
    code: str
    message: str
    affected_layers: list[str] = Field(default_factory=list)
    affected_count: int = 0
    sample_handles: list[str] = Field(default_factory=list)


class InspectCivilDxfReport(BaseModel):
    """Output schema for Tool 1: inspect_civil_dxf."""

    file_path: str
    dxf_version: str
    total_modelspace_entities: int
    total_paperspace_entities_ignored: int
    total_layers_returned: int
    modelspace_bbox: BoundingBox3D | None = None
    crs_and_units: CrsAndUnitsEstimate
    cad_health_warnings: list[CadHealthWarning] = Field(default_factory=list)
    layers: list[LayerInfo] = Field(default_factory=list)


class ElevationAnomaly(BaseModel):
    """Warning when a 2D plan entity has unexpected 3D Z-elevation jumps ('Flying Z')."""

    handle: str
    layer: str
    entity_type: str
    min_z_m: float
    max_z_m: float
    delta_z_m: float


class AutoClosedPolygonInfo(BaseModel):
    """Audit record for an open polyline that was automatically closed within gap tolerance."""

    handle: str
    layer: str
    gap_distance_m: float
    start_point_xy: tuple[float, float]
    end_point_xy: tuple[float, float]
    resulting_area_m2: float


class LayerLinearBreakdown(BaseModel):
    """Linear quantity take-off breakdown for a single CAD layer."""

    layer_name: str
    entity_count: int
    straight_gross_length_m: float
    curved_gross_length_m: float
    gross_length_m: float
    deducted_overlap_m: float
    net_length_m: float


class LinearMeasurementSummary(BaseModel):
    """Aggregated linear measurement results across all target linear layers."""

    measured_layers: list[str]
    unit_scale_to_meter: float
    snap_tolerance_m: float
    total_entities_processed: int
    straight_gross_length_m: float
    curved_gross_length_m: float
    gross_length_m: float
    deducted_overlap_m: float
    net_length_m: float
    by_layer: list[LayerLinearBreakdown] = Field(default_factory=list)


class AreaMeasurementSummary(BaseModel):
    """Topological area measurement results with boolean island subtraction."""

    boundary_layers: list[str]
    island_hole_layers: list[str] = Field(default_factory=list)
    min_island_deduction_m2: float = 0.0
    auto_close_gap_tolerance_m: float = 0.10
    outer_polygon_count: int = 0
    island_polygon_count_deducted: int = 0
    island_polygon_count_skipped_below_threshold: int = 0
    gross_area_m2: float = 0.0
    deducted_islands_area_m2: float = 0.0
    skipped_small_islands_area_m2: float = 0.0
    net_area_m2: float = 0.0
    outer_perimeter_m: float = 0.0
    deducted_islands_perimeter_m: float = 0.0
    auto_closed_polygons: list[AutoClosedPolygonInfo] = Field(default_factory=list)


class MeasureFeaturesReport(BaseModel):
    """Output schema for Tool 2: measure_linear_and_area_features."""

    file_path: str
    unit_scale_to_meter: float
    linear_summary: LinearMeasurementSummary | None = None
    area_summary: AreaMeasurementSummary | None = None
    elevation_anomalies: list[ElevationAnomaly] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class UtilityNodeInfo(BaseModel):
    """Manhole, gully, or utility chamber extracted from DXF with spatial/attribute level data."""

    node_id: str
    handle: str
    layer: str
    source_type: Literal["INSERT", "CIRCLE", "POLYLINE_BOX"]
    block_name: str | None = None
    x_m: float
    y_m: float
    cover_level_m: float | None = None
    invert_level_m: float | None = None
    depth_h1_m: float | None = None
    net_shaft_height_h2_m: float | None = None
    nominal_diameter_mm: int | None = None
    matched_via: Literal["BLOCK_ATTRIB", "LEADER_ARROW", "SPATIAL_PROXIMITY", "NONE"] = Field(
        default="NONE"
    )
    raw_attributes: dict[str, str] = Field(default_factory=dict)
    matched_texts: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)


class PipeSegmentInfo(BaseModel):
    """Utility pipe segment with length, slope, and optional DIN 4124 / EN 1610 trench volumes."""

    segment_id: str
    handle: str
    layer: str
    start_node_id: str | None = None
    end_node_id: str | None = None
    length_2d_m: float
    nominal_diameter_dn_mm: int | None = None
    material: str | None = None
    slope_percent: float | None = None
    avg_trench_depth_m: float | None = None
    trench_bottom_width_m: float | None = None
    excavation_volume_m3: float | None = None
    bedding_and_pipe_zone_volume_m3: float | None = None
    pipe_displacement_volume_m3: float | None = None
    main_backfill_volume_m3: float | None = None
    matched_texts: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)


class TrenchEarthworkSummary(BaseModel):
    """Aggregated trench earthwork quantities according to DIN 4124 and EN 1610."""

    standard_reference: str = "DIN 4124 / MSZ EN 1610"
    trench_wall_angle_deg: float
    bedding_thickness_m: float
    side_cover_above_crown_m: float
    total_pipe_length_m: float = 0.0
    total_excavation_volume_m3: float = 0.0
    total_bedding_and_zone_sand_gravel_m3: float = 0.0
    total_pipe_displacement_m3: float = 0.0
    total_main_backfill_m3: float = 0.0


class UtilityNetworkReport(BaseModel):
    """Output schema for Tool 3: extract_utility_network_and_levels."""

    file_path: str
    unit_scale_to_meter: float
    node_count: int
    nodes_with_complete_levels: int
    diameter_distribution: dict[str, int] = Field(default_factory=dict)
    total_network_length_m: float
    length_by_dn_and_material: dict[str, float] = Field(default_factory=dict)
    nodes: list[UtilityNodeInfo] = Field(default_factory=list)
    pipe_segments: list[PipeSegmentInfo] = Field(default_factory=list)
    earthwork_summary: TrenchEarthworkSummary | None = None
    anomalies: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# v0.2 MODELS: DWG Conversion, Precast Manhole Optimizer & Pavement Calculator
# -----------------------------------------------------------------------------


class DwgConversionItem(BaseModel):
    """Result record for a single .dwg -> .dxf conversion."""

    source_dwg_path: str
    output_dxf_path: str | None = None
    source_dwg_version: str | None = None
    target_dxf_version: str = "R2018"
    used_cache: bool = False
    converter_backend: str = "NONE"
    status: Literal["SUCCESS", "FAILED"]
    error_message: str | None = None


class DwgConversionReport(BaseModel):
    """Output schema for Tool 6: convert_dwg_to_dxf."""

    total_files: int
    succeeded_count: int
    failed_count: int
    converter_backend_detected: str | None = None
    conversions: list[DwgConversionItem] = Field(default_factory=list)


class ManholeSpecInput(BaseModel):
    """Input specification for a single manhole or road gully to decompose into precast elements."""

    node_id: str = Field(description="Identifier of the manhole or gully (e.g., 'CS-01', 'VA-02').")
    cover_level_m: float | None = Field(
        default=None, description="Rim/cover elevation in meters (Z_cover)."
    )
    invert_level_m: float | None = Field(
        default=None, description="Invert/bottom elevation in meters (Z_invert)."
    )
    total_depth_h1_m: float | None = Field(
        default=None,
        description="Total depth h1 in meters (if cover_level_m and invert_level_m are not given).",
    )
    nominal_diameter_mm: int = Field(
        default=1000,
        description="Nominal shaft diameter in mm (1000 or 1200 for manholes, 500 for 50x50cm gullies).",
    )
    node_type: Literal["MANHOLE", "GULLY_50X50"] = Field(
        default="MANHOLE",
        description="Structure type: 'MANHOLE' (circular inspection chamber) or 'GULLY_50X50' (road gully).",
    )
    surface_type: Literal["ASPHALT_OR_ROAD", "PAVING_BLOCK", "GREEN_OR_UNPAVED"] = Field(
        default="ASPHALT_OR_ROAD",
        description="Surface finish around cover (PAVING_BLOCK triggers 600x600mm square-frame HYDROTEC cover).",
    )


class PrecastElementLine(BaseModel):
    """A single precast concrete or cast-iron component inside a manhole assembly."""

    element_code: str
    category: Literal[
        "BASE", "CONE_OR_SLAB", "SHAFT_RING", "ADJUSTMENT_RING", "COVER_OR_GRATE"
    ]
    description: str
    height_cm: float
    quantity: int = 1


class ManholeAssemblyBreakdown(BaseModel):
    """Complete vertical assembly breakdown for a single manhole or gully."""

    node_id: str
    node_type: str
    nominal_diameter_mm: int
    surface_type: str
    target_total_depth_h1_cm: float
    cover_deduction_cm: float
    target_net_precast_height_h2_cm: float
    built_precast_height_cm: float
    built_total_height_cm: float
    height_residual_cm: float = Field(
        description="Difference between built total height and target depth h1 in cm."
    )
    used_flat_cover_slab: bool = False
    elements: list[PrecastElementLine] = Field(default_factory=list)
    engineering_notes: list[str] = Field(default_factory=list)


class PrecastBomSummaryItem(BaseModel):
    """Aggregated Bill of Materials (BOM) line item for precast manufacturer RFQ ordering."""

    element_code: str
    category: str
    description: str
    unit: str = "pcs"
    net_quantity: int
    spare_quantity: int = 0
    total_order_quantity: int


class ManholeOptimizationReport(BaseModel):
    """Output schema for Tool 7: optimize_precast_manhole_elements."""

    catalog_profile: str
    total_structures_processed: int
    manholes_count: int
    gullies_count: int
    shallow_flat_slab_count: int
    assemblies: list[ManholeAssemblyBreakdown] = Field(default_factory=list)
    consolidated_bom: list[PrecastBomSummaryItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PavementLayerInput(BaseModel):
    """Input definition of a single structural layer in a road or paving cross-section."""

    layer_order: int = Field(description="Order from top (1 = wearing course) to bottom.")
    layer_name: str = Field(description="Material and technical specification of the layer.")
    layer_category: Literal[
        "PAVING_BLOCK",
        "BEDDING_SAND_STONE",
        "ASPHALT",
        "CONCRETE_OR_CKT",
        "GRANULAR_SUBBASE",
        "GEOSYNTHETIC",
    ]
    thickness_cm: float = Field(
        default=0.0, ge=0.0, description="Compacted design thickness in centimeters (0 for geotextile)."
    )
    lateral_step_out_m: float = Field(
        default=0.0,
        ge=0.0,
        description="Cumulative lateral overhang / widening beyond top curb/edge line in meters.",
    )
    compaction_factor: float = Field(
        default=1.0,
        gt=0.0,
        description="Compaction/consolidation multiplier (e.g., 1.20 for CKT-4 base concrete).",
    )
    waste_or_overlap_factor: float = Field(
        default=1.03,
        gt=0.0,
        description="Cutting waste or overlap multiplier (e.g., 1.03 for paving blocks, 1.12 for geotextile).",
    )
    bulk_density_t_per_m3: float | None = Field(
        default=None,
        description="Compacted bulk density in t/m3 (e.g., 2.40 for asphalt, 2.05 for FZKA 0/63, 1.65 for 2/5 bedding).",
    )


class PavementLayerQuantityResult(BaseModel):
    """Calculated area, volume, and tonnage quantities for a single pavement layer."""

    layer_order: int
    layer_name: str
    layer_category: str
    thickness_cm: float
    lateral_step_out_m: float
    net_plan_area_m2: float
    step_out_extra_area_m2: float
    effective_layer_area_m2: float
    geometric_compacted_volume_m3: float
    compaction_factor: float
    waste_or_overlap_factor: float
    order_area_m2: float
    order_volume_m3: float
    order_tonnage_t: float | None = None
    primary_order_unit: Literal["m2", "m3", "t"]
    primary_order_quantity: float
    engineering_formula_note: str


class PavementCalculationReport(BaseModel):
    """Output schema for Tool 8: calculate_pavement_layer_quantities."""

    preset_used: str | None = None
    input_net_surface_area_m2: float
    input_active_edge_perimeter_m: float
    total_structure_thickness_cm: float
    total_subgrade_excavation_volume_m3: float
    layers: list[PavementLayerQuantityResult] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# v0.3 MODELS: Vector PDF Converter, Longitudinal Profile Parser & Visual Overlay
# -----------------------------------------------------------------------------


class PdfConversionReport(BaseModel):
    """Output schema for Tool 9: convert_vector_pdf_to_dxf."""

    source_pdf_path: str
    page_number: int = 1
    output_dxf_path: str | None = None
    converter_backend: str = "NONE"
    status: Literal["SUCCESS", "FAILED"]
    detected_scale_ratio_text: str | None = Field(
        default=None,
        description="Scale ratio text found in title block or drawing annotations (e.g., '1:250', '1:500').",
    )
    recommended_scale_factor_to_meters: float = Field(
        default=1.0,
        description="Multiplier to pass as `custom_scale_to_meter` to `measure_linear_and_area_features`.",
    )
    calibration_method: Literal[
        "REFERENCE_DIMENSION", "TITLE_BLOCK_SCALE", "EXPLICIT_SCALE_RATIO", "DEFAULT_UNSCALED"
    ] = "DEFAULT_UNSCALED"
    extracted_layer_count: int = 0
    extracted_entity_count: int = 0
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ProfileStationInput(BaseModel):
    """Input or extracted station point along a utility longitudinal profile (hossz-szelvény)."""

    station_m: float = Field(description="Chainage / station distance in meters (e.g., 0.0, 25.4, 43.47).")
    station_label: str = Field(default="", description="Formatted chainage string (e.g., '0+025.40').")
    node_id: str | None = Field(default=None, description="Manhole or chamber ID at this station.")
    ground_level_m: float = Field(description="Existing/designed ground or road elevation [mBf].")
    invert_level_m: float = Field(description="Pipe invert elevation (csőfenékszint / folyásfenék) [mBf].")


class ProfileCrossingInput(BaseModel):
    """Utility crossing along a longitudinal profile requiring hand excavation protection zone."""

    station_m: float | None = Field(default=None, description="Station distance of the crossing in meters.")
    crossed_utility_name: str = Field(
        default="Keresztező közmű",
        description="Name/type of crossed utility (e.g., 'DN150 tűzivíz', '0,4 kV kábel').",
    )
    protection_half_width_m: float = Field(
        default=1.0,
        gt=0.0,
        description="Hand-excavation protection zone on each side of the crossing in meters (default 1.0m -> 2.0m total).",
    )


class ProfileStationComputed(BaseModel):
    """Computed vertical metrics at a profile station point."""

    station_m: float
    station_label: str
    node_id: str | None = None
    ground_level_m: float
    invert_level_m: float
    depth_to_invert_h1_m: float
    crown_cover_depth_m: float


class ProfileSpanAnalysis(BaseModel):
    """Earthwork, shoring, and shallow-cover protection quantities for a single pipe span."""

    span_index: int
    start_station_m: float
    end_station_m: float
    start_node_id: str | None = None
    end_node_id: str | None = None
    length_m: float
    dn_mm: int
    outer_diameter_m: float
    slope_permille: float
    avg_depth_to_invert_m: float
    min_crown_cover_m: float
    trench_width_m: float
    total_excavation_m3: float
    bedding_and_pipe_zone_m3: float
    pipe_displacement_m3: float
    backfill_m3: float
    shoring_area_m2: float
    requires_shallow_concrete_protection: bool = False
    shallow_concrete_encase_m3: float = 0.0


class LongitudinalProfileReport(BaseModel):
    """Output schema for Tool 10: extract_longitudinal_profile_data."""

    profile_name: str
    nominal_diameter_dn_mm: int
    outer_diameter_m: float
    trench_width_m: float
    total_profile_length_m: float
    min_cover_threshold_m: float
    shoring_depth_threshold_m: float
    stations: list[ProfileStationComputed] = Field(default_factory=list)
    spans: list[ProfileSpanAnalysis] = Field(default_factory=list)
    crossings_count: int = 0
    total_excavation_m3: float
    machine_excavation_m3: float
    hand_excavation_m3: float
    total_bedding_and_zone_m3: float
    total_pipe_displacement_m3: float
    total_backfill_m3: float
    total_excess_spoil_m3: float
    total_shoring_area_m2: float
    shallow_protection_length_m: float
    shallow_concrete_encase_m3: float
    warnings: list[str] = Field(default_factory=list)


class VisualOverlayReport(BaseModel):
    """Output schema for Tool 12: export_qto_visual_overlay."""

    source_dxf_path: str
    output_html_path: str
    output_svg_path: str
    unit_scale_to_meter: float
    rendered_polygons_count: int
    rendered_lines_count: int
    rendered_points_count: int
    total_net_area_m2: float
    total_linear_length_m: float
    warnings: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# v0.4 MODELS: Foundation, Plinth & Industrial Floor Slab QTO Calculator
# -----------------------------------------------------------------------------


class FoundationElementInput(BaseModel):
    """Input definition of a structural foundation, plinth wall, or industrial floor element."""

    element_id: str = Field(description="Identifier of the foundation/slab element (e.g., 'PA-1', 'SA-1', 'IP-1').")
    element_type: Literal[
        "PAD_FOOTING",
        "CUP_FOOTING",
        "STRIP_FOUNDATION",
        "PLINTH_WALL",
        "RAFT_SLAB",
        "INDUSTRIAL_FLOOR",
    ] = Field(description="Structural foundation or slab type.")
    count: int = Field(default=1, ge=1, description="Number of identical elements.")
    length_m: float = Field(default=0.0, ge=0.0, description="Length (or perimeter length for strip/plinth) in meters.")
    width_m: float = Field(default=0.0, ge=0.0, description="Width (or thickness for plinth wall) in meters.")
    height_m: float = Field(gt=0.0, description="Structural height / slab thickness in meters.")
    plan_area_m2: float | None = Field(
        default=None,
        description="Optional explicit footprint area in m2 (if irregular polygon rather than length*width).",
    )
    plan_perimeter_m: float | None = Field(
        default=None,
        description="Optional explicit footprint perimeter in m (if irregular polygon).",
    )
    cup_void_volume_m3: float = Field(
        default=0.0,
        ge=0.0,
        description="Socket/cup void volume (m3) deducted per element for CUP_FOOTING (kehelyalap).",
    )
    rebar_kg_per_m3: float | None = Field(
        default=None,
        description="Specific reinforcement ratio in kg/m3 (defaults by element_type: e.g., 95 for pad, 115 for raft, 30 for fiber floor).",
    )
    include_blinding: bool = Field(
        default=True, description="Whether to compute lean/blinding concrete underneath (with overhang)."
    )
    include_excavation: bool = Field(
        default=True, description="Whether to compute pit/trench excavation with working space and batter slope."
    )
    xps_insulation_sides: int = Field(
        default=0,
        ge=0,
        le=2,
        description="For PLINTH_WALL or RAFT_SLAB: number of vertical sides (0, 1, or 2) receiving XPS perimeter insulation.",
    )
    joint_grid_spacing_m: float = Field(
        default=6.0,
        gt=0.0,
        description="For INDUSTRIAL_FLOOR: contraction joint saw-cutting grid spacing in meters (default 6.0m x 6.0m).",
    )


class FoundationElementBreakdown(BaseModel):
    """Calculated structural, earthwork, formwork, rebar, and insulation quantities for an element group."""

    element_id: str
    element_type: str
    count: int
    footprint_area_m2: float
    structural_concrete_m3: float
    blinding_area_m2: float
    blinding_concrete_m3: float
    formwork_area_m2: float
    rebar_or_fiber_tonnes: float
    excavation_volume_m3: float
    backfill_volume_m3: float
    displaced_spoil_m3: float
    xps_insulation_area_m2: float
    saw_cut_joints_m: float
    perimeter_isolation_strip_m: float


class FoundationAndSlabQtoReport(BaseModel):
    """Output schema for Tool 14: calculate_foundation_and_slab_qto."""

    elements_processed_count: int
    blinding_thickness_m: float
    blinding_overhang_m: float
    working_space_m: float
    batter_angle_deg: float
    total_structural_concrete_m3: float
    total_blinding_concrete_m3: float
    total_formwork_area_m2: float
    total_rebar_and_fiber_tonnes: float
    total_excavation_m3: float
    total_backfill_m3: float
    total_displaced_spoil_m3: float
    total_xps_insulation_m2: float
    total_saw_cut_joints_m: float
    total_perimeter_strip_m: float
    breakdowns: list[FoundationElementBreakdown] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# v0.5 MODELS: 3D TIN Surface Earthwork (Cut/Fill/Topsoil) Engine
# -----------------------------------------------------------------------------


class SpotElevationPoint(BaseModel):
    """A 3D survey or design spot elevation point (X, Y, Z in meters)."""

    x_m: float = Field(description="X / Easting coordinate in meters.")
    y_m: float = Field(description="Y / Northing coordinate in meters.")
    z_m: float = Field(description="Z elevation [mBf / m a.s.l.] in meters.")
    point_id: str | None = Field(default=None, description="Optional point label or ID.")


class TinEarthworkReport(BaseModel):
    """Output schema for Tool 16: calculate_tin_earthwork_volumes."""

    existing_points_count: int
    design_points_count: int
    tin_triangles_count: int
    footprint_area_m2: float
    topsoil_strip_thickness_m: float
    topsoil_strip_volume_m3: float
    pavement_structure_deduction_m: float
    cut_area_m2: float
    fill_area_m2: float
    cut_volume_bank_m3: float = Field(
        description="Net excavation (bevágás) volume below stripped ground to subgrade level in m3."
    )
    fill_volume_compacted_m3: float = Field(
        description="Net embankment/fill (töltés) volume above stripped ground to subgrade level in m3."
    )
    compaction_factor: float = 1.15
    fill_volume_required_bank_m3: float = Field(
        description="Required borrow/bank fill volume including compaction factor (fill_compacted * compaction_factor)."
    )
    net_earth_balance_m3: float = Field(
        description="fill_volume_required_bank_m3 - cut_volume_bank_m3 (negative = excess spoil to haul away; positive = borrow needed)."
    )
    min_existing_z_m: float
    max_existing_z_m: float
    avg_existing_z_m: float
    min_subgrade_z_m: float
    max_subgrade_z_m: float
    avg_subgrade_z_m: float
    warnings: list[str] = Field(default_factory=list)




