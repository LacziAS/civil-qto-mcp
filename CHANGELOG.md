# Changelog

All notable changes to **`civil-qto-mcp`** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
