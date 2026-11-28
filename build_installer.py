"""build_installer.py - Reproducible Windows Installer builder for Glass Nova (PyInstaller + Inno Setup 6).

Usage:
    python build_installer.py --check     # Validate version, PyInstaller config, .iss script & ISCC.exe
    python build_installer.py             # Build dist/GlassNova/GlassNova.exe + dist/installer/GlassNova-Setup-<ver>.exe
    python build_installer.py --skip-exe  # Compile Inno Setup installer from an existing dist/GlassNova/ folder
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional

from nova_updater import APP_VERSION
import build_exe

ROOT = Path(__file__).resolve().parent
ISS_PATH = ROOT / "installer" / "GlassNovaSetup.iss"
DIST_APP_DIR = ROOT / "dist" / "GlassNova"
INSTALLER_OUT_DIR = ROOT / "dist" / "installer"


def find_iscc() -> Optional[Path]:
    """Locate Inno Setup Compiler (ISCC.exe) on PATH or standard Windows install locations."""
    on_path = shutil.which("ISCC") or shutil.which("ISCC.exe")
    if on_path:
        return Path(on_path)

    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def expected_installer_path(version: str = APP_VERSION) -> Path:
    return INSTALLER_OUT_DIR / f"GlassNova-Setup-{version}.exe"


def build_iscc_command(iscc_path: Path, version: str = APP_VERSION) -> list[str]:
    return [
        str(iscc_path),
        f"/DMyAppVersion={version}",
        str(ISS_PATH),
    ]


def validate_packaging_setup() -> dict:
    """Validate installer script, build_exe configuration, and Inno Setup availability."""
    iss_exists = ISS_PATH.is_file()
    iss_text = ISS_PATH.read_text(encoding="utf-8") if iss_exists else ""
    iscc = find_iscc()
    data_entries = build_exe.collect_data_entries()
    return {
        "ok": iss_exists and len(data_entries) > 0 and "nova_updater" in build_exe.HIDDEN_IMPORTS,
        "version": APP_VERSION,
        "iss_path": str(ISS_PATH),
        "iss_exists": iss_exists,
        "preserves_user_data": "usPostUninstall" in iss_text and "MB_DEFBUTTON2" in iss_text,
        "excludes_env": ".env" in iss_text and "Excludes:" in iss_text,
        "iscc_found": iscc is not None,
        "iscc_path": str(iscc) if iscc else "",
        "expected_output": str(expected_installer_path(APP_VERSION)),
        "hidden_imports_count": len(build_exe.HIDDEN_IMPORTS),
        "data_entries_count": len(data_entries),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build Glass Nova Windows Installer (Inno Setup 6).")
    parser.add_argument("--check", action="store_true", help="Validate packaging configuration without building.")
    parser.add_argument("--skip-exe", action="store_true", help="Skip PyInstaller step and reuse dist/GlassNova/.")
    args = parser.parse_args(argv)

    info = validate_packaging_setup()
    if args.check:
        print(f"Glass Nova Installer Check — Version: v{info['version']}")
        print(f"  Inno Setup Script : {info['iss_path']} ({'OK' if info['iss_exists'] else 'MISSING'})")
        print(f"  PyInstaller Specs : {info['hidden_imports_count']} hidden imports, {info['data_entries_count']} data entries")
        print(f"  User Data Guard   : {'OK' if info['preserves_user_data'] else 'MISSING'}")
        print(f"  Secret .env Guard : {'OK' if info['excludes_env'] else 'MISSING'}")
        print(f"  ISCC.exe Compiler : {info['iscc_path'] or 'Not installed (install Inno Setup 6 to compile .exe)'}")
        print(f"  Expected Output   : {info['expected_output']}")
        return 0 if info["ok"] else 1

    if not info["iss_exists"]:
        print(f"ERROR: Missing Inno Setup script at {ISS_PATH}", file=sys.stderr)
        return 1

    if not args.skip_exe:
        print(f"[1/2] Building standalone GlassNova.exe (v{APP_VERSION}) via PyInstaller...")
        rc = build_exe.build(check_only=False, make_zip=False)
        if rc != 0:
            return rc

    if not (DIST_APP_DIR / "GlassNova.exe").is_file():
        print(f"ERROR: Expected {DIST_APP_DIR / 'GlassNova.exe'} before running Inno Setup.", file=sys.stderr)
        return 1

    iscc = find_iscc()
    if iscc is None:
        print(
            "ERROR: Inno Setup 6 (ISCC.exe) was not found.\n"
            "Install Inno Setup 6 from https://jrsoftware.org/isdl.php and re-run `python build_installer.py --skip-exe`.",
            file=sys.stderr,
        )
        return 1

    INSTALLER_OUT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = build_iscc_command(iscc, APP_VERSION)
    print(f"[2/2] Compiling Windows Installer via Inno Setup 6 ({iscc})...")
    subprocess.check_call(cmd, cwd=str(ROOT))
    out_file = expected_installer_path(APP_VERSION)
    print(f"\nWindows Installer ready: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
