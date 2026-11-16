"""
Construction & Civil Engineering Unit Synonym and Multiplier Normalizer.
Handles Hungarian (TERC/ÉNGY), German (VOB/DIN), and English (CESMM/NRM) unit notations.
"""

from __future__ import annotations

import re

# Mapping of cleaned lowercase unit string -> (canonical_unit, multiplier_to_canonical)
UNIT_SYNONYM_MAP: dict[str, tuple[str, float]] = {
    # Length -> canonical 'm'
    "m": ("m", 1.0),
    "fm": ("m", 1.0),
    "lm": ("m", 1.0),
    "mh": ("m", 1.0),
    "m.h.": ("m", 1.0),
    "rm": ("m", 1.0),
    "lin.m": ("m", 1.0),
    "linm": ("m", 1.0),
    "folyóméter": ("m", 1.0),
    "folyometer": ("m", 1.0),
    "lfm": ("m", 1.0),
    "km": ("m", 1000.0),
    "kilométer": ("m", 1000.0),
    "kilometer": ("m", 1000.0),
    "cm": ("m", 0.01),
    "mm": ("m", 0.001),
    # Area -> canonical 'm2'
    "m2": ("m2", 1.0),
    "m²": ("m2", 1.0),
    "nm": ("m2", 1.0),
    "négyzetméter": ("m2", 1.0),
    "negyzetmeter": ("m2", 1.0),
    "sqm": ("m2", 1.0),
    "sq.m": ("m2", 1.0),
    "qm": ("m2", 1.0),
    "ha": ("m2", 10_000.0),
    "hektár": ("m2", 10_000.0),
    "hektar": ("m2", 10_000.0),
    # Volume -> canonical 'm3'
    "m3": ("m3", 1.0),
    "m³": ("m3", 1.0),
    "lm3": ("m3", 1.0),
    "lm³": ("m3", 1.0),
    "bm3": ("m3", 1.0),
    "bm³": ("m3", 1.0),
    "tm3": ("m3", 1.0),
    "tm³": ("m3", 1.0),
    "köbméter": ("m3", 1.0),
    "kobmeter": ("m3", 1.0),
    "cum": ("m3", 1.0),
    "cu.m": ("m3", 1.0),
    "cbm": ("m3", 1.0),
    "l": ("m3", 0.001),
    "liter": ("m3", 0.001),
    # Count / Discrete items -> canonical 'pcs'
    "db": ("pcs", 1.0),
    "darab": ("pcs", 1.0),
    "pcs": ("pcs", 1.0),
    "pc": ("pcs", 1.0),
    "nr": ("pcs", 1.0),
    "no": ("pcs", 1.0),
    "ea": ("pcs", 1.0),
    "each": ("pcs", 1.0),
    "st": ("pcs", 1.0),
    "stk": ("pcs", 1.0),
    "stück": ("pcs", 1.0),
    "akna": ("pcs", 1.0),
    "klt": ("pcs", 1.0),
    "készlet": ("pcs", 1.0),
    "keszlet": ("pcs", 1.0),
    "set": ("pcs", 1.0),
    "pont": ("pcs", 1.0),
    "hely": ("pcs", 1.0),
    # Mass -> canonical 't'
    "t": ("t", 1.0),
    "to": ("t", 1.0),
    "ton": ("t", 1.0),
    "tonna": ("t", 1.0),
    "kg": ("t", 0.001),
    # Lump sum -> canonical 'lump_sum'
    "átalány": ("lump_sum", 1.0),
    "atalany": ("lump_sum", 1.0),
    "ls": ("lump_sum", 1.0),
    "lump sum": ("lump_sum", 1.0),
    "psch": ("lump_sum", 1.0),
    "pauschal": ("lump_sum", 1.0),
}


def normalize_engineering_unit(raw_unit: str) -> tuple[str, float]:
    """
    Normalize a raw BoQ or CAD unit string into (canonical_unit, multiplier_to_canonical).
    Example:
        'fm' -> ('m', 1.0)
        'km' -> ('m', 1000.0)
        'db' -> ('pcs', 1.0)
    """
    if not raw_unit:
        return "unknown", 1.0
    cleaned = raw_unit.strip().lower()
    cleaned = re.sub(r"[.\s]+$", "", cleaned)
    if cleaned in UNIT_SYNONYM_MAP:
        return UNIT_SYNONYM_MAP[cleaned]
    # Remove spaces and dots
    compact = re.sub(r"[\s.]+", "", cleaned)
    if compact in UNIT_SYNONYM_MAP:
        return UNIT_SYNONYM_MAP[compact]
    return cleaned, 1.0
