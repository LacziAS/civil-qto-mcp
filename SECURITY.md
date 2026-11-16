# Security & Data Privacy Policy

## Local-Only Execution Guarantee

`civil-qto-mcp` runs **100% locally** on the engineer's workstation over standard MCP `stdio` transport:
- **Zero External Telemetry:** No CAD geometry, coordinates, unit prices, or Excel BoQ data are ever transmitted to external servers by this package.
- **Hidden Worksheet Protection:** `inspect_and_audit_boq_xlsx` strictly skips `hidden` and `veryHidden` Excel worksheets and hidden rows by default so internal contractor cost-calculation sheets are never accidentally leaked into an LLM context window.

## Reporting a Vulnerability

If you discover a security or data-isolation issue (such as path traversal, XML entity expansion in `.xlsx` archives, or command injection in external CAD converter calls), please open a private security advisory on [GitHub Security Advisories](https://github.com/LacziAS/civil-qto-mcp/security/advisories) rather than a public issue.
