# Changelog

All notable changes to **`civil-qto-mcp`** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.6.2] - 2026-11-24

### Added
- **LandXML 1.0/1.1/1.2 (`.xml`) 3D Surface Support in Tool 16 (`calculate_tin_earthwork_volumes`):** Direct parsing of Civil 3D / OpenRoads / ArchiCAD LandXML `<Surface>` TIN point clouds (`landxml_existing_path`, `landxml_design_path`, `existing_surface_name`, `design_surface_name`) with automatic Metric/Imperial `<Units>` scaling and `Northing Easting Elevation` coordinate handling.
- Added `test_landxml_3d_surface_earthwork_volumes` (44 total passing tests).

---

## [0.6.1] - 2026-11-19

### Added
- **5 Built-In MCP Resources (`src/civil_qto_mcp/resources_and_prompts.py`):** Direct read-only engineering standards (`en1610-trench-widths`, `pavement-densities-and-factors`, `precast-manhole-catalogs`, `technological-completeness-chains`, and `hitl-3-step-protocol`).
- **5 One-Click Engineering Workflow MCP Prompts:** `tender_full_audit`, `roadworks_and_paving_qto`, `utility_network_and_trench_qto`, `supplier_bid_tabulation`, and `monthly_progress_billing`.
- **CAD Geometry & Callout Hardening (Tools 1–3):** Civil 3D `MULTILEADER` (`MLEADER`) arrow-tip & callout text extraction, distant manhole schedule Node-ID matching (`Step 2C`), and cross-layer boundary corner-gap bridging (`<= 0.10 m`) for multi-layer `polygonize`.
- **Manufacturer Quote Synonyms & Lidl BBS Profile (Tools 13 & 19):** Brand synonym matching (`Leier Solido/Piazza`, `KK Rotterdam/London/Lisszabon`, `Frühwald Behaton/Classic`, `SzeBeton`, `Pureco`, `HYDROTEC`), automatic freight/crane/pallet fee extraction from quote files, Lidl BBS profile checks, and numbered Technical Clarification Questions (`technical_clarification_questions`).
- **PyPI Trusted Publishing CI Workflow:** Added `.github/workflows/publish-pypi.yml` for automated OIDC publishing to PyPI on GitHub Releases.

---

## [0.6.0] - 2026-11-17

### Added
- **Tool 18 (`calculate_trench_earthwork_and_bedding`):** Detailed **MSZ EN 1610 / DIN 4124** utility pipe-trench and manhole-pit earthwork engine (`src/civil_qto_mcp/cad/trench_earthwork_calculator.py`) computing depth-banded excavation (`0–1.5 m`, `1.5–3.0 m`, `>3.0 m`), machine vs. hand excavation split, lower bedding (`m3`), pipe-zone protective cover (`m3`), exact pipe & manhole volume displacement (`m3`), native backfill (`m3`), excess spoil haul-off (`m3`), two-sided shoring (`m2`), warning tape (`m`), and geotextile wrapping (`m2`).
- **Tool 19 (`compare_supplier_quotes_and_build_bid_tab`):** Multi-supplier quote reader & Bid Tabulation (`Ártükör.xlsx`) generator (`src/civil_qto_mcp/boq/supplier_bid_tabulator.py`) normalizing `.xlsx`, `.pdf`, or structured quotes onto a **True Landed Unit Price** basis (allocating truckload freight, crane unloading, and non-refundable pallet/packaging surcharges), scanning for commercial fine-print traps, and outputting ready-to-apply `BoQRowPricingInput` records.
- **Tool 20 (`calculate_cross_section_and_embankment_qto`):** Road typical cross-section (*mintakeresztszelvény*) calculator (`src/civil_qto_mcp/cad/cross_section_calculator.py`) computing crushed-stone shoulders (`m2`, compacted `m3`, order `m3`, and tonnes `t` including outer batter-slope wedge), inclined slope area (`m2`), slope topsoil (`m3`), hydroseeding (`m2`), and trapezoidal roadside drainage/infiltration ditch excavation, lining, washed gravel fill, and geotextile wrap.
- **Tool 21 (`generate_progress_certificate`):** Construction Measurement Sheet (*Felmérési Napló*) and Monthly Progress Payment Certificate (*Teljesítésigazolás* / FIDIC IPC) generator (`src/civil_qto_mcp/boq/progress_billing_generator.py`) computing previous, current, and cumulative quantities/amounts, flagging $>100\%$ quantity overruns (*Pótmunka*), applying advance repayment and retention deductions, and exporting a 2-sheet Excel workbook with live formulas.
- Added `tests/test_v06_workflows.py` (41 total passing tests).

---

## [0.5.0] - 2026-11-16

### Added
- **Tool 16 (`calculate_tin_earthwork_volumes`):** 3D Delaunay TIN surface earthwork engine (`src/civil_qto_mcp/cad/tin_earthwork_engine.py`) computing topsoil stripping (`m3`), subgrade mirror-level offset (`pavement_structure_thickness_m`), Cut (`m3`), Fill (`m3`), and Net Earth Balance directly from CAD spot-elevation layers (`POINT`, `INSERT`, `TEXT`/`MTEXT`) or 3D point clouds, with sub-triangle zero-line (`nullvonal`) integration.
- **Tool 17 (`compare_drawing_and_boq_revisions`):** Spatial CAD & Excel BoQ revision and As-Built comparator (`src/civil_qto_mcp/cad/revision_comparator.py`) reporting boolean `New - Old` added areas, `Old - New` removed areas, linear/point deltas, and row-level BoQ revision diffs with ready-to-apply variation order payloads.
- Added `tests/test_v05_workflows.py` (35 total passing tests).

---

## [0.4.0] - 2026-11-16

### Added
- **Tool 13 (`audit_technological_completeness`):** 6-chain technological completeness auditor (`ASPHALT_CHAIN`, `PAVING_BLOCK_CHAIN`, `GRAVITY_SEWER_CHAIN`, `PRESSURE_WATER_CHAIN`, `RC_FOUNDATION_CHAIN`, `INDUSTRIAL_SLAB_CHAIN`) detecting omitted companion items (missing curbs, tack coat, tie-in saw-cutting, manhole vs. cover count mismatches, shallow pipe concrete protection, slab contraction joints).
- **Tool 14 (`calculate_foundation_and_slab_qto`):** Structural foundation, plinth wall, and industrial floor slab QTO engine computing structural concrete (`m3`), blinding concrete with $+10\text{ cm}$ lateral overhang (`m3`), lateral formwork (`m2`), rebar/fiber tonnage (`t`), DIN 4124 prismoidal pit excavation (`m3`), plinth XPS insulation (`m2`), and saw-cut contraction joints (`m`).
- **Tool 15 (`match_prices_and_generate_rfq`):** Pluggable 3-tier regional unit-price matcher and 6-package supplier RFQ generator.
- Added `tests/test_v04_workflows.py` (31 total passing tests).

---

## [0.3.0] - 2026-11-16

### Added
- **Tool 9 (`convert_vector_pdf_to_dxf`):** Vector PDF engineering plan importer (`_-PDFIMPORT`) with 3-stage automatic scale calibration (`REFERENCE_DIMENSION`, `EXPLICIT_SCALE_RATIO`, `TITLE_BLOCK_SCALE`).
- **Tool 10 (`extract_longitudinal_profile_data`):** Multi-language (`EN` / `DE` / `HU`) utility longitudinal profile analyzer computing shallow-cover ($<1.00\text{ m}$) `C12/15` concrete encasement, trench shoring ($\ge 1.25\text{ m}$), and hand vs. machine excavation split at utility crossings.
- **Tool 11 (`apply_boq_modifications_and_pricing`):** Safe Excel BoQ writer supporting 1-, 2-, and 3-column layouts, currency-safe decimal rounding (`round_step=0.01`), `SUPPLEMENTAL_ITEM` / `K_TETEL` rows, `VARIATION_PAIR` / `KIV_ESZREVETEL_PAIR` (`-`/`+`) pairs, XML `<v>` cache updates, and `xl/calcChain.xml` cleanup.
- **Tool 12 (`export_qto_visual_overlay`):** Interactive standalone `.html` and `.svg` visual verification overlay generator (`locale="en" | "de" | "hu"`).
- Full internationalization (`i18n`) across `EN`, `DE`, and `HU` standards (`DIN EN 1917`, `ASTM C478`, `SzeBeton T2`, `EN 13108 / 13285 / 14227`).

---

## [0.2.0] - 2026-11-16

### Added
- **Tool 6 (`convert_dwg_to_dxf`):** Native `.dwg` support with binary header detection (`AC1015`..`AC1032`) and SHA-256 caching via AutoCAD `accoreconsole.exe` / ODA File Converter / LibreDWG.
- **Tool 7 (`optimize_precast_manhole_elements`):** Combinatorial precast manhole and road gully ring optimizer with shallow flat-slab (`AFT`) fallback and manufacturer BOM generation.
- **Tool 8 (`calculate_pavement_layer_quantities`):** Multi-layer road and paving structural calculator with lateral step-out widening, compaction multipliers, and tonnage conversion.

---

## [0.1.0] - 2026-11-16

### Added
- Initial release with 5 core tools: `inspect_civil_dxf`, `measure_linear_and_area_features`, `extract_utility_network_and_levels`, `inspect_and_audit_boq_xlsx`, and `compare_qto_with_boq`.
