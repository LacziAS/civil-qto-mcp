# Contributing to `civil-qto-mcp`

Thank you for your interest in contributing to **`civil-qto-mcp`**!

Our mission is to provide civil engineers, quantity surveyors, and construction AI agents with **100% deterministic, zero-hallucination CAD Quantity Take-Off (QTO) and Bill of Quantities (BoQ) auditing tools**.

---

## Development Setup

We use [`uv`](https://docs.astral.sh/uv/) for fast, reproducible dependency management.

```bash
git clone https://github.com/LacziAS/civil-qto-mcp.git
cd civil-qto-mcp
uv sync --extra dev
```

## Running Tests and Linting

Before opening a Pull Request, ensure all unit tests and `ruff` checks pass:

```bash
uv run ruff check src tests
uv run pytest -v
```

## Core Engineering Principles

1. **Strict `stdio` Safety:** Never write debug output or third-party warnings to `sys.stdout`. All tools in `server.py` wrap execution in `contextlib.redirect_stdout(sys.stderr)`.
2. **Deterministic Computational Geometry:** Never approximate circular arcs (`bulge`) or polygon areas with LLM heuristics; use exact analytical formulas and `Shapely 2.0` topological operations.
3. **Spreadsheet Privacy & Integrity:** Never read `hidden` or `veryHidden` worksheets or hidden rows unless explicitly requested, and always keep XML `<v>` cached values synchronized when writing `.xlsx` files.
4. **Synthetic Fixtures Only in Tests:** Do not commit proprietary client `.dwg`, `.dxf`, or `.xlsx` files. Extend `tests/factories/dxf_factory.py` or `tests/factories/xlsx_factory.py` to reproduce edge cases programmatically.
