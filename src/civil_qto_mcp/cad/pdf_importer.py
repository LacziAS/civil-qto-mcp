"""
Tool 9: Vector PDF Plan Importer & Automatic Scale Calibrator (convert_vector_pdf_to_dxf).

Converts vector civil engineering PDF drawings into layered AutoCAD DXF files via
AutoCAD Core Console (`accoreconsole.exe` `_-PDFIMPORT`) and automatically calibrates
the `recommended_scale_factor_to_meters` from:
1. Reference dimension calibration (`reference_real_meters / reference_cad_length`),
2. Explicit paper scale ratio (`1:250`, `1:500`, etc.), or
3. Automatic title-block scale regex detection (`M=1:250`, `Lépték: 1:500`, `Scale 1:100`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import ezdxf

from civil_qto_mcp.cad.geometry_engine import clean_cad_text
from civil_qto_mcp.models.dxf_models import PdfConversionReport

SCALE_TEXT_REGEX = re.compile(
    r"(?:méretarány|meretarany|lépték|leptek|maßstab|massstab|scale|\bm)\s*[=:]?\s*1\s*:\s*(\d{2,5})\b",
    re.IGNORECASE,
)
FALLBACK_RATIO_REGEX = re.compile(r"\b1\s*:\s*(50|100|200|250|500|1000|2000)\b")


def _find_accoreconsole_binary() -> str | None:
    which_hit = shutil.which("accoreconsole.exe") or shutil.which("accoreconsole")
    if which_hit:
        return which_hit
    autodesk_root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Autodesk"
    if autodesk_root.exists():
        for exe in sorted(autodesk_root.rglob("accoreconsole.exe"), reverse=True):
            return str(exe.resolve())
    return None


def detect_scale_from_dxf_texts(doc: ezdxf.document.Drawing) -> tuple[str | None, int | None]:
    """
    Scan modelspace and paperspace TEXT/MTEXT entities for title-block scale ratios
    such as 'M=1:250', 'M: 1:500', 'Lépték 1:100'.
    """
    texts: list[str] = []
    for layout in [doc.modelspace(), *doc.layouts]:
        for ent in layout:
            dxftype = ent.dxftype()
            if dxftype == "TEXT":
                raw = getattr(ent.dxf, "text", "") or ""
            elif dxftype == "MTEXT":
                raw = getattr(ent, "text", "") or ""
            else:
                continue
            cleaned = clean_cad_text(raw)
            if cleaned:
                texts.append(cleaned)

    for t in texts:
        m = SCALE_TEXT_REGEX.search(t)
        if m:
            denom = int(m.group(1))
            return f"1:{denom}", denom

    for t in texts:
        m2 = FALLBACK_RATIO_REGEX.search(t)
        if m2:
            denom = int(m2.group(1))
            return f"1:{denom}", denom

    return None, None


def convert_vector_pdf_to_dxf(
    pdf_path: str | Path,
    page_number: int = 1,
    output_dxf_path: str | Path | None = None,
    paper_scale_denominator: int | None = None,
    reference_cad_length: float | None = None,
    reference_real_meters: float | None = None,
    pdf_import_base_unit: Literal["inch", "mm", "m"] = "inch",
) -> PdfConversionReport:
    """
    Import a vector PDF civil engineering plan into a layered DXF file and compute
    its exact `recommended_scale_factor_to_meters`.
    """
    src_path = Path(pdf_path).resolve()
    warnings_list: list[str] = []

    if not src_path.exists():
        return PdfConversionReport(
            source_pdf_path=str(src_path),
            page_number=page_number,
            status="FAILED",
            error_message=f"File not found: {src_path}",
        )

    base_unit_to_m = {
        "inch": 0.0254,
        "mm": 0.001,
        "m": 1.0,
    }[pdf_import_base_unit]

    # Allow passing an already-imported .dxf file directly for scale calibration & inspection
    if src_path.suffix.lower() == ".dxf":
        target_dxf = src_path
        backend_used = "EXISTING_DXF_PASSTHROUGH"
    else:
        if output_dxf_path is not None:
            target_dxf = Path(output_dxf_path).resolve()
            target_dxf.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = Path(tempfile.gettempdir()) / "civil_qto_pdf_cache"
            out_dir.mkdir(parents=True, exist_ok=True)
            target_dxf = out_dir / f"{src_path.stem}_page{page_number}.dxf"

        accore_bin = _find_accoreconsole_binary()
        if not accore_bin:
            return PdfConversionReport(
                source_pdf_path=str(src_path),
                page_number=page_number,
                status="FAILED",
                error_message=(
                    "AutoCAD Core Console (accoreconsole.exe) is required for vector PDF import "
                    "(`_-PDFIMPORT`) but was not found on this machine."
                ),
            )

        backend_used = f"AutoCAD accoreconsole ({accore_bin})"
        if target_dxf.exists():
            target_dxf.unlink()

        with tempfile.TemporaryDirectory(prefix="civil_qto_pdf_") as tmp_dir_str:
            scr_path = Path(tmp_dir_str) / "pdf_import.scr"
            scr_lines = [
                "_-PDFIMPORT",
                "_File",
                f'"{src_path}"',
                str(page_number),
                "0,0",
                "1.0",
                "0.0",
                "_SAVEAS",
                "DXF",
                "16",
                f'"{target_dxf}"',
                "_QUIT",
                "_Y",
                "",
            ]
            scr_path.write_text("\r\n".join(scr_lines), encoding="utf-8")

            proc = subprocess.run(
                [accore_bin, "/s", str(scr_path), "/l", "en-US"],
                capture_output=True,
                timeout=90,
                check=False,
            )
            if not target_dxf.exists():
                stderr_tail = proc.stderr.decode("utf-8", errors="ignore")[-400:]
                return PdfConversionReport(
                    source_pdf_path=str(src_path),
                    page_number=page_number,
                    converter_backend=backend_used,
                    status="FAILED",
                    error_message=f"accoreconsole _-PDFIMPORT did not produce DXF. {stderr_tail}".strip(),
                )

    doc = ezdxf.readfile(str(target_dxf))
    msp = doc.modelspace()
    entity_count = sum(1 for _ in msp)
    layer_count = len(doc.layers)

    detected_ratio_str, detected_denom = detect_scale_from_dxf_texts(doc)

    # Determine scale factor to meters
    if (
        reference_cad_length is not None
        and reference_cad_length > 0
        and reference_real_meters is not None
        and reference_real_meters > 0
    ):
        scale_to_m = round(reference_real_meters / reference_cad_length, 8)
        cal_method: Literal[
            "REFERENCE_DIMENSION", "TITLE_BLOCK_SCALE", "EXPLICIT_SCALE_RATIO", "DEFAULT_UNSCALED"
        ] = "REFERENCE_DIMENSION"
    elif paper_scale_denominator is not None and paper_scale_denominator > 0:
        scale_to_m = round(base_unit_to_m * paper_scale_denominator, 8)
        cal_method = "EXPLICIT_SCALE_RATIO"
        if not detected_ratio_str:
            detected_ratio_str = f"1:{paper_scale_denominator}"
    elif detected_denom is not None and detected_denom > 0:
        scale_to_m = round(base_unit_to_m * detected_denom, 8)
        cal_method = "TITLE_BLOCK_SCALE"
    else:
        scale_to_m = 1.0
        cal_method = "DEFAULT_UNSCALED"
        warnings_list.append(
            "Could not auto-detect paper scale from title block texts. Pass `paper_scale_denominator` "
            "(e.g., 250 for 1:250) or `reference_cad_length` + `reference_real_meters`."
        )

    return PdfConversionReport(
        source_pdf_path=str(src_path),
        page_number=page_number,
        output_dxf_path=str(target_dxf),
        converter_backend=backend_used,
        status="SUCCESS",
        detected_scale_ratio_text=detected_ratio_str,
        recommended_scale_factor_to_meters=scale_to_m,
        calibration_method=cal_method,
        extracted_layer_count=layer_count,
        extracted_entity_count=entity_count,
        warnings=warnings_list,
    )
