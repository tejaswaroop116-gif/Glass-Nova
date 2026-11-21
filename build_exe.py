"""build_exe.py - One-click PyInstaller packager for Glass Nova.

Usage:
    python build_exe.py

Produces a standalone Windows executable bundle in dist/GlassNova/GlassNova.exe
including companion assets, the hand landmarker model, and backend modules.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[1/2] Installing PyInstaller into the current environment...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    add_data = [
        f"{ROOT / 'assets'};assets",
        f"{ROOT / 'hand_landmarker.task'};.",
        f"{ROOT / 'pc_assistant.py'};.",
    ]
    hidden_imports = [
        "bot",
        "ui_dialogs",
        "cloudy",
        "ghost",
        "mummy",
        "pet_engine",
        "tools",
        "desktop_agent",
        "screen_agent",
        "document_agent",
        "video_agent",
        "permissions",
        "wake_word",
        "media_ducking",
        "hand_gesture",
        "nova_action_router",
        "nova_companion_bridge",
        "nova_screen_pointing",
        "nova_walkthrough",
        "nova_workflows",
    ]

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
    for d in add_data:
        cmd.extend(["--add-data", d])
    for h in hidden_imports:
        cmd.extend(["--hidden-import", h])
    cmd.append(str(ROOT / "ui.py"))

    print("[2/2] Building GlassNova standalone bundle...")
    subprocess.check_call(cmd, cwd=str(ROOT))
    print(f"\nBuild complete! Executable located at: {ROOT / 'dist' / 'GlassNova' / 'GlassNova.exe'}")


if __name__ == "__main__":
    build()
