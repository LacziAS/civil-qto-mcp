"""
v0.2 Module 1: Transparent .dwg and .dxf document loader and batch converter.

Features:
- Reads the first 6 bytes of any .dwg file to identify exact AutoCAD version (AC1015=R2000 .. AC1032=R2018)
- SHA-256 content/mtime caching in %TEMP%/civil_qto_mcp_cache so repeated tool calls on the same .dwg
  never re-run conversion
- Auto-discovers ODA File Converter (ODAFileConverter.exe), LibreDWG (dwg2dxf), or ezdxf.addons.odafc
- Provides both transparent `load_cad_document(file_path)` for all CAD tools and explicit
  `convert_dwg_to_dxf(source_path, output_dir)` for Tool 6.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import ezdxf
from ezdxf.addons import odafc
from ezdxf.document import Drawing

from civil_qto_mcp.models.dxf_models import DwgConversionItem, DwgConversionReport

DWG_VERSION_MAP: dict[str, str] = {
    "AC1012": "AutoCAD R13",
    "AC1014": "AutoCAD R14",
    "AC1015": "AutoCAD 2000/2002",
    "AC1018": "AutoCAD 2004/2006",
    "AC1021": "AutoCAD 2007/2009",
    "AC1024": "AutoCAD 2010/2012",
    "AC1027": "AutoCAD 2013/2017",
    "AC1032": "AutoCAD 2018+",
}


class DwgConversionError(RuntimeError):
    """Raised when a .dwg file cannot be converted because no external converter is available."""


def read_dwg_header_version(dwg_path: Path) -> str | None:
    """Read the 6-byte ASCII version signature from the start of a binary .dwg file."""
    try:
        with dwg_path.open("rb") as f:
            sig = f.read(6).decode("ascii", errors="ignore").strip()
        if sig.startswith("AC"):
            human = DWG_VERSION_MAP.get(sig, "Unknown AutoCAD")
            return f"{sig} ({human})"
    except OSError:
        pass
    return None


def find_dwg_converter() -> tuple[str, str] | None:
    """
    Search the system for an installed DWG -> DXF converter executable.
    Returns (backend_name, executable_path) or None.
    """
    # 1. Explicit environment variable override
    env_oda = os.environ.get("ODA_FILE_CONVERTER_PATH")
    if env_oda and Path(env_oda).is_file():
        return ("ODA_FILE_CONVERTER", str(Path(env_oda).resolve()))

    # 2. Check PATH for ODAFileConverter or dwg2dxf
    for bin_name, backend in [
        ("ODAFileConverter", "ODA_FILE_CONVERTER"),
        ("ODAFileConverter.exe", "ODA_FILE_CONVERTER"),
        ("dwg2dxf", "LIBREDWG_DWG2DXF"),
        ("dwg2dxf.exe", "LIBREDWG_DWG2DXF"),
    ]:
        which_hit = shutil.which(bin_name)
        if which_hit:
            return (backend, which_hit)

    # 3. Scan standard Windows installation directories for AutoCAD accoreconsole.exe
    autodesk_root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Autodesk"
    if autodesk_root.exists():
        for exe in sorted(autodesk_root.rglob("accoreconsole.exe"), reverse=True):
            return ("AUTOCAD_CORE_CONSOLE", str(exe.resolve()))

    # 4. Scan standard Windows installation directories for ODA File Converter
    search_roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ODA",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "ODA",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ODA",
        Path(os.environ.get("USERPROFILE", "")) / "ODA",
    ]
    for root in search_roots:
        if root.exists():
            for exe in sorted(root.rglob("ODAFileConverter.exe"), reverse=True):
                return ("ODA_FILE_CONVERTER", str(exe.resolve()))

    # 5. Check if ezdxf odafc already knows where it is
    try:
        if odafc.is_installed():
            return ("EZDXF_ODAFC", "odafc")
    except Exception:  # noqa: BLE001
        return None

    return None


def _get_cache_dxf_path(dwg_path: Path, target_version: str = "R2018") -> Path:
    """Compute a deterministic cache file path based on file path, size, and mtime_ns."""
    stat = dwg_path.stat()
    key = f"{dwg_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{target_version}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    cache_dir = Path(tempfile.gettempdir()) / "civil_qto_mcp_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{dwg_path.stem}_{digest}.dxf"


def convert_single_dwg(
    dwg_path: str | Path,
    output_dxf_path: str | Path | None = None,
    target_version: str = "R2018",
    use_cache: bool = True,
) -> DwgConversionItem:
    """
    Convert a single .dwg file (or pass through a .dxf file) to DXF format.
    """
    src = Path(dwg_path).resolve()
    if not src.exists():
        return DwgConversionItem(
            source_dwg_path=str(src),
            status="FAILED",
            error_message=f"Source file not found: {src}",
        )

    # If the input is already a .dxf file, copy or return directly
    if src.suffix.lower() == ".dxf":
        dest = Path(output_dxf_path).resolve() if output_dxf_path else src
        if dest != src:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return DwgConversionItem(
            source_dwg_path=str(src),
            output_dxf_path=str(dest),
            source_dwg_version="DXF",
            target_dxf_version=target_version,
            used_cache=False,
            converter_backend="NATIVE_DXF",
            status="SUCCESS",
        )

    dwg_ver = read_dwg_header_version(src)
    cache_file = _get_cache_dxf_path(src, target_version=target_version)
    final_dest = Path(output_dxf_path).resolve() if output_dxf_path else cache_file

    if use_cache and cache_file.exists() and cache_file.stat().st_size > 0:
        if final_dest != cache_file:
            final_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache_file, final_dest)
        return DwgConversionItem(
            source_dwg_path=str(src),
            output_dxf_path=str(final_dest),
            source_dwg_version=dwg_ver,
            target_dxf_version=target_version,
            used_cache=True,
            converter_backend="CACHE",
            status="SUCCESS",
        )

    converter_info = find_dwg_converter()
    if converter_info is None:
        return DwgConversionItem(
            source_dwg_path=str(src),
            source_dwg_version=dwg_ver,
            target_dxf_version=target_version,
            converter_backend="NONE",
            status="FAILED",
            error_message=(
                f"Cannot convert binary DWG '{src.name}' ({dwg_ver or 'DWG'}): "
                "Neither ODA File Converter (ODAFileConverter.exe) nor LibreDWG (dwg2dxf) was found. "
                "Install ODA File Converter (https://www.opendesign.com/guestfiles/oda_file_converter) "
                "or set the ODA_FILE_CONVERTER_PATH environment variable."
            ),
        )

    backend_name, exe_path = converter_info

    try:
        if backend_name == "AUTOCAD_CORE_CONSOLE":
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_dwg = cache_file.with_suffix(".tmp_in.dwg")
            tmp_scr = cache_file.with_suffix(".tmp.scr")
            shutil.copy2(src, tmp_dwg)
            scr_content = f'FILEDIA\n0\n_DXFOUT\n"{cache_file}"\n16\n_QUIT\nY\n'
            tmp_scr.write_text(scr_content, encoding="utf-8")
            try:
                subprocess.run(
                    [exe_path, "/i", str(tmp_dwg), "/s", str(tmp_scr)],
                    check=True,
                    capture_output=True,
                    timeout=180,
                )
            finally:
                tmp_dwg.unlink(missing_ok=True)
                tmp_scr.unlink(missing_ok=True)
        elif backend_name in ("ODA_FILE_CONVERTER", "EZDXF_ODAFC"):
            if exe_path != "odafc":
                ezdxf.options.set("odafc-addon", "win_exec_path", exe_path)
                ezdxf.options.set("odafc-addon", "unix_exec_path", exe_path)
            doc = odafc.readfile(str(src), version=target_version)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            doc.saveas(str(cache_file))
        elif backend_name == "LIBREDWG_DWG2DXF":
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [exe_path, "-o", str(cache_file), str(src)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

        if final_dest != cache_file:
            final_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache_file, final_dest)

        return DwgConversionItem(
            source_dwg_path=str(src),
            output_dxf_path=str(final_dest),
            source_dwg_version=dwg_ver,
            target_dxf_version=target_version,
            used_cache=False,
            converter_backend=backend_name,
            status="SUCCESS",
        )
    except Exception as exc:  # noqa: BLE001
        return DwgConversionItem(
            source_dwg_path=str(src),
            source_dwg_version=dwg_ver,
            target_dxf_version=target_version,
            converter_backend=backend_name,
            status="FAILED",
            error_message=f"DWG conversion failed via {backend_name}: {exc}",
        )


def convert_dwg_to_dxf(
    source_path: str | Path,
    output_dir: str | Path | None = None,
    target_dxf_version: str = "R2018",
    use_cache: bool = True,
) -> DwgConversionReport:
    """
    Tool 6 implementation: Convert a single .dwg file or all .dwg files in a directory to .dxf.
    """
    src = Path(source_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Path not found: {src}")

    conv_info = find_dwg_converter()
    detected_backend = conv_info[0] if conv_info else None

    dwg_files: list[Path] = []
    if src.is_dir():
        dwg_files = sorted(src.glob("*.dwg")) + sorted(src.glob("*.DWG"))
        # Deduplicate case-insensitive matches on Windows
        seen: set[str] = set()
        unique_files: list[Path] = []
        for p in dwg_files:
            key = str(p.resolve()).lower()
            if key not in seen:
                seen.add(key)
                unique_files.append(p)
        dwg_files = unique_files
    else:
        dwg_files = [src]

    out_dir_path = Path(output_dir).resolve() if output_dir else None
    results: list[DwgConversionItem] = []

    for fpath in dwg_files:
        if out_dir_path is not None:
            target_out = out_dir_path / f"{fpath.stem}.dxf"
        else:
            target_out = fpath.with_suffix(".dxf")
        item = convert_single_dwg(
            fpath,
            output_dxf_path=target_out,
            target_version=target_dxf_version,
            use_cache=use_cache,
        )
        results.append(item)

    succ = sum(1 for r in results if r.status == "SUCCESS")
    fail = sum(1 for r in results if r.status == "FAILED")

    return DwgConversionReport(
        total_files=len(results),
        succeeded_count=succ,
        failed_count=fail,
        converter_backend_detected=detected_backend,
        conversions=results,
    )


def load_cad_document(file_path: str | Path) -> tuple[Drawing, Path]:
    """
    Unified loader for all CAD tools (`inspect_civil_dxf`, `measure_linear_and_area_features`,
    `extract_utility_network_and_levels`).
    Accepts either `.dxf` or `.dwg` files transparently.
    Returns (ezdxf.Drawing, resolved_original_path).
    """
    path_obj = Path(file_path).resolve()
    if not path_obj.exists():
        raise FileNotFoundError(f"CAD file not found: {path_obj}")

    if path_obj.suffix.lower() == ".dwg":
        conv = convert_single_dwg(path_obj, output_dxf_path=None, use_cache=True)
        if conv.status != "SUCCESS" or not conv.output_dxf_path:
            raise DwgConversionError(conv.error_message or f"Failed to convert {path_obj}")
        doc = ezdxf.readfile(conv.output_dxf_path)
        return doc, path_obj

    doc = ezdxf.readfile(str(path_obj))
    return doc, path_obj
