"""build_exe.py - One-click PyInstaller packager & portable ZIP builder for Glass Nova.

Usage:
    python build_exe.py          # Builds dist/GlassNova/GlassNova.exe + dist/GlassNova-Windows-Portable.zip
    python build_exe.py --check  # Validates all bundle paths and hidden imports without invoking PyInstaller
    python build_exe.py --no-zip # Builds dist/GlassNova/GlassNova.exe only
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

HIDDEN_IMPORTS = [
    "bot",
    "ui_dialogs",
    "ui_theme",
    "cloudy",
    "ghost",
    "pet_engine",
    "tools",
    "tools_state",
    "tools_files",
    "desktop_agent",
    "desktop_agent_browser",
    "screen_agent",
    "document_agent",
    "video_agent",
    "permissions",
    "wake_word",
    "media_ducking",
    "hand_gesture",
    "nova_audio",
    "nova_dpapi",
    "nova_tool_defs",
    "nova_action_router",
    "nova_offline_ai",
    "nova_local_knowledge",
    "nova_offline_agent",
    "nova_lifecycle",
    "nova_voice_control",
    "nova_companion_bridge",
    "nova_screen_pointing",
    "nova_walkthrough",
    "nova_workflows",
    "nova_updater",
    "pc_assistant",
    "comtypes",
    "comtypes.client",
    "win32com",
    "win32com.client",
]

EXCLUDE_MODULES = [
    "pytest",
    "tkinter",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
]


def collect_data_entries() -> list[str]:
    candidates = [
        (ROOT / "assets", "assets"),
        (ROOT / "hand_landmarker.task", "."),
        (ROOT / "pc_assistant.py", "."),
        (ROOT / ".env.example", "."),
        (ROOT / "README.md", "."),
    ]
    entries = []
    for src, dst in candidates:
        if src.exists():
            entries.append(f"{src};{dst}")
    return entries


def build_command() -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "GlassNova",
    ]
    for entry in collect_data_entries():
        cmd.extend(["--add-data", entry])
    for mod in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", mod])
    for excl in EXCLUDE_MODULES:
        cmd.extend(["--exclude-module", excl])
    cmd.append(str(ROOT / "ui.py"))
    return cmd


def package_zip(dist_dir: Path) -> Path:
    archive_base = ROOT / "dist" / "GlassNova-Windows-Portable"
    zip_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=str(dist_dir.parent), base_dir=dist_dir.name))
    return zip_path


def build(check_only: bool = False, make_zip: bool = True) -> int:
    data_entries = collect_data_entries()
    cmd = build_command()
    if check_only:
        print(f"Verified build_exe.py: {len(data_entries)} data entries, {len(HIDDEN_IMPORTS)} hidden imports.")
        return 0

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[1/3] Installing PyInstaller into the current environment...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    print("[2/3] Building GlassNova standalone bundle...")
    subprocess.check_call(cmd, cwd=str(ROOT))
    dist_dir = ROOT / "dist" / "GlassNova"
    exe_path = dist_dir / "GlassNova.exe"
    print(f"\nExecutable ready: {exe_path}")

    if make_zip and dist_dir.is_dir():
        print("[3/3] Creating portable release ZIP...")
        zip_path = package_zip(dist_dir)
        print(f"Release archive ready: {zip_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Glass Nova standalone Windows bundle.")
    parser.add_argument("--check", action="store_true", help="Validate build inputs without running PyInstaller.")
    parser.add_argument("--no-zip", action="store_true", help="Skip creating the portable ZIP archive.")
    args = parser.parse_args()
    return build(check_only=args.check, make_zip=not args.no_zip)


if __name__ == "__main__":
    raise SystemExit(main())
