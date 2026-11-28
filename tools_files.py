"""tools_files.py - File, folder, calculator, and Windows-native clipboard tools for Glass Nova."""
import ast
import ctypes
import operator
import os
import re
import time
import sys
from pathlib import Path

from tools_state import WORKSPACE, _check_perm, _err, _ok


_SAFE_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_SAFE_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_arith_node(node):
    if isinstance(node, ast.Expression):
        return _eval_arith_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_UNARY_OPS:
        return _SAFE_UNARY_OPS[type(node.op)](_eval_arith_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BIN_OPS:
        left = _eval_arith_node(node.left)
        right = _eval_arith_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("Exponent too large.")
        return _SAFE_BIN_OPS[type(node.op)](left, right)
    raise ValueError("Unsupported expression.")


def calculator_operation(expression: str):
    expr = expression.lower().strip()
    replacements = {
        "multiplied by": "*", "multiply by": "*", "times": "*",
        "divided by": "/", "divide by": "/",
        "plus": "+", "add": "+", "minus": "-", "subtract": "-",
        "to the power of": "**", "power of": "**",
    }
    for k, v in sorted(replacements.items(), key=lambda x: -len(x[0])):
        expr = expr.replace(k, v)
    expr = re.sub(r"\bx\b", "*", expr)
    expr = re.sub(r"[^0-9+\-*/().% ]", "", expr)
    if not expr.strip():
        return _err("I couldn't find a valid arithmetic expression.")
    try:
        tree = ast.parse(expr, mode="eval")
        value = _eval_arith_node(tree)
        return _ok(f"The answer is {value}.", result=value)
    except Exception:
        return _err("I couldn't calculate that expression.")


def _resolve_path(path: str):
    r"""Resolve Windows special folders, including paths such as Desktop\TestFolder."""
    raw = str(path or "").strip().strip('"')
    raw = raw.replace("/", "\\")
    shortcuts = {
        "downloads": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "desktop": Path.home() / "Desktop",
        "pictures": Path.home() / "Pictures",
        "music": Path.home() / "Music",
        "videos": Path.home() / "Videos",
        "home": Path.home(),
        "workspace": WORKSPACE,
    }
    key = raw.lower().rstrip("\\")
    if key in shortcuts:
        return shortcuts[key]

    parts = [part for part in raw.split("\\") if part]
    if parts and parts[0].lower() in shortcuts:
        base = shortcuts[parts[0].lower()]
        return base.joinpath(*parts[1:])

    return Path(os.path.expandvars(os.path.expanduser(raw)))


def _is_protected_path(p: Path) -> str:
    """Return a non-empty reason string if 'p' (resolved through symlinks and '..') is a protected OS/root/app path."""
    try:
        resolved = p.resolve()
    except Exception:
        return "Invalid or unresolvable path."
    # Block drive roots like C:\
    if resolved.parent == resolved:
        return f"Refusing operation on drive root '{resolved}'."
    low = str(resolved).lower().rstrip("\\/")
    home_low = str(Path.home().resolve()).lower().rstrip("\\/")
    appdata_low = str(Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")).resolve()).lower().rstrip("\\/")
    localapp_low = str(Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")).resolve()).lower().rstrip("\\/")
    app_root_low = str(Path(__file__).resolve().parent).lower().rstrip("\\/")
    protected_roots = {home_low, "c:\\users", appdata_low, localapp_low, app_root_low}
    if getattr(sys, "frozen", False):
        exe_dir_low = str(Path(sys.executable).resolve().parent).lower().rstrip("\\/")
        meipass_low = str(Path(getattr(sys, "_MEIPASS", exe_dir_low)).resolve()).lower().rstrip("\\/")
        protected_roots.add(exe_dir_low)
        protected_roots.add(meipass_low)
        if low == exe_dir_low or low.startswith(exe_dir_low + "\\"):
            return f"Refusing operation inside Glass Nova installation directory '{resolved}'."

    if low in protected_roots:
        return f"Refusing operation on protected root directory '{resolved}'."

    protected_prefixes = (
        "c:\\windows",
        "c:\\program files",
        "c:\\program files (x86)",
        "c:\\programdata",
        "c:\\$recycle.bin",
        "c:\\system volume information",
    )
    for pref in protected_prefixes:
        if low == pref or low.startswith(pref + "\\"):
            return f"Refusing operation inside protected Windows system directory '{resolved}'."
    return ""


def create_folder(path: str):
    if not _check_perm("file_modify", f"Create folder '{path}'"):
        return _err("File modification permission was denied.")
    try:
        p = _resolve_path(path)
        prot = _is_protected_path(p)
        if prot:
            return _err(prot)
        p.mkdir(parents=True, exist_ok=True)
        return _ok(f"Created folder {p}.", path=str(p))
    except Exception as e:
        return _err(f"Could not create folder: {e}")


def list_folder(path: str = "downloads"):
    if not _check_perm("document_access", f"List folder contents of '{path}'"):
        return _err("Document access permission was denied.", permission_denied=True)
    try:
        p = _resolve_path(path)
        if not p.is_dir():
            return _err(f"That folder does not exist: {p}")
        items = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            kind = "folder" if child.is_dir() else "file"
            items.append({"name": child.name, "type": kind})
        if not items:
            return _ok(f"{p} is empty.", items=[])
        preview = "; ".join(f"{i['name']} ({i['type']})" for i in items[:100])
        suffix = "" if len(items) <= 100 else f"; and {len(items)-100} more"
        return _ok(f"Contents of {p}: {preview}{suffix}", items=items)
    except Exception as e:
        return _err(f"Could not list folder: {e}")


def rename_file(path: str, new_name: str):
    if not _check_perm("file_modify", f"Rename '{path}' to '{new_name}'"):
        return _err("File modification permission was denied.")
    try:
        src = _resolve_path(path)
        if not src.exists():
            return _err(f"That file or folder does not exist: {src}")
        name = Path(str(new_name).strip().strip('"')).name
        if not name:
            return _err("Please provide a new name.")
        dest = src.with_name(name)
        if dest.exists():
            return _err(f"A file or folder with that name already exists: {dest}")
        src.rename(dest)
        return _ok(f"Renamed {src.name} to {dest.name}.", path=str(dest))
    except Exception as e:
        return _err(f"Could not rename it: {e}")


def copy_file(source: str, destination: str):
    if not _check_perm("file_modify", f"Copy '{source}' to '{destination}'"):
        return _err("File modification permission was denied.")
    try:
        import shutil
        src = _resolve_path(source)
        dst = _resolve_path(destination)
        if not src.exists():
            return _err(f"The source does not exist: {src}")
        if dst.exists() and dst.is_dir():
            dst = dst / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                return _err(f"The destination already exists: {dst}")
            shutil.copytree(src, dst)
        else:
            if dst.exists():
                return _err(f"The destination already exists: {dst}")
            shutil.copy2(src, dst)
        return _ok(f"Copied {src.name} to {dst}.", path=str(dst))
    except Exception as e:
        return _err(f"Could not copy it: {e}")


def move_file(source: str, destination: str):
    if not _check_perm("file_modify", f"Move '{source}' to '{destination}'"):
        return _err("File modification permission was denied.")
    try:
        import shutil
        src = _resolve_path(source)
        dst = _resolve_path(destination)
        if not src.exists():
            return _err(f"The source does not exist: {src}")
        if dst.exists() and dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            return _err(f"The destination already exists: {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return _ok(f"Moved {src.name} to {dst}.", path=str(dst))
    except Exception as e:
        return _err(f"Could not move it: {e}")


def delete_file(path: str, confirm: bool = False):
    try:
        p = _resolve_path(path)
        prot = _is_protected_path(p)
        if prot:
            return _err(prot)
        if not p.exists():
            return _err(f"That file or folder does not exist: {p}")
        if not confirm:
            return {"ok": False, "confirmation_required": True, "message": f"Deleting {p} is destructive. Ask the user for confirmation before deleting it."}
        if not _check_perm("file_delete", f"Delete '{p}'", {"path": str(p)}):
            return _err("File deletion permission was denied.", permission_denied=True)
        import shutil
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return _ok(f"Deleted {p}.")
    except Exception as e:
        return _err(f"Could not delete it: {e}")


def write_text_file(filename: str, text: str):
    if not _check_perm("file_modify", f"Write text file '{filename}'"):
        return _err("File modification permission was denied.")
    try:
        p = WORKSPACE / Path(filename).name
        p.write_text(str(text), encoding="utf-8")
        return _ok(f"Wrote {p.name}.", path=str(p))
    except Exception as e:
        return _err(f"Could not write file: {e}")


def read_text_file(filename: str):
    if not _check_perm("document_access", f"Read text file '{filename}'"):
        return _err("Document access permission was denied.", permission_denied=True)
    try:
        p = WORKSPACE / Path(filename).name
        return _ok(f"Read {p.name}.", text=p.read_text(encoding="utf-8"))
    except Exception as e:
        return _err(f"Could not read file: {e}")


# Windows-native clipboard using user32/kernel32 APIs.
def _clipboard_get_text():
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    CF_UNICODETEXT = 13
    if not user32.OpenClipboard(None):
        raise OSError("Windows could not open the clipboard.")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            raise OSError("Windows could not read clipboard data.")
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _clipboard_empty():
    """Clear the current Windows clipboard without allocating a new item."""
    user32 = ctypes.windll.user32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                if not user32.EmptyClipboard():
                    raise OSError("Windows could not empty the clipboard.")
                return
            finally:
                user32.CloseClipboard()
        time.sleep(0.08)
    raise OSError("Windows could not open the clipboard after several attempts.")


def _clipboard_set_text(text: str):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_bool
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = str(text).encode("utf-16-le") + b"\x00\x00"
    hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not hmem:
        raise OSError("Windows could not allocate clipboard memory.")
    ptr = kernel32.GlobalLock(hmem)
    if not ptr:
        kernel32.GlobalFree(hmem)
        raise OSError("Windows could not lock clipboard memory.")
    try:
        ctypes.memmove(ptr, data, len(data))
    finally:
        kernel32.GlobalUnlock(hmem)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(hmem)
        raise OSError("Windows could not open the clipboard.")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, hmem):
            kernel32.GlobalFree(hmem)
            raise OSError("Windows could not set clipboard data.")
        hmem = None
    finally:
        user32.CloseClipboard()
        if hmem:
            kernel32.GlobalFree(hmem)


def clipboard_control(action: str, text: str = ""):
    if not _check_perm("mouse_keyboard", f"Clipboard control: '{action}'"):
        return _err("Mouse and keyboard permission was denied.", permission_denied=True)
    try:
        import pyautogui
        a = action.lower().strip().replace("-", "_").replace(" ", "_")
        if a in ("read", "get", "read_clipboard", "get_clipboard"):
            value = _clipboard_get_text()
            if value == "":
                return _ok("The clipboard is empty.", text="")
            return _ok("Read the clipboard.", text=value)
        if a in ("set", "write", "copy_text", "set_clipboard"):
            _clipboard_set_text(text)
            return _ok("Updated the clipboard.", text=str(text))
        if a in ("append", "append_text"):
            current = _clipboard_get_text()
            _clipboard_set_text(current + str(text))
            return _ok("Appended text to the clipboard.", text=current + str(text))
        if a in ("clear", "empty", "clear_clipboard", "delete_clipboard"):
            _clipboard_empty()
            return _ok("Cleared the current clipboard item.")
        if a in ("copy", "copy_selected", "copy_selection"):
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.15)
            value = _clipboard_get_text()
            return _ok("Copied the selected text to the clipboard.", text=value)
        if a in ("paste", "paste_clipboard"):
            pyautogui.hotkey("ctrl", "v")
            return _ok("Pasted the clipboard.")
        if a in ("history", "open_history", "clipboard_history"):
            pyautogui.hotkey("win", "v")
            return _ok("Opened Windows Clipboard History.")
        if a in ("delete_history", "clear_history", "clear_clipboard_history", "clipboard_history_clear"):
            pyautogui.hotkey("win", "v")
            time.sleep(0.35)
            return _ok("Opened Clipboard History. Use Clear all at the top to delete the history.")
        return _err("Unknown clipboard action.")
    except Exception as e:
        return _err(f"Could not control the clipboard: {type(e).__name__}: {e}")
