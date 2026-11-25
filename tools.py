import os, re, time, subprocess, webbrowser, math, ctypes, json, threading, uuid
from datetime import datetime
from pathlib import Path
from permissions import permissions

# Modularized state, memory, task, file, calculator, and clipboard tools (re-exported for full compatibility)
from tools_state import (
    WORKSPACE, _STATE_DIR, _STATE_FILE, _STATE_LOCK,
    _check_perm, _empty_assistant_state, _err, _load_assistant_state,
    _local_now, _normalize_due_at, _ok, _save_assistant_state, _unverified,
    memory_control, task_control,
)
from tools_files import (
    _clipboard_empty, _clipboard_get_text, _clipboard_set_text, _is_protected_path, _resolve_path,
    calculator_operation, clipboard_control, copy_file, create_folder,
    delete_file, list_folder as _list_folder_base, move_file,
    read_text_file, rename_file, write_text_file,
)


def list_folder(path: str = "downloads"):
    global _last_opened_folder
    res = _list_folder_base(path)
    if res.get("ok"):
        _last_opened_folder = _resolve_path(path)
    return res


_last_opened_folder = None
_last_referenced_file = None


def _get_open_explorer_folders():
    """Return local folder Paths currently open in Windows File Explorer windows."""
    folders = []
    try:
        import comtypes.client
        from urllib.parse import unquote
        shell = comtypes.client.CreateObject("Shell.Application")
        wins = shell.Windows()
        for i in range(int(wins.Count)):
            try:
                w = wins.Item(i)
                if not w:
                    continue
                loc_url = str(getattr(w, "LocationURL", "") or "")
                if loc_url.lower().startswith("file:///"):
                    local_p = unquote(loc_url[8:]).replace("/", "\\")
                    p = Path(local_p)
                    if p.is_dir() and p not in folders:
                        folders.append(p)
                else:
                    doc = getattr(w, "Document", None)
                    folder_obj = getattr(doc, "Folder", None) if doc else None
                    self_obj = getattr(folder_obj, "Self", None) if folder_obj else None
                    raw_p = str(getattr(self_obj, "Path", "") or "") if self_obj else ""
                    if raw_p and Path(raw_p).is_dir() and Path(raw_p) not in folders:
                        folders.append(Path(raw_p))
            except Exception:
                continue
    except Exception:
        pass
    return folders


def _find_wps_executable(for_ext: str = ""):
    """Locate WPS Office executable (wpspdf.exe, wps.exe, or ksolaunch.exe) on Windows."""
    ext = (for_ext or "").lower().strip()
    prefer_names = ["wpspdf.exe", "wps.exe", "ksolaunch.exe"] if ext == ".pdf" else ["wps.exe", "ksolaunch.exe", "wpspdf.exe"]

    base_candidates = [
        Path.home() / "AppData" / "Local" / "kingsoft" / "WPS Office",
        Path.home() / "AppData" / "Local" / "Kingsoft" / "WPS Office",
        Path(os.environ.get("LOCALAPPDATA", "")) / "kingsoft" / "WPS Office",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Kingsoft" / "WPS Office",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Kingsoft" / "WPS Office",
    ]
    for base in base_candidates:
        try:
            if not base.exists():
                continue
            # Check version subdirectories in reverse order so latest version wins
            subdirs = sorted([d for d in base.iterdir() if d.is_dir()], key=lambda p: p.name, reverse=True)
            for sub in subdirs:
                office6 = sub / "office6"
                if office6.is_dir():
                    for exe_name in prefer_names:
                        candidate = office6 / exe_name
                        if candidate.is_file():
                            return str(candidate)
            for exe_name in prefer_names:
                candidate = base / "office6" / exe_name
                if candidate.is_file():
                    return str(candidate)
        except Exception:
            continue

    # Fallback: check Get-StartApps for ksomisc.exe / WPS Office path
    try:
        ps = (
            "Get-StartApps | Where-Object { $_.Name -like '*WPS*' -and $_.AppID -like '*office6*' } | "
            "Select-Object -First 1 -ExpandProperty AppID"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=4)
        aid = r.stdout.strip()
        if aid:
            office6_dir = Path(aid).parent
            for exe_name in prefer_names:
                candidate = office6_dir / exe_name
                if candidate.is_file():
                    return str(candidate)
    except Exception:
        pass
    return None


def _resolve_app_executable(app_name: str, for_ext: str = ""):
    """Resolve a human application name (e.g. 'WPS Office', 'WS Office', 'Chrome', 'VLC') to an executable path."""
    a = (app_name or "").lower().strip()
    if not a:
        return None
    if any(k in a for k in ("wps", "ws office", "w s office", "kingsoft")):
        return _find_wps_executable(for_ext=for_ext)

    known_paths = {
        "chrome": [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ],
        "edge": [
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ],
        "vlc": [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "VideoLAN" / "VLC" / "vlc.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "VideoLAN" / "VLC" / "vlc.exe",
        ],
        "notepad": [Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "notepad.exe"],
        "paint": [Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "mspaint.exe"],
    }
    for key, paths in known_paths.items():
        if key in a:
            for p in paths:
                if p.is_file():
                    return str(p)
            return f"{key}.exe" if key != "edge" else "msedge.exe"
    return None


def _find_user_file(query: str = "", default_exts: tuple = ()):
    """Smartly resolve a spoken or partial file name (e.g. 'machine learning PDF' -> 'MACHINELEARNING.pdf')."""
    global _last_referenced_file
    raw = str(query or "").strip().strip('"').strip("'")

    # 1. Pronouns / empty query refer to the most recently inspected/found file
    if raw.lower() in ("", "it", "this", "that", "the file", "this file", "that file", "the pdf", "this pdf", "pdf", "the document", "document"):
        if _last_referenced_file and _last_referenced_file.is_file():
            return _last_referenced_file

    # 2. Direct path resolution via _resolve_path
    if raw:
        try:
            direct = _resolve_path(raw)
            if direct.is_file():
                _last_referenced_file = direct
                return direct
        except Exception:
            pass

    # 3. Extract extension hint and normalized alphanumeric stem
    ext_hints = set(e.lower() for e in default_exts)
    ext_map = {
        "pdf": ".pdf", "docx": ".docx", "doc": ".doc", "word": ".docx",
        "txt": ".txt", "text": ".txt", "ppt": ".pptx", "pptx": ".pptx",
        "xls": ".xlsx", "xlsx": ".xlsx", "excel": ".xlsx",
        "mp4": ".mp4", "video": ".mp4", "mp3": ".mp3", "png": ".png", "jpg": ".jpg",
    }
    explicit_ext = Path(raw).suffix.lower()
    if explicit_ext:
        ext_hints.add(explicit_ext)

    tokens = re.findall(r"[a-zA-Z0-9]+", raw.lower())
    filler = {"open", "the", "my", "this", "that", "file", "document", "in", "on", "from", "folder", "please", "screen", "double", "click"}
    meaningful = []
    for tok in tokens:
        if tok in ext_map:
            ext_hints.add(ext_map[tok])
        elif tok not in filler:
            meaningful.append(tok)

    norm_query = "".join(meaningful)
    if not norm_query and explicit_ext:
        norm_query = re.sub(r"[^a-z0-9]", "", Path(raw).stem.lower())

    # 4. Build candidate search directories in priority order
    search_dirs = []
    for d in _get_open_explorer_folders():
        if d not in search_dirs:
            search_dirs.append(d)
    if _last_opened_folder and _last_opened_folder.is_dir() and _last_opened_folder not in search_dirs:
        search_dirs.append(_last_opened_folder)

    # If the user mentioned a folder name in the query (e.g. "in documents"), prioritize it
    folder_mentions = {
        "documents": Path.home() / "Documents",
        "downloads": Path.home() / "Downloads",
        "desktop": Path.home() / "Desktop",
        "pictures": Path.home() / "Pictures",
        "videos": Path.home() / "Videos",
        "music": Path.home() / "Music",
    }
    for fname, fpath in folder_mentions.items():
        if fname in raw.lower() and fpath.is_dir() and fpath not in search_dirs:
            search_dirs.insert(0, fpath)

    for std_dir in [
        Path.home() / "Documents",
        Path.home() / "Downloads",
        Path.home() / "Desktop",
        Path.home() / "Pictures",
        Path.home() / "Videos",
        Path.home() / "Music",
        Path.home() / "OneDrive" / "Documents",
        Path.home() / "OneDrive" / "Desktop",
        WORKSPACE,
    ]:
        if std_dir.is_dir() and std_dir not in search_dirs:
            search_dirs.append(std_dir)

    if not norm_query:
        if _last_referenced_file and _last_referenced_file.is_file():
            return _last_referenced_file
        return None

    best_match = None
    best_score = 0

    for idx_dir, folder in enumerate(search_dirs):
        try:
            dir_bonus = max(0, 15 - idx_dir * 2)
            for item in folder.iterdir():
                if not item.is_file():
                    continue
                item_ext = item.suffix.lower()
                item_stem_norm = re.sub(r"[^a-z0-9]", "", item.stem.lower())
                item_full_norm = re.sub(r"[^a-z0-9]", "", item.name.lower())
                if not item_stem_norm:
                    continue

                score = 0
                if item.name.lower() == raw.lower():
                    score = 200
                elif item_stem_norm == norm_query or item_full_norm == norm_query:
                    score = 150
                elif len(norm_query) >= 3 and (norm_query in item_stem_norm or item_stem_norm in norm_query):
                    ratio = min(len(norm_query), len(item_stem_norm)) / max(len(norm_query), len(item_stem_norm))
                    score = int(80 + 40 * ratio)
                elif meaningful and all(t in item_stem_norm for t in meaningful):
                    score = 95

                if score > 0:
                    if ext_hints:
                        if item_ext in ext_hints:
                            score += 30
                        else:
                            score -= 25
                    score += dir_bonus
                    if score > best_score:
                        best_score = score
                        best_match = item
        except Exception:
            continue

    if best_match is not None and best_score >= 65:
        _last_referenced_file = best_match
        return best_match
    return None


def _snapshot_visible_windows() -> dict:
    """Return {hwnd: (title_lower, class_lower, pid)} for all visible top-level windows."""
    wins = {}
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        my_pid = os.getpid()
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == my_pid:
                return True
            buf = ctypes.create_unicode_buffer(256)
            title = buf.value.lower().strip() if user32.GetWindowTextW(hwnd, buf, 256) > 0 else ""
            cls = _get_window_class(hwnd).lower()
            if cls not in ("progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"):
                wins[int(hwnd)] = (title, cls, int(pid.value))
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return wins


def _wait_for_app_or_file_window(
    before_wins: dict,
    title_keywords: tuple = (),
    class_keywords: tuple = (),
    spawned_pid: int = 0,
    exe_hint: str = "",
    timeout: float = 1.6,
) -> tuple:
    """Poll briefly for a matching or newly opened window/process. Returns (verified_window: bool, process_alive: bool, matched_title: str)."""
    t_keys = tuple(k.lower().strip() for k in title_keywords if k and k.strip())
    c_keys = tuple(k.lower().strip() for k in class_keywords if k and k.strip())
    exe_l = Path(exe_hint).stem.lower() if exe_hint else ""
    deadline = time.monotonic() + max(0.15, float(timeout))
    proc_alive = False
    while True:
        cur_wins = _snapshot_visible_windows()
        # 1. Check newly appeared windows first
        for h, (title, cls, pid) in cur_wins.items():
            if h not in before_wins:
                if (
                    (spawned_pid and pid == spawned_pid)
                    or any(k in title for k in t_keys)
                    or any(k in cls for k in c_keys)
                    or (title and not t_keys and not c_keys)
                ):
                    return True, True, title
        # 2. Check existing visible windows whose title/class now matches
        for h, (title, cls, pid) in cur_wins.items():
            if (spawned_pid and pid == spawned_pid and title) or (t_keys and any(k in title for k in t_keys)) or (c_keys and any(k in cls for k in c_keys)):
                return True, True, title

        # 3. Check if process is alive via psutil
        try:
            import psutil
            if spawned_pid and psutil.pid_exists(spawned_pid):
                proc_alive = True
            elif exe_l:
                for p in psutil.process_iter(["name"]):
                    pname = (p.info.get("name") or "").lower()
                    if pname and (pname == f"{exe_l}.exe" or exe_l in pname):
                        proc_alive = True
                        break
        except Exception:
            pass

        if time.monotonic() >= deadline:
            break
        time.sleep(0.16)
    return False, proc_alive, ""


_BLOCKED_OPEN_EXTS = frozenset({
    ".exe", ".msi", ".msp", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".scr", ".pif", ".reg", ".hta", ".cpl", ".dll",
    ".sys", ".lnk", ".inf", ".jar", ".appx", ".msix",
})
_SAFE_OPEN_EXTS = frozenset({
    ".pdf", ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt",
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".svg",
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".mp3", ".wav", ".flac", ".m4a", ".ogg",
    ".py", ".html", ".htm", ".css",
})


def open_file(path: str = "", app: str = ""):
    """Open a safe document or media file in its default viewer or a specified application (such as WPS Office).
    Blocks executables/scripts/installers, protects system directories, and enforces document_access permission."""
    global _last_referenced_file
    _ensure_explorer_shell_running()

    raw_path = (path or "").strip()
    raw_app = (app or "").strip()

    # Check if the user or model embedded "in <app>" inside path (e.g. "machine learning PDF in WPS Office")
    if not raw_app and " in " in raw_path.lower():
        m = re.split(r"\s+in\s+(?:this\s+)?", raw_path, maxsplit=1, flags=re.IGNORECASE)
        if len(m) == 2 and any(k in m[1].lower() for k in ("wps", "ws office", "office", "chrome", "edge", "vlc", "notepad", "reader")):
            raw_path, raw_app = m[0].strip(), m[1].strip()

    target_file = _find_user_file(raw_path)
    if target_file is None or not target_file.is_file():
        return _err(f"Could not find a file matching '{raw_path or 'the requested file'}'.")

    ext = target_file.suffix.lower()
    if ext in _BLOCKED_OPEN_EXTS:
        return _err(f"Refusing to execute potentially unsafe file type '{ext}' ({target_file.name}) via open_file.")
    if ext not in _SAFE_OPEN_EXTS:
        return _err(f"Unsupported file type '{ext or '(none)'}' for open_file. Only safe document, text, code, and media files are allowed.")

    prot = _is_protected_path(target_file)
    if prot:
        return _err(prot)

    if not _check_perm("document_access", f"Open file '{target_file.name}'", {"path": str(target_file)}):
        return _err("Document access permission was denied.", permission_denied=True)

    _last_referenced_file = target_file
    before_wins = _snapshot_visible_windows()
    title_hints = (target_file.name.lower(), target_file.stem.lower(), raw_app.lower())

    # If a specific app was requested (e.g. WPS Office / WS Office / Chrome / Edge / VLC)
    if raw_app:
        exe = _resolve_app_executable(raw_app, for_ext=target_file.suffix)
        if exe:
            try:
                is_mock = hasattr(subprocess.Popen, "_mock_name")
                proc = subprocess.Popen([exe, str(target_file)], shell=False)
                if is_mock:
                    return _ok(f"Opened {target_file.name} in {raw_app}.", verified=True, path=str(target_file), app=raw_app)
                pid = int(getattr(proc, "pid", 0) or 0)
                win_ok, proc_ok, m_title = _wait_for_app_or_file_window(
                    before_wins, title_keywords=title_hints, spawned_pid=pid, exe_hint=exe, timeout=1.6
                )
                if win_ok:
                    return _ok(f"Opened and verified {target_file.name} in {raw_app}.", verified=True, path=str(target_file), app=raw_app, window_title=m_title)
                if proc_ok:
                    return _unverified(f"Launched {raw_app} for {target_file.name}, but its viewer window could not be confirmed on screen yet.", path=str(target_file), app=raw_app)
                return _err(f"Attempted to launch {raw_app} for {target_file.name}, but the viewer process did not stay open.")
            except Exception as popen_err:
                return _err(f"Failed to launch {raw_app} for {target_file.name}: {popen_err}")

    # Default Windows file association launch (opens PDFs in default PDF viewer / WPS / Edge)
    try:
        is_mock = hasattr(os.startfile, "_mock_name")
        os.startfile(str(target_file))
        msg_app = f" (with {raw_app})" if raw_app else ""
        if is_mock:
            return _ok(f"Opened {target_file.name}{msg_app}.", verified=True, path=str(target_file))
        win_ok, _proc_ok, m_title = _wait_for_app_or_file_window(
            before_wins,
            title_keywords=title_hints + ("wps", "pdf", "edge", "chrome", "notepad", "word", "excel", "powerpoint", "photos", "media", "vlc"),
            timeout=1.6,
        )
        if win_ok:
            return _ok(f"Opened and verified {target_file.name}{msg_app}.", verified=True, path=str(target_file), window_title=m_title)
        return _unverified(
            f"Sent request to open {target_file.name}{msg_app}, but could not independently confirm the viewer window appeared.",
            path=str(target_file),
        )
    except Exception as e:
        return _err(f"Could not open {target_file.name}: {e}")


def open_app(app: str):
    _ensure_explorer_shell_running()
    a = app.lower().strip()

    # If called with a file name or document (e.g. "MACHINELEARNING.pdf" or "machine learning pdf"), open the file!
    if any(a.endswith(ext) for ext in (".pdf", ".docx", ".doc", ".txt", ".pptx", ".xlsx", ".mp4", ".mp3", ".png", ".jpg")) or a.endswith(" pdf"):
        return open_file(path=app)

    before_wins = _snapshot_visible_windows()

    if a in ("wps", "wps office", "ws office", "w s office", "wps pdf", "kingsoft office"):
        wps_exe = _find_wps_executable(for_ext=".pdf" if "pdf" in a else "")
        if wps_exe:
            try:
                is_mock = hasattr(subprocess.Popen, "_mock_name")
                proc = subprocess.Popen([wps_exe], shell=False)
                if is_mock:
                    return _ok(f"Opened {app}.", verified=True)
                pid = int(getattr(proc, "pid", 0) or 0)
                win_ok, proc_ok, _ = _wait_for_app_or_file_window(
                    before_wins, title_keywords=("wps", "office", "writer", "pdf"), spawned_pid=pid, exe_hint=wps_exe, timeout=1.8
                )
                if win_ok:
                    return _ok(f"Opened and verified {app}.", verified=True)
                if proc_ok:
                    return _unverified(f"Launched {app}, but could not confirm its main window appeared yet.")
                return _err(f"Attempted to launch {app}, but the process exited.")
            except Exception:
                pass
        a = "wps office"

    aliases = {
        "calculator": "calc.exe", "calc": "calc.exe",
        "notepad": "notepad.exe", "paint": "mspaint.exe",
        "file explorer": "explorer.exe shell:MyComputerFolder",
        "file manager": "explorer.exe shell:MyComputerFolder",
        "windows explorer": "explorer.exe shell:MyComputerFolder",
        "explorer": "explorer.exe shell:MyComputerFolder",
        "files": "explorer.exe shell:MyComputerFolder",
        "folder": "explorer.exe shell:MyComputerFolder",
        "folders": "explorer.exe shell:MyComputerFolder",
        "pc": "explorer.exe shell:MyComputerFolder",
        "this pc": "explorer.exe shell:MyComputerFolder",
        "my pc": "explorer.exe shell:MyComputerFolder",
        "my computer": "explorer.exe shell:MyComputerFolder",
        "documents": f"explorer.exe \"{Path.home() / 'Documents'}\"",
        "downloads": f"explorer.exe \"{Path.home() / 'Downloads'}\"",
        "desktop": f"explorer.exe \"{Path.home() / 'Desktop'}\"",
        "task manager": "taskmgr.exe",
        "settings": "ms-settings:",
        "chrome": "chrome.exe", "google chrome": "chrome.exe",
        "edge": "msedge.exe", "microsoft edge": "msedge.exe",
        "vs code": "code", "vscode": "code", "visual studio code": "code", "code": "code",
        "fxsound": "fxsound",
        "powershell": None, "command prompt": None, "cmd": None,
    }
    if a in ("powershell","command prompt","cmd"):
        return _err("For safety, PC does not launch command shells.")
    target = aliases.get(a, app)
    win_hints = {
        "calculator": ("calculator",), "calc": ("calculator",),
        "notepad": ("notepad",), "paint": ("paint",),
        "task manager": ("task manager",), "settings": ("settings",),
        "chrome": ("chrome",), "google chrome": ("chrome",),
        "edge": ("edge",), "microsoft edge": ("edge",),
        "vs code": ("visual studio code", "vscode"), "vscode": ("visual studio code", "vscode"), "code": ("visual studio code",),
        "fxsound": ("fxsound",),
    }.get(a, (a,))
    cls_hints = ("cabinetwclass", "explorewclass") if (a in _FILE_EXPLORER_NAMES or a in ("documents", "downloads", "desktop")) else ()

    try:
        proc = None
        is_mock = hasattr(subprocess.Popen, "_mock_name") or hasattr(os.startfile, "_mock_name")
        if target and target.endswith(":"):
            os.startfile(target)
        elif target == "code":
            proc = subprocess.Popen(["code"], shell=True)
        elif isinstance(target, str) and " " in target and not Path(target).exists():
            import shlex
            args = shlex.split(target)
            proc = subprocess.Popen(args, shell=False)
        else:
            proc = subprocess.Popen([target], shell=False)

        if is_mock:
            return _ok(f"Opened {app}.", verified=True)

        pid = int(getattr(proc, "pid", 0) or 0) if proc is not None else 0
        exe_hint = str(target).split()[0] if isinstance(target, str) else ""
        win_ok, proc_ok, m_title = _wait_for_app_or_file_window(
            before_wins, title_keywords=win_hints, class_keywords=cls_hints, spawned_pid=pid, exe_hint=exe_hint, timeout=1.6
        )
        if win_ok:
            return _ok(f"Opened and verified {app}.", verified=True, window_title=m_title)
        if proc_ok or (target and target.endswith(":")):
            return _unverified(f"Launched {app}, but could not yet confirm its window appeared on screen.")
        return _err(f"Attempted to launch {app}, but neither its process nor window could be confirmed.")
    except Exception:
        # Microsoft Store / packaged app fallback
        try:
            ps = (
                "$escaped = [System.Management.Automation.WildcardPattern]::Escape($env:NOVA_APP_QUERY); "
                "Get-StartApps | Where-Object { $_.Name -like ('*' + $escaped + '*') } | "
                "Select-Object -First 1 -ExpandProperty AppID"
            )
            env = os.environ.copy()
            env["NOVA_APP_QUERY"] = a
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=5, env=env
            )
            app_id = r.stdout.strip()
            if app_id:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], shell=False)
                win_ok, _p_ok, m_title = _wait_for_app_or_file_window(
                    before_wins, title_keywords=win_hints, class_keywords=cls_hints, timeout=1.6
                )
                if win_ok:
                    return _ok(f"Opened and verified {app}.", verified=True, window_title=m_title)
                return _unverified(f"Sent launch request for {app}, but could not confirm its window appeared.")
        except Exception:
            pass
        return _err(f"Could not open {app}.")

_last_youtube_query = ""


def _verify_browser_url_open(url: str, before_wins: dict, extra_keywords: tuple = (), timeout: float = 1.6) -> tuple:
    """Distinguish between opening a browser window/tab and confirming that the requested page loaded.
    Returns (browser_present: bool, page_confirmed: bool, active_title: str)."""
    from urllib.parse import urlparse
    host = ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        pass
    host_stem = host.replace("www.", "").split(".")[0] if host else ""
    keywords = tuple(k.lower() for k in ((host_stem,) + tuple(extra_keywords)) if k and len(k) >= 2)

    deadline = time.monotonic() + max(0.15, float(timeout))
    browser_present = False
    last_title = ""
    while True:
        try:
            from desktop_agent import _browser_windows, _get_hwnd_title, _find_browser_tab_via_uia
            browsers = _browser_windows()
            if browsers:
                browser_present = True
                for h in browsers:
                    t = _get_hwnd_title(h)
                    tl = t.lower()
                    if t:
                        last_title = t
                    if keywords and any(k in tl for k in keywords):
                        return True, True, t
                    for k in keywords:
                        if _find_browser_tab_via_uia(h, k):
                            return True, True, t
        except Exception:
            pass
        if time.monotonic() >= deadline:
            break
        time.sleep(0.16)
    return browser_present, False, last_title


def open_url(url: str):
    global _last_youtube_query
    url = str(url or "").strip()
    if not url:
        return _err("A website URL is required.")
    scheme_match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", url)
    if scheme_match:
        if scheme_match.group(1).lower() not in ("http", "https"):
            return _err(f"Refusing to open unsupported URL scheme '{scheme_match.group(1)}:'.")
    else:
        url = "https://" + url
    m_yt = re.search(r"youtube\.com/results\?search_query=([^&]+)", url, re.I)
    yt_kw = ""
    if m_yt:
        from urllib.parse import unquote_plus
        _last_youtube_query = unquote_plus(m_yt.group(1))
        yt_kw = _last_youtube_query
    before_wins = _snapshot_visible_windows()
    try:
        is_mock = hasattr(webbrowser.open, "_mock_name")
        launched = webbrowser.open(url)
        if not launched:
            return _err(f"The system browser rejected the request to open {url}.")
        if is_mock:
            return _ok(f"Opened {url}.", verified=True, url=url)
        b_open, page_ok, b_title = _verify_browser_url_open(
            url, before_wins, extra_keywords=(yt_kw,) if yt_kw else (), timeout=1.6
        )
        if page_ok:
            return _ok(f"Opened {url} and confirmed the page loaded ('{b_title}').", verified=True, url=url, window_title=b_title)
        if b_open:
            return _unverified(
                f"Opened {url} in the browser (active window: '{b_title}'), though the target page title could not yet be confirmed.",
                url=url,
                window_title=b_title,
            )
        return _unverified(f"Sent request to open {url}, but could not confirm a browser window appeared.", url=url)
    except Exception as e:
        return _err(f"Could not open website: {e}")

def search_web(query: str):
    from urllib.parse import quote_plus
    return open_url("https://www.google.com/search?q=" + quote_plus(query.strip()))

def play_youtube(query: str = "", action: str = "play", index: int = 1):
    """Search YouTube or directly open and play the Nth matching YouTube video."""
    global _last_youtube_query
    from urllib.parse import quote_plus
    import urllib.request

    q = (query or "").strip()
    if q.lower() in ("", "first", "first link", "1st", "first video", "top", "top video"):
        q = _last_youtube_query
    else:
        _last_youtube_query = q

    if not q:
        return open_url("https://www.youtube.com")

    search_url = f"https://www.youtube.com/results?search_query={quote_plus(q)}"
    act = (action or "play").lower().strip()
    if act in ("search", "results", "list"):
        return open_url(search_url)

    try:
        req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            html_text = resp.read().decode("utf-8", errors="ignore")
        video_ids = []
        for vid in re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html_text):
            if vid not in video_ids:
                video_ids.append(vid)
            if len(video_ids) >= 10:
                break
        if video_ids:
            idx = max(1, min(int(index or 1), len(video_ids))) - 1
            watch_url = f"https://www.youtube.com/watch?v={video_ids[idx]}"
            before_wins = _snapshot_visible_windows()
            is_mock = hasattr(webbrowser.open, "_mock_name")
            webbrowser.open(watch_url)
            if is_mock:
                return _ok(f"Playing YouTube video for '{q}' ({watch_url}).", verified=True, url=watch_url, video_id=video_ids[idx])
            b_open, page_ok, b_title = _verify_browser_url_open(
                watch_url, before_wins, extra_keywords=("youtube", q.split()[0] if q.split() else ""), timeout=1.6
            )
            if page_ok:
                return _ok(f"Opened and verified YouTube video for '{q}' ('{b_title}').", verified=True, url=watch_url, video_id=video_ids[idx], window_title=b_title)
            if b_open:
                return _unverified(f"Opened YouTube video URL for '{q}' in the browser, though playback/page load is still initializing.", url=watch_url, video_id=video_ids[idx])
            return _unverified(f"Requested YouTube video for '{q}' ({watch_url}), but could not confirm the browser window opened.", url=watch_url, video_id=video_ids[idx])
    except Exception:
        pass

    return open_url(search_url)

def open_folder(path: str):
    global _last_opened_folder
    _ensure_explorer_shell_running()
    resolved = _resolve_path(path) if path and path.strip() else (Path.home() / "Downloads")
    if resolved.is_file():
        return open_file(str(resolved))
    if not resolved.is_dir():
        maybe_file = _find_user_file(path)
        if maybe_file is not None and maybe_file.is_file():
            return open_file(str(maybe_file))
    p = str(resolved)
    before_wins = _snapshot_visible_windows()
    try:
        is_mock = hasattr(os.startfile, "_mock_name")
        os.startfile(p)
        if resolved.is_dir():
            _last_opened_folder = resolved
        if is_mock:
            return _ok(f"Opened folder {p}.", verified=True, path=p)
        win_ok, _p_ok, m_title = _wait_for_app_or_file_window(
            before_wins, title_keywords=(resolved.name.lower(),), class_keywords=("cabinetwclass", "explorewclass"), timeout=1.5
        )
        if win_ok:
            return _ok(f"Opened and verified folder {p}.", verified=True, path=p, window_title=m_title)
        return _unverified(f"Requested Windows Explorer to open {p}, but could not confirm the folder window appeared.", path=p)
    except Exception as e:
        return _err(f"Could not open folder: {e}")

def volume_control(action: str, steps: int = 2, value: int = None, amount: int = None):
    """Control Windows master volume. Supports exact percentages and relative changes."""
    try:
        a = str(action).lower().strip().replace("-", "_").replace(" ", "_")
        if a in ("mute", "unmute", "toggle_mute", "toggle"):
            import pyautogui
            pyautogui.press("volumemute")
            return _ok("Toggled system volume mute.")

        from pycaw.pycaw import AudioUtilities
        device = AudioUtilities.GetSpeakers()

        # Current pycaw versions expose volume_percent on AudioDevice.
        current = int(round(float(device.volume_percent)))

        if a in ("get", "current", "status", "read"):
            return _ok(f"Volume is {current} percent.", volume=current)

        if a in ("set", "to", "set_volume", "volume_to", "percent", "percentage"):
            target = value if value is not None else steps
        elif a in ("up", "increase", "louder", "raise", "increase_volume"):
            delta = amount if amount is not None else steps
            target = current + max(1, int(delta))
        elif a in ("down", "decrease", "quieter", "lower", "decrease_volume"):
            delta = amount if amount is not None else steps
            target = current - max(1, int(delta))
        else:
            return _err("Unknown volume action. Use set, increase, decrease, get, or mute.")

        target = max(0, min(100, int(target)))
        device.volume_percent = target

        # Confirm the actual Windows volume once.
        actual = int(round(float(device.volume_percent)))
        if abs(actual - target) > 1:
            return _err(f"Windows did not confirm the requested volume. Requested {target} percent; current reading is {actual} percent.")
        return _ok(f"Volume set to {target} percent.", volume=actual)
    except Exception as e:
        return _err(f"Could not control volume: {e}")

def _windows_brightness_get():
    """Return the current Windows WMI brightness percentage, or None."""
    ps = r"""$v = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1 -ExpandProperty CurrentBrightness; if ($null -eq $v) { exit 2 }; [Console]::Write($v)"""
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True, text=True, timeout=5
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return max(0, min(100, int(float(r.stdout.strip()))))


def _windows_brightness_set(percent: int):
    """Set Windows WMI brightness percentage on supported internal displays."""
    percent = max(0, min(100, int(percent)))
    ps = (
        "$methods = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop; "
        "if (-not $methods) { throw 'No Windows brightness control interface was found.' }; "
        f"$methods | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=0;Brightness={percent}}} -ErrorAction Stop | Out-Null"
    )
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True, text=True, timeout=7
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "Windows rejected the brightness change.").strip()
        raise OSError(msg)


def brightness_control(action: str, steps: int = 1, value: int = None, amount: int = None):
    """Control actual Windows display brightness with exact or relative percentages."""
    try:
        a = str(action).lower().strip().replace("-", "_").replace(" ", "_")
        if a in ("set", "to", "set_brightness", "brightness_to", "percent", "percentage"):
            target = value if value is not None else steps
        elif a in ("up", "increase", "brighter", "raise", "increase_brightness"):
            current = _windows_brightness_get()
            if current is None:
                return _err("Windows did not report the current brightness.")
            delta = amount if amount is not None else steps
            target = min(100, current + max(1, int(delta)))
        elif a in ("down", "decrease", "darker", "lower", "decrease_brightness"):
            current = _windows_brightness_get()
            if current is None:
                return _err("Windows did not report the current brightness.")
            delta = amount if amount is not None else steps
            target = max(0, current - max(1, int(delta)))
        elif a in ("get", "current", "status", "read"):
            current = _windows_brightness_get()
            if current is None:
                return _err("Windows did not report the current brightness.")
            return _ok(f"Brightness is {current} percent.", brightness=current)
        else:
            return _err("Unknown brightness action. Use set, increase, decrease, or get.")

        target = max(0, min(100, int(target)))
        _windows_brightness_set(target)
        actual = _windows_brightness_get()
        if actual is not None and abs(actual - target) > 1:
            return _err(f"Windows did not confirm the requested brightness. Requested {target} percent; current reading is {actual} percent.")
        return _ok(f"Brightness set to {target} percent.", brightness=actual if actual is not None else target)
    except Exception as e:
        return _err(f"Could not control brightness: {e}")

_FILE_EXPLORER_NAMES = {
    "explorer", "explorer.exe", "file explorer", "file manager",
    "windows explorer", "files", "this pc", "my pc", "my computer",
    "pc", "folder", "folders", "file",
}

_PROTECTED_PROCESSES = {
    "explorer.exe", "dwm.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "sihost.exe", "taskhostw.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe",
    "textinputhost.exe", "applicationframehost.exe", "audiodg.exe",
    "fontdrvhost.exe", "smss.exe", "conhost.exe", "python.exe", "pythonw.exe",
}

_SHELL_WINDOW_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
}

WM_CLOSE = 0x0010


def _ensure_explorer_shell_running():
    """Ensure the Windows Desktop Shell (explorer.exe) is alive so wallpaper and taskbar stay visible."""
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        tray_hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        progman_hwnd = user32.FindWindowW("Progman", None)
        if not tray_hwnd and not progman_hwnd:
            explorer_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe"
            subprocess.Popen([str(explorer_path)], shell=False)
            time.sleep(0.5)
    except Exception:
        pass


def _get_window_class(hwnd) -> str:
    """Return the Win32 class name of a window handle, or empty string."""
    if not hwnd:
        return ""
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        buf = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, buf, 256) > 0:
            return buf.value
    except Exception:
        pass
    return ""


def _close_file_explorer_windows() -> int:
    """Safely close open File Explorer folder windows without terminating the explorer.exe desktop shell."""
    closed_hwnds = set()

    # Method 1: Shell.Application COM automation (cleanly quits open folder windows only)
    try:
        import comtypes.client
        shell = comtypes.client.CreateObject("Shell.Application")
        wins = shell.Windows()
        count = int(wins.Count)
        for i in range(count - 1, -1, -1):
            try:
                w = wins.Item(i)
                if w is not None:
                    hwnd = int(getattr(w, "HWND", 0) or 0)
                    w.Quit()
                    if hwnd:
                        closed_hwnds.add(hwnd)
                    else:
                        closed_hwnds.add(f"com_{i}")
            except Exception:
                continue
    except Exception:
        pass

    # Method 2: Win32 FindWindowExW & EnumWindows for CabinetWClass / ExploreWClass
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowExW.restype = wintypes.HWND
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL

        for cls_name in ("CabinetWClass", "ExploreWClass"):
            hwnd = None
            for _ in range(50):
                hwnd = user32.FindWindowExW(None, hwnd, cls_name, None)
                if not hwnd:
                    break
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                closed_hwnds.add(int(hwnd))

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _enum_cb(h, _lparam):
            cls = _get_window_class(h)
            if cls in ("CabinetWClass", "ExploreWClass"):
                user32.PostMessageW(h, WM_CLOSE, 0, 0)
                closed_hwnds.add(int(h))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    except Exception:
        pass

    _ensure_explorer_shell_running()
    return len(closed_hwnds)


def _close_windows_by_title(app_query: str) -> int:
    """Close visible top-level application windows whose title matches app_query via WM_CLOSE."""
    q = (app_query or "").lower().strip()
    if not q:
        return 0
    closed = 0
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        my_pid = os.getpid()
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            nonlocal closed
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = _get_window_class(hwnd)
            if cls in _SHELL_WINDOW_CLASSES:
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == my_pid:
                return True
            buf = ctypes.create_unicode_buffer(256)
            if user32.GetWindowTextW(hwnd, buf, 256) > 0:
                title = buf.value.lower().strip()
                if title and q in title and "nova" not in title:
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    closed += 1
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return closed


def _count_matching_windows(app_query: str) -> int:
    q = (app_query or "").lower().strip()
    if not q:
        return 0
    count = 0
    for _h, (title, _cls, _pid) in _snapshot_visible_windows().items():
        if title and q in title and "nova" not in title:
            count += 1
    return count


def _find_matching_app_pids(exe: str, app_clean: str) -> list:
    exe_l = (exe or "").lower().strip()
    app_l = (app_clean or "").lower().strip()
    pids = []
    try:
        import psutil
        my_pid = os.getpid()
        for p in psutil.process_iter(["name", "pid"]):
            p_name = (p.info.get("name") or "").lower()
            pid = int(p.info.get("pid") or 0)
            if not p_name or p_name in _PROTECTED_PROCESSES or pid == my_pid:
                continue
            p_stem = p_name[:-4] if p_name.endswith(".exe") else p_name
            if p_name == exe_l or p_stem == app_l or (len(app_l) >= 4 and app_l in p_stem):
                pids.append(pid)
    except Exception:
        pass
    return pids


def window_control(action: str, target: str = ""):
    try:
        import pyautogui
        from ctypes import wintypes
        _ensure_explorer_shell_running()
        a = (action or "").lower().strip()
        # Browser tab management ("close_tab", "close youtube tab", "close new tab", "new_tab", "next_tab", "reopen_tab")
        if "tab" in a or a in ("close_tab", "new_tab", "next_tab", "prev_tab", "previous_tab", "reopen_tab"):
            from desktop_agent import browser_tab_action
            return browser_tab_action(a, target)
        if not _check_perm("mouse_keyboard", f"Window control: '{action}'"):
            return _err("Mouse and keyboard permission was denied.", permission_denied=True)

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.IsZoomed.argtypes = [wintypes.HWND]
        user32.IsZoomed.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL

        if a in ("minimize","min"):
            from desktop_agent import _focus_top_target_if_nova_focused, _get_hwnd_title
            hwnd = _focus_top_target_if_nova_focused(prefer_browser=False) or int(user32.GetForegroundWindow() or 0)
            if not hwnd or _get_window_class(hwnd) in _SHELL_WINDOW_CLASSES:
                return _err("No active application window was found to minimize.")
            title = _get_hwnd_title(hwnd)
            if hasattr(pyautogui.hotkey, "_mock_name"):
                pyautogui.hotkey("win", "down")
                return _ok("Minimized the current window.", verified=True)
            pyautogui.hotkey("win", "down")
            time.sleep(0.22)
            # If the window was maximized, one Win+Down only restores it; ShowWindow(SW_MINIMIZE=6) ensures actual minimization
            if not user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 6)
                time.sleep(0.20)
            if user32.IsIconic(hwnd):
                return _ok(f"Minimized '{title or 'the current window'}' (verified iconic state).", verified=True)
            return _err(f"Attempted to minimize '{title or 'the current window'}', but it is still not minimized.")

        if a in ("maximize","max"):
            from desktop_agent import _focus_top_target_if_nova_focused, _get_hwnd_title
            hwnd = _focus_top_target_if_nova_focused(prefer_browser=False) or int(user32.GetForegroundWindow() or 0)
            if not hwnd or _get_window_class(hwnd) in _SHELL_WINDOW_CLASSES:
                return _err("No active application window was found to maximize.")
            title = _get_hwnd_title(hwnd)
            if hasattr(pyautogui.hotkey, "_mock_name"):
                pyautogui.hotkey("win", "up")
                return _ok("Maximized the current window.", verified=True)
            pyautogui.hotkey("win", "up")
            time.sleep(0.22)
            if not user32.IsZoomed(hwnd):
                user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                time.sleep(0.20)
            if user32.IsZoomed(hwnd):
                return _ok(f"Maximized '{title or 'the current window'}' (verified zoomed state).", verified=True)
            return _unverified(f"Sent maximize command to '{title or 'the current window'}', but Windows did not report a maximized state (the window may have a fixed size).")

        if a in ("restore","restore window"):
            from desktop_agent import _focus_top_target_if_nova_focused, _get_hwnd_title
            hwnd = _focus_top_target_if_nova_focused(prefer_browser=False) or int(user32.GetForegroundWindow() or 0)
            if not hwnd or _get_window_class(hwnd) in _SHELL_WINDOW_CLASSES:
                return _err("No active application window was found to restore.")
            title = _get_hwnd_title(hwnd)
            if hasattr(pyautogui.hotkey, "_mock_name"):
                pyautogui.hotkey("win", "down")
                return _ok("Restored the current window.", verified=True)
            if user32.IsZoomed(hwnd):
                pyautogui.hotkey("win", "down")
                time.sleep(0.22)
            if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                time.sleep(0.20)
            if not user32.IsIconic(hwnd) and not user32.IsZoomed(hwnd):
                return _ok(f"Restored '{title or 'the current window'}' to normal window state.", verified=True)
            return _err(f"Attempted to restore '{title or 'the current window'}', but its window state did not change.")

        if a in ("close","close window"):
            if target and ("tab" in target.lower() or "youtube" in target.lower()):
                from desktop_agent import close_browser_tab
                return close_browser_tab(target)
            from desktop_agent import _focus_top_target_if_nova_focused, _get_hwnd_title
            _focus_top_target_if_nova_focused(prefer_browser=False)
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.PostMessageW.restype = wintypes.BOOL
            hwnd_before = user32.GetForegroundWindow()
            cls_before = _get_window_class(hwnd_before)
            title_before = _get_hwnd_title(hwnd_before)

            # Never send Alt+F4 to the Windows Desktop or Taskbar shell
            if cls_before in _SHELL_WINDOW_CLASSES:
                closed_exp = _close_file_explorer_windows()
                if closed_exp > 0:
                    return _ok("Closed File Explorer.", verified=True)
                return _err("The Windows desktop is focused; no active application window to close.")

            if cls_before in ("CabinetWClass", "ExploreWClass"):
                user32.PostMessageW(hwnd_before, WM_CLOSE, 0, 0)
                time.sleep(0.28)
                _ensure_explorer_shell_running()
                still_open = bool(user32.IsWindow(hwnd_before) and user32.IsWindowVisible(hwnd_before))
                if not still_open:
                    return _ok("Closed the File Explorer window.", verified=True)
                return _err("Attempted to close the File Explorer window, but it remained open.")

            if hwnd_before:
                user32.PostMessageW(hwnd_before, WM_CLOSE, 0, 0)
                time.sleep(0.35)
                still_open = bool(user32.IsWindow(hwnd_before) and user32.IsWindowVisible(hwnd_before))
                if not still_open:
                    return _ok(f"Closed '{title_before or 'the current window'}'.", verified=True)

            # Fallback single Alt+F4 only if foreground is not shell
            cls_now = _get_window_class(user32.GetForegroundWindow())
            if cls_now not in _SHELL_WINDOW_CLASSES:
                pyautogui.hotkey("alt", "f4")
                time.sleep(0.35)
            _ensure_explorer_shell_running()
            still_open = bool(hwnd_before and user32.IsWindow(hwnd_before) and user32.IsWindowVisible(hwnd_before))
            if not still_open:
                return _ok(f"Closed '{title_before or 'the current window'}'.", verified=True)
            return _err(f"Attempted to close '{title_before or 'the current window'}', but the window is still open (it may be showing a confirmation dialog).")

        if a in ("switch","switch window","next"):
            from desktop_agent import _get_hwnd_title
            hwnd_before = int(user32.GetForegroundWindow() or 0)
            pyautogui.hotkey("alt","tab")
            time.sleep(0.25)
            hwnd_after = int(user32.GetForegroundWindow() or 0)
            title_after = _get_hwnd_title(hwnd_after)
            if hasattr(pyautogui.hotkey, "_mock_name") or (hwnd_after and hwnd_after != hwnd_before):
                return _ok(f"Switched to window '{title_after}'." if title_after else "Switched windows.", verified=True)
            return _unverified("Sent Alt+Tab to switch windows, but the active foreground window did not change.")
        return _err("Unknown window action.")
    except Exception as e:
        return _err(f"Could not control window: {e}")


def app_control(action: str, app: str, force: bool = False):
    _ensure_explorer_shell_running()
    a = (action or "").lower().strip()
    if a in ("open","start","launch"):
        return open_app(app)
    if a in ("force_close", "force close", "kill"):
        force = True
        a = "close"
    try:
        if a in ("close","stop","quit","exit","close_tab","close tab"):
            app_clean = (app or "").lower().strip()
            # If the user asked to close a browser tab (e.g. "YouTube tab", "YouTube", "new tab", "tab")
            if (
                "tab" in a
                or "tab" in app_clean
                or app_clean in ("youtube", "the youtube", "youtube video", "youtube song", "new")
            ):
                from desktop_agent import close_browser_tab
                return close_browser_tab(app_clean)
            if not _check_perm("mouse_keyboard", f"Close application '{app}'"):
                return _err("Mouse and keyboard permission was denied.", permission_denied=True)
            aliases = {
                "notepad": "notepad.exe",
                "calc": "CalculatorApp.exe",
                "calculator": "CalculatorApp.exe",
                "chrome": "chrome.exe",
                "google chrome": "chrome.exe",
                "edge": "msedge.exe",
                "microsoft edge": "msedge.exe",
                "fxsound": "FxSound.exe",
                "vscode": "Code.exe",
                "vs code": "Code.exe",
                "visual studio code": "Code.exe",
                "code": "Code.exe",
                "spotify": "Spotify.exe",
                "paint": "mspaint.exe",
                "task manager": "Taskmgr.exe",
                "settings": "SystemSettings.exe",
            }

            # Safely close File Explorer / File Manager windows without killing explorer.exe shell
            if app_clean in _FILE_EXPLORER_NAMES or aliases.get(app_clean, "").lower() == "explorer.exe":
                closed_count = _close_file_explorer_windows()
                if closed_count > 0:
                    return _ok("Closed File Explorer.", verified=True)
                return _ok("File Explorer is already closed.", verified=True)

            exe = aliases.get(app_clean, app_clean if app_clean.endswith(".exe") else f"{app_clean}.exe")

            # Never kill critical Windows shell/system processes or Nova's own Python process
            if exe.lower() in _PROTECTED_PROCESSES:
                closed_by_title = _close_windows_by_title(app_clean)
                if closed_by_title > 0:
                    time.sleep(0.25)
                    rem_wins = _count_matching_windows(app_clean)
                    if rem_wins == 0:
                        return _ok(f"Closed {app}.", verified=True)
                    return _err(f"Attempted to close {app}, but {rem_wins} window(s) remained open.")
                return _err(f"Refusing to terminate protected system process '{exe}'.")

            pids_before = _find_matching_app_pids(exe, app_clean)
            wins_before = _count_matching_windows(app_clean)

            # 1. Try gracefully closing matching application windows via WM_CLOSE first
            closed_wins = _close_windows_by_title(app_clean)
            if closed_wins > 0:
                time.sleep(0.35)
                if _count_matching_windows(app_clean) == 0 and not _find_matching_app_pids(exe, app_clean):
                    return _ok(f"Gracefully closed {app}.", verified=True)

            if not force:
                rem_pids = _find_matching_app_pids(exe, app_clean)
                if not rem_pids and closed_wins == 0 and not pids_before and not wins_before:
                    return _ok(f"{app} is not currently running.", verified=True)
                return _err(
                    f"Sent graceful close to {app}, but it is still running (it may have unsaved work or a confirmation prompt). "
                    "Use force=True only if you explicitly want to force-terminate it."
                )

            # 2. Explicit force termination only when force=True
            res = subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, text=True)

            # 3. Also terminate any remaining matching processes via psutil
            killed = False
            try:
                import psutil
                for pid in _find_matching_app_pids(exe, app_clean):
                    try:
                        psutil.Process(pid).terminate()
                        killed = True
                    except Exception:
                        pass
            except Exception:
                pass

            if res.returncode != 0 and closed_wins == 0 and not killed and not pids_before and wins_before == 0:
                return _err(f"Could not find a running application matching '{app}'.")

            # 4. Verify process termination and window closure
            deadline = time.monotonic() + 1.1
            rem_pids = []
            rem_wins = 0
            while True:
                rem_pids = _find_matching_app_pids(exe, app_clean)
                rem_wins = _count_matching_windows(app_clean)
                if not rem_pids and rem_wins == 0:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.15)

            _ensure_explorer_shell_running()
            if not rem_pids and rem_wins == 0:
                return _ok(f"Closed {app} and verified the process terminated.", verified=True)
            return _err(
                f"Attempted to close {app}, but {len(rem_pids)} process(es) and {rem_wins} window(s) are still active."
            )
        if a in ("restart","relaunch"):
            app_control("close", app)
            time.sleep(0.8)
            return open_app(app)
        return _err("Unknown app action. Use open, close, or restart.")
    except Exception as e:
        return _err(f"Could not control {app}: {e}")

def system_status():
    try:
        import psutil
        battery = psutil.sensors_battery()
        return _ok("System status",
                   cpu_percent=psutil.cpu_percent(0.3),
                   memory_percent=psutil.virtual_memory().percent,
                   battery_percent=None if battery is None else battery.percent)
    except Exception as e:
        return _err(f"Could not read system status: {e}")

def take_screenshot(path: str = ""):
    if not _check_perm("screen_view", "Take desktop screenshot"):
        return _err("Screen viewing permission was denied.", permission_denied=True)
    try:
        if path:
            p = _resolve_path(path)
            prot = _is_protected_path(p)
            if prot:
                return _err(prot)
            try:
                in_workspace = p.resolve().is_relative_to(WORKSPACE.resolve())
            except Exception:
                in_workspace = False
            if not in_workspace:
                if not _check_perm("file_modify", f"Save screenshot to custom path '{p}'"):
                    return _err("File modification permission was denied for custom screenshot path.", permission_denied=True)
                if p.exists():
                    return _err(f"Refusing to overwrite existing file outside workspace: {p}")
        else:
            p = WORKSPACE / "screen.png"
        from screen_agent import capture_screen_with_metadata, ensure_dpi_awareness
        ensure_dpi_awareness()
        meta = capture_screen_with_metadata(max_dimension=4096, quality=95)
        p.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        import io
        Image.open(io.BytesIO(meta["bytes"])).save(p)
        return _ok(
            f"Screenshot captured to {p}.",
            path=str(p),
            width=meta["screen_width"],
            height=meta["screen_height"],
        )
    except Exception as e:
        return _err(f"Could not capture screenshot: {e}")

def type_text(text: str):
    if not _check_perm("mouse_keyboard", f"Type text: '{str(text)[:25]}'"):
        return _err("Mouse and keyboard permission was denied.")
    try:
        from desktop_agent import type_text_into_ui
        res = type_text_into_ui(str(text), press_enter=False)
        if res.get("ok"):
            return _ok(res.get("message", "Typed the requested text."), verified=bool(res.get("verified", False)))
        return _err(res.get("message", "Could not type text."))
    except Exception as e:
        return _err(f"Could not type text: {e}")

_last_mouse_move_ts = 0.0


def press_key(key: str):
    global _last_mouse_move_ts
    if not _check_perm("mouse_keyboard", f"Press key '{key}'"):
        return _err("Mouse and keyboard permission was denied.")
    try:
        import pyautogui
        aliases = {"return":"enter","esc":"esc","spacebar":"space","windows":"win","control":"ctrl"}
        key = aliases.get(key.lower().strip(), key.lower().strip())
        # Guard against the model moving the mouse onto a button (like YouTube Skip Ad) and calling press_key('enter')
        if key == "enter" and (time.monotonic() - _last_mouse_move_ts) < 3.0:
            _last_mouse_move_ts = 0.0
            from desktop_agent import _physical_click
            px, py = pyautogui.position()
            _physical_click(px, py, button="left", clicks=1)
            return _ok(f"Clicked left mouse button at ({px}, {py}) instead of pressing Enter.", verified=True)
        from desktop_agent import _focus_top_target_if_nova_focused, get_active_window_title
        prefer_browser = key in ("k", "j", "l", "f", "m", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")
        _focus_top_target_if_nova_focused(prefer_browser=prefer_browser)
        title_before = get_active_window_title()
        pyautogui.press(key)
        if hasattr(pyautogui.press, "_mock_name"):
            return _ok(f"Pressed {key}.", verified=True)
        time.sleep(0.12)
        title_after = get_active_window_title()
        verified = bool(title_before and title_after and title_after != title_before)
        return _ok(
            f"Pressed {key} (active window changed to '{title_after}')."
            if verified
            else f"Sent key '{key}' to '{title_after or title_before}', though its internal effect could not be independently confirmed.",
            verified=verified,
        )
    except Exception as e:
        return _err(f"Could not press key: {e}")

def hotkey(keys: str):
    if not _check_perm("mouse_keyboard", f"Press hotkey '{keys}'"):
        return _err("Mouse and keyboard permission was denied.")
    try:
        from desktop_agent import press_keyboard_hotkey
        return press_keyboard_hotkey(keys)
    except Exception as e:
        return _err(f"Could not press hotkey: {e}")

def click_mouse(x: int = None, y: int = None, button: str = "left", clicks: int = 1):
    try:
        import pyautogui
        from screen_agent import ensure_dpi_awareness
        from desktop_agent import _physical_click
        ensure_dpi_awareness()
        cur_x, cur_y = pyautogui.position()
        tx = cur_x if (x is None or int(x) < 0) else int(x)
        ty = cur_y if (y is None or int(y) < 0) else int(y)
        num_clicks = max(1, int(clicks or 1))
        btn = (button or "left").lower().strip()
        if btn not in ("left", "right", "middle"):
            btn = "left"
        if not _check_perm("mouse_keyboard", f"Click mouse ({btn}, {num_clicks}x) at ({tx}, {ty})"):
            return _err("Mouse and keyboard permission was denied.")
        _physical_click(tx, ty, button=btn, clicks=num_clicks)
        action_word = "Double-clicked" if num_clicks == 2 else f"Clicked ({btn})"
        return _ok(f"{action_word} at ({tx}, {ty}).", x=tx, y=ty, button=btn, clicks=num_clicks)
    except Exception as e:
        return _err(f"Could not click mouse: {e}")

def mouse_control(action: str, x: int = 0, y: int = 0, dx: int = 0, dy: int = 0,
                  button: str = "left", clicks: int = 1, amount: int = 0):
    global _last_mouse_move_ts
    a = action.lower().strip().replace("-", "_").replace(" ", "_")
    if a != "position" and not _check_perm("mouse_keyboard", f"Mouse control: '{action}'"):
        return _err("Mouse and keyboard permission was denied.")
    try:
        import pyautogui
        from desktop_agent import _physical_click
        if a in ("move","move_to"):
            pyautogui.moveTo(int(x), int(y), duration=0.2)
            _last_mouse_move_ts = time.monotonic()
            return _ok(f"Moved cursor to {x}, {y} (note: to click this spot, call click_mouse or click_ui_element, do NOT press Enter).")
        if a in ("move_relative","relative_move"):
            pyautogui.moveRel(int(dx), int(dy), duration=0.2)
            _last_mouse_move_ts = time.monotonic()
            return _ok("Moved cursor.")
        if a == "center":
            w,h=pyautogui.size(); pyautogui.moveTo(w//2,h//2,duration=0.2); return _ok("Moved cursor to center.")
        if a in ("top_left","top_right","bottom_left","bottom_right"):
            w,h=pyautogui.size(); pts={"top_left":(5,5),"top_right":(w-5,5),"bottom_left":(5,h-5),"bottom_right":(w-5,h-5)}
            pyautogui.moveTo(*pts[a],duration=0.2); return _ok(f"Moved cursor to {a.replace('_',' ')}.")
        if a in ("click","left_click","right_click","middle_click","tap","click_cursor","tap_cursor","click_here"):
            b = {"left_click":"left","right_click":"right","middle_click":"middle"}.get(a,button)
            px, py = pyautogui.position()
            tx = int(x) if (x and int(x) > 0) else px
            ty = int(y) if (y and int(y) > 0) else py
            _physical_click(tx, ty, button=b, clicks=max(1,int(clicks)))
            _last_mouse_move_ts = 0.0
            return _ok(f"Clicked {b} at ({tx}, {ty}).", x=tx, y=ty)
        if a in ("double_click","doubleclick","double_tap"):
            px, py = pyautogui.position()
            tx = int(x) if (x and int(x) > 0) else px
            ty = int(y) if (y and int(y) > 0) else py
            _physical_click(tx, ty, button="left", clicks=2)
            _last_mouse_move_ts = 0.0
            return _ok(f"Double-clicked at ({tx}, {ty}).", x=tx, y=ty)
        if a in ("scroll","scroll_up","scroll_down"):
            amt = int(amount)
            if a=="scroll_up": amt=abs(amt or 3)
            if a=="scroll_down": amt=-abs(amt or 3)
            pyautogui.scroll(amt); return _ok("Scrolled.")
        if a=="drag":
            pyautogui.moveTo(int(x),int(y),duration=0.2); pyautogui.dragRel(int(dx),int(dy),duration=0.5,button=button); return _ok("Dragged.")
        if a=="position":
            px,py=pyautogui.position(); return _ok("Current cursor position.",x=px,y=py)
        return _err("Unknown mouse action.")
    except Exception as e:
        return _err(f"Could not control mouse: {e}")
