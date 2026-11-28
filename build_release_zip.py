"""build_release_zip.py - Build a clean, beginner-friendly Windows Source+Launcher ZIP for Glass Nova.

Excludes .env, .git, .venv, tests, caches, website docs/, and unsigned GlassNova.exe.
Outputs: dist/GlassNova-Windows-Source-Launcher.zip
"""
from __future__ import annotations

from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
ZIP_PATH = DIST_DIR / "GlassNova-Windows-Source-Launcher.zip"
TOP_FOLDER = "GlassNova"

EXCLUDE_PY_NAMES = {
    "build_exe.py",
    "build_release_zip.py",
}

INCLUDE_ROOT_FILES = [
    "Start_Glass_Nova.bat",
    "Launch Glass Nova.vbs",
    "INSTALL.txt",
    "README.md",
    "requirements.txt",
    ".env.example",
    "hand_landmarker.task",
]


def collect_release_files(root: Path = ROOT) -> list[tuple[Path, str]]:
    """Return list of (absolute_source_path, archive_relative_path) pairs."""
    items: list[tuple[Path, str]] = []

    # 1. Runtime Python source files (exclude tests and build scripts)
    for py_file in sorted(root.glob("*.py")):
        if py_file.name.startswith("test_") or py_file.name in EXCLUDE_PY_NAMES:
            continue
        items.append((py_file, f"{TOP_FOLDER}/{py_file.name}"))

    # 2. Required launchers, docs, config templates, and models
    for name in INCLUDE_ROOT_FILES:
        p = root / name
        if p.is_file():
            items.append((p, f"{TOP_FOLDER}/{name}"))

    # 3. Companion & UI assets directory (skip caches/hidden junk)
    assets_dir = root / "assets"
    if assets_dir.is_dir():
        for f in sorted(assets_dir.rglob("*")):
            if not f.is_file():
                continue
            parts = set(f.relative_to(assets_dir).parts)
            if "__pycache__" in parts or f.name in (".DS_Store", "Thumbs.db"):
                continue
            rel = f.relative_to(root).as_posix()
            items.append((f, f"{TOP_FOLDER}/{rel}"))

    return items


def build_zip(output_path: Path = ZIP_PATH, root: Path = ROOT) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    files = collect_release_files(root)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, arcname in files:
            zf.write(src, arcname=arcname)
    return output_path


if __name__ == "__main__":
    out = build_zip()
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"Created release ZIP: {out} ({size_mb:.2f} MB)")
