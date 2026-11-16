# civil-qto-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-FastMCP-green.svg)](https://modelcontextprotocol.io/)

**Deterministic Model Context Protocol (MCP) Server for Civil Engineering, Roadworks, Paving & Utilities Quantity Take-Off (`.dxf`) and Bill of Quantities (`.xlsx` / `.xlsm`) Auditing.**

---

## Why `civil-qto-mcp`?

Vision LLMs and naive text-dump scripts fail on real-world civil engineering drawings and construction budgets:
- **Real CAD drawings are messy:** Inconsistent layer names, exploded `LINE`/`ARC` segments, overlapping duplicate lines (`OVERKILL` not run), unclosed polygons with $2\text{–}8\text{ cm}$ gaps, 3D "Flying Z" elevation snaps on 2D plans, mirrored OCS entities (`extrusion Z = -1`), and mismatched `$INSUNITS` headers on national grid coordinates (such as Hungarian **EOV / HD72 EPSG:23700**).
- **Real Excel BoQs contain traps:** Hidden internal calculation worksheets, hidden alternative rows, split Material + Labor columns (Hungarian **TERC / ÉNGY** or German **VOB** layouts), hardcoded numbers overwriting live formulas, and incomplete `=SUM(...)` ranges.

`civil-qto-mcp` solves this by combining **exact C++ computational geometry (`Shapely 2.0` / `GEOS` + `ezdxf`)** and **exact `Decimal` spreadsheet auditing (`openpyxl`)** with a strict **3-Step Human-in-the-Loop (HITL)** engineering workflow:

```text
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 1: DISCOVERY & CAD HYGIENE (`inspect_civil_dxf`)                     │
│  • Lists Modelspace layers, entity counts, sample TEXT/MTEXT & ATTRIBs    │
│  • Detects CRS (e.g. Hungarian EOV HD72) & recommends unit_scale_to_meter │
│  • Flags Civil 3D proxies, XREFs, Flying-Z jumps & nearly-closed polygons │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 2: HUMAN ENGINEERING CONFIRMATION                                    │
│  • AI presents discovered layers & unit scale to the Civil Engineer       │
│  • Engineer confirms layer semantics (curbs, paving, islands, utilities)  │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 3: DETERMINISTIC QTO & BOQ AUDIT (0% Math Hallucination)             │
│  • `measure_linear_and_area_features` (1D overlap dedup + 2D Difference)  │
│  • `extract_utility_network_and_levels` (ATTRIB/Leader/STRtree + DIN4124) │
│  • `inspect_and_audit_boq_xlsx` (Hidden sheet guard + Formula/SUM audit)  │
│  • `compare_qto_with_boq` (Unit synonym & multiplier cross-verification)  │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Tool Reference (v0.2 – 8 Deterministic Tools)

| Tool Name | Workflow Stage | Purpose & Engineering Guarantees |
| :--- | :---: | :--- |
| **`inspect_civil_dxf`** | Step 1 | Lists Modelspace layers (ignoring Paperspace layouts) from `.dxf` or `.dwg` files, extracts `INSERT` block names, `ATTRIB` tag names, and cleaned `TEXT`/`MTEXT` samples. Performs 3-factor CRS & unit scale estimation and flags CAD hygiene issues. |
| **`measure_linear_and_area_features`** | Step 3A | Measures linear features ($\text{m}$) with analytical circular arc (`bulge`) calculation, 2D horizontal projection ($Z \equiv 0$), and 1D `Shapely` overlap deduplication. Measures net surface areas ($\text{m}^2$) via 2D boolean `.difference()` (preventing double-deduction on perforated `HATCH`es) with configurable **VOB/C (DIN 18318)** minimum island deduction thresholds (`min_island_deduction_m2`). |
| **`extract_utility_network_and_levels`** | Step 3B | Extracts manholes/gullies (`INSERT`, `CIRCLE`, box `LWPOLYLINE`) and utility pipes. Matches Cover (`F`/`OK`/`CL`) and Invert (`Ff`/`S`/`IL`) levels in priority order: (1) `insert.attribs` (`ATTRIB`), (2) `LEADER` arrow tip tracing, (3) `STRtree` spatial proximity. Calculates **DIN 4124 / MSZ EN 1610** trench excavation, bedding/pipe-zone, pipe displacement, and backfill volumes ($\text{m}^3$). |
| **`inspect_and_audit_boq_xlsx`** | Step 3C | Safely parses `.xlsx` and `.xlsm` BoQs while **strictly excluding `hidden` / `veryHidden` worksheets and hidden rows**. Supports Single-Price and Split Material+Labor (TERC) layouts, `keyword_filter` + `limit`/`offset` pagination, and audits 100% of visible rows for `MATH_MISMATCH`, `HARDCODED_TOTAL`, `SUM_RANGE_GAP`, `SHARED_FORMULA_RISK`, and `ZERO_UNIT_PRICE`. |
| **`compare_qto_with_boq`** | Step 3D | Deterministically cross-checks CAD QTO quantities against Excel BoQ items with built-in construction unit synonym & scale conversion (`m` $\leftrightarrow$ `fm`/`lm`/`km`, `m2` $\leftrightarrow$ `nm`/`sqm`/`ha`, `m3` $\leftrightarrow$ `lm3`/`bm3`, `pcs` $\leftrightarrow$ `db`/`akna`/`klt`). |
| **`convert_dwg_to_dxf`** | v0.2 Accelerator | Batch-converts `.dwg` files (or directories of `.dwg` files) to `.dxf` with SHA-256 content caching and binary AutoCAD header detection (`AC1015`..`AC1032`) via ODA File Converter / LibreDWG. |
| **`optimize_precast_manhole_elements`** | v0.2 Accelerator | Decomposes manholes (`DN1000`/`DN1200`) and $50\times 50\text{ cm}$ road gullies into optimal precast concrete modules (`AFE` base, `ASZ` cone or shallow `AFT` flat cover slab, `AGY` 100/75/50/25 cm shaft rings, `SZB` 10/8/6 cm adjustment rings, and `D400` circular / `600x600 HYDROTEC` covers) and builds a consolidated manufacturer RFQ Bill of Materials (BOM). |
| **`calculate_pavement_layer_quantities`** | v0.2 Accelerator | Calculates multi-layer road and paving structural quantities ($\text{m}^2$, compacted $\text{m}^3$, order $\text{m}^3$, and metric tons $\text{t}$) accounting for lateral step-out widening beyond curb lines, compaction multipliers (e.g., `CKT-4` $\times 1.20$), and cutting/overlap waste. |
| **`convert_vector_pdf_to_dxf`** | v0.3 Accelerator | Imports vector PDF engineering drawings into layered DXF files via AutoCAD Core Console (`_-PDFIMPORT`) and automatically calibrates `recommended_scale_factor_to_meters` from reference dimensions, explicit scale ratios, or title-block scale texts (`M=1:250`, `1:500`). |
| **`extract_longitudinal_profile_data`** | v0.3 Accelerator | Parses utility longitudinal profile (hossz-szelvény) station bands (`Terepszint`, `Csőfenék`, `Szelvény`) to calculate trench excavation, machine vs. hand excavation split at utility crossings, two-sided shoring area ($\ge 1.25\text{ m}$), and shallow-cover ($<1.00\text{ m}$) `C12/15` concrete encasement volumes ($\text{m}^3$). |
| **`apply_boq_modifications_and_pricing`** | v0.3 Accelerator | Safely writes unit prices (with optional `markup_factor`), red-bold quantity modifications, supplemental `K-tételek`, and `-`/`+` `kiv. észrevétel` observation pairs into 1-, 2-, or 3-column Excel BoQ workbooks while updating `SUM` ranges, populating XML `<v>` cached values, and stripping `xl/calcChain.xml`. |
| **`export_qto_visual_overlay`** | v0.3 Accelerator | Exports an interactive standalone `.html` and `.svg` visual verification map highlighting all measured net surface polygons, deducted islands, linear features, and point nodes with their exact $\text{m}^2$ / $\text{m}$ quantities. |
| **`audit_technological_completeness`** | v0.4 Accelerator | Scans BoQ workbooks against 6 civil engineering technological completeness chains (asphalt, block paving, gravity sewers, pressure water lines, RC foundations, industrial slabs) to detect omitted companion items (missing curbs, tack coat, tie-in saw-cutting, manhole cover count mismatches, shallow pipe concrete encasement, slab joints) and outputs ready-to-insert `BoQSupplementalItemInput` payloads. |
| **`calculate_foundation_and_slab_qto`** | v0.4 Accelerator | Calculates the complete structural and earthwork quantity chain for pad footings, cup footings (`kehelyalap`), strip foundations, plinth walls, raft slabs, and industrial floors: structural concrete ($\text{m}^3$), blinding concrete with $+10\text{ cm}$ lateral overhang ($\text{m}^3$), lateral formwork ($\text{m}^2$), rebar/fiber tonnage ($\text{t}$), **DIN 4124** prismoidal pit excavation ($\text{m}^3$), plinth XPS insulation ($\text{m}^2$), and saw-cut contraction joints ($\text{m}$). |
| **`match_prices_and_generate_rfq`** | v0.4 Accelerator | Matches BoQ line items against 3-tier regional unit-price databases (own historical DB, supplier price lists, and built-in benchmarks), flags transport-sensitive items, generates ready-to-apply `BoQRowPricingInput` payloads, and groups materials into 6 procurement RFQ packages with regional supplier contacts. |

---

## Quick Start & Installation

### 1. Run Directly via `uvx` (Recommended – Zero Manual Setup)

Add `civil-qto-mcp` to your **Claude Desktop** (`claude_desktop_config.json`), **Claude Code**, or **Antigravity** MCP configuration:

```json
{
  "mcpServers": {
    "civil-qto": {
      "command": "uvx",
      "args": ["civil-qto-mcp"]
    }
  }
}
```

### 2. Run from Local Source Repository (Development / Local Path)

```json
{
  "mcpServers": {
    "civil-qto": {
      "command": "uv",
      "args": [
        "--directory",
        "C:/Users/User/Desktop/András/civil-qto-mcp",
        "run",
        "civil-qto-mcp"
      ]
    }
  }
}
```

---

## Development & Automated Testing

All geometric and spreadsheet edge cases are tested deterministically using programmatic synthetic DXF (`tests/factories/dxf_factory.py`) and XLSX (`tests/factories/xlsx_factory.py`) generators—requiring zero proprietary client drawings in CI.

```bash
# Install dependencies including dev tools
uv sync --extra dev

# Run Ruff linter
uv run ruff check src tests

# Run Pytest test suite
uv run pytest
```

---

## License

Released under the [MIT License](LICENSE).
