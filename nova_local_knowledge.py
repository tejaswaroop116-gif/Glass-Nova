"""nova_local_knowledge.py - Opt-in local folder knowledge index, local file search, code analysis, and AST-restricted safe Python sandbox.

Privacy guarantee:
- NEVER indexes any folder automatically. The user must explicitly add folders.
- All indexes stay locally in %APPDATA%\\PC_Complete_Assistant\\local_knowledge_index.json.
"""
from __future__ import annotations

import ast
import io
import json
import math
import os
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from permissions import redact_sensitive_text
from tools_files import _resolve_path
from tools_state import _check_perm, _err, _ok

_STATE_DIR = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "PC_Complete_Assistant"
_KNOWLEDGE_FILE = _STATE_DIR / "local_knowledge_index.json"
_SUPPORTED_EXTS = {".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml", ".yml", ".pdf", ".docx"}
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB per file cap for 16 GB RAM safety
_MAX_FILES_PER_FOLDER = 250
_MAX_TOTAL_CHUNKS = 2000

_SKIP_DIR_NAMES = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".tox", "dist", "build", "appdata", "$recycle.bin",
    "system volume information",
}
_SECRET_FILE_PATTERNS = re.compile(
    r"(?:^\.env(?:\..+)?$|secret|credential|password|id_rsa|id_ed25519|\.pem$|\.key$|\.pfx$)",
    re.IGNORECASE,
)


def _is_forbidden_index_root(p: Path) -> Optional[str]:
    """Prevent indexing entire OS drives, Windows system directories, or the top-level user profile root."""
    try:
        resolved = p.resolve()
    except Exception:
        return "Invalid folder path."
    # Block drive roots like C:\
    if resolved.parent == resolved:
        return f"Refusing to index entire drive root '{resolved}'. Please choose a specific project or documents subfolder."
    low = str(resolved).lower().rstrip("\\/")
    home_low = str(Path.home().resolve()).lower().rstrip("\\/")
    if low == home_low:
        return f"Refusing to index the entire user profile root '{resolved}'. Please choose a specific subfolder (e.g. Documents\\MyProject)."
    forbidden_prefixes = (
        "c:\\windows",
        "c:\\program files",
        "c:\\program files (x86)",
        "c:\\programdata",
        str(_STATE_DIR.parent.resolve()).lower(),
    )
    for fp in forbidden_prefixes:
        if low == fp or low.startswith(fp + "\\"):
            return f"Refusing to index protected system/AppData directory '{resolved}'."
    return None


def _tokenize(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9_]{2,}", (text or "").lower()) if len(w) <= 40]


def extract_text_from_file(path: Path, max_chars: int = 24000) -> str:
    """Extract plain text locally from .txt, .md, .py, .json, .csv, .pdf, or .docx."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        parts: List[str] = []
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            for page in reader.pages[:40]:
                t = (page.extract_text() or "").strip()
                if t:
                    parts.append(t)
                if sum(len(x) for x in parts) >= max_chars:
                    break
            return "\n".join(parts)[:max_chars]
        except Exception:
            pass
        try:
            import fitz
            doc = fitz.open(str(path))
            for i in range(min(len(doc), 40)):
                t = (doc[i].get_text() or "").strip()
                if t:
                    parts.append(t)
            doc.close()
            return "\n".join(parts)[:max_chars]
        except Exception:
            return ""

    if ext == ".docx":
        try:
            with zipfile.ZipFile(path) as zf:
                xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            texts = [el.text for el in root.iter() if el.tag.endswith("}t") and el.text]
            return " ".join(texts)[:max_chars]
        except Exception:
            return ""

    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


class LocalKnowledgeStore:
    """User-controlled local document indexer and retriever (pure Python, zero external vector DB)."""

    def __init__(self, index_file: Optional[Path] = None):
        self.index_file = Path(index_file) if index_file else _KNOWLEDGE_FILE
        self._lock = threading.RLock()
        self.state = self._load()

    def _empty(self) -> Dict[str, Any]:
        return {"version": 1, "folders": [], "chunks": []}

    def _load(self) -> Dict[str, Any]:
        if not self.index_file.is_file():
            return self._empty()
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("folders"), list) and isinstance(data.get("chunks"), list):
                return data
        except Exception:
            pass
        return self._empty()

    def _save(self) -> None:
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.index_file.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_file, self.index_file)

    def list_folders(self) -> List[str]:
        with self._lock:
            return list(self.state["folders"])

    def add_folder(self, folder_path: str) -> Dict[str, Any]:
        p = _resolve_path(folder_path).resolve()
        if not p.is_dir():
            return _err(f"Folder does not exist: {p}")
        forbidden_reason = _is_forbidden_index_root(p)
        if forbidden_reason:
            return _err(forbidden_reason)
        with self._lock:
            p_str = str(p)
            if p_str not in self.state["folders"]:
                self.state["folders"].append(p_str)
            count = self._index_single_folder_locked(p)
            if len(self.state["chunks"]) > _MAX_TOTAL_CHUNKS:
                self.state["chunks"] = self.state["chunks"][-_MAX_TOTAL_CHUNKS:]
            self._save()
        return _ok(f"Indexed {count} text chunk(s) from {p}.", folder=p_str, chunks_indexed=count)

    def remove_folder(self, folder_path: str) -> Dict[str, Any]:
        p = _resolve_path(folder_path).resolve()
        p_str = str(p)
        with self._lock:
            if p_str not in self.state["folders"]:
                return _err(f"Folder is not in the local knowledge index: {p_str}")
            self.state["folders"] = [f for f in self.state["folders"] if f != p_str]
            self.state["chunks"] = [c for c in self.state["chunks"] if c.get("folder") != p_str]
            self._save()
        return _ok(f"Removed {p_str} from local knowledge index.")

    def _index_single_folder_locked(self, folder: Path) -> int:
        f_str = str(folder)
        # Remove stale chunks for this folder before re-indexing
        self.state["chunks"] = [c for c in self.state["chunks"] if c.get("folder") != f_str]
        added = 0
        files_seen = 0
        for root_dir, dirs, files in os.walk(folder):
            # Prune hidden, virtualenv, node_modules, and build directories in-place
            dirs[:] = sorted(
                d for d in dirs
                if not d.startswith(".") and d.lower() not in _SKIP_DIR_NAMES
            )
            for fname in sorted(files):
                if files_seen >= _MAX_FILES_PER_FOLDER:
                    break
                if fname.startswith(".") or _SECRET_FILE_PATTERNS.search(fname):
                    continue
                p = Path(root_dir) / fname
                if p.suffix.lower() not in _SUPPORTED_EXTS:
                    continue
                try:
                    if not p.is_file() or p.stat().st_size > _MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                files_seen += 1
                text = redact_sensitive_text(extract_text_from_file(p))
                if not text.strip():
                    continue
                # Split into ~900-char overlapping chunks
                step = 800
                for start in range(0, min(len(text), 12000), step):
                    chunk_txt = text[start: start + 950].strip()
                    if len(chunk_txt) < 20:
                        continue
                    self.state["chunks"].append(
                        {
                            "folder": f_str,
                            "path": str(p),
                            "name": p.name,
                            "text": chunk_txt,
                        }
                    )
                    added += 1
            if files_seen >= _MAX_FILES_PER_FOLDER:
                break
        return added

    def reindex(self) -> Dict[str, Any]:
        with self._lock:
            total = 0
            valid_folders = []
            for f_str in list(self.state["folders"]):
                p = Path(f_str)
                if p.is_dir():
                    valid_folders.append(f_str)
                    total += self._index_single_folder_locked(p)
            self.state["folders"] = valid_folders
            self._save()
        return _ok(f"Re-indexed {len(valid_folders)} folder(s) ({total} chunks).", folders=valid_folders, chunks=total)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        with self._lock:
            chunks = list(self.state["chunks"])
        if not chunks:
            return []

        n_docs = len(chunks)
        df: Dict[str, int] = {}
        doc_tokens_list: List[List[str]] = []
        for ch in chunks:
            toks = _tokenize(ch.get("name", "") + " " + ch.get("text", ""))
            doc_tokens_list.append(toks)
            for t in set(toks):
                if t in q_tokens:
                    df[t] = df.get(t, 0) + 1

        scored = []
        for ch, toks in zip(chunks, doc_tokens_list):
            if not toks:
                continue
            score = 0.0
            for qt in q_tokens:
                tf = toks.count(qt)
                if tf > 0:
                    idf = math.log((n_docs + 1.0) / (df.get(qt, 0) + 1.0)) + 1.0
                    score += (1.0 + math.log(tf)) * idf
            if score > 0:
                scored.append((score, ch))
        scored.sort(key=lambda item: -item[0])
        return [
            {
                "score": round(s, 3),
                "path": ch["path"],
                "name": ch["name"],
                "excerpt": ch["text"][:450],
            }
            for s, ch in scored[: max(1, int(top_k))]
        ]

    def clear(self) -> Dict[str, Any]:
        """Clear all indexed folders and chunks from memory and disk."""
        with self._lock:
            self.state = self._empty()
            self._save()
        return _ok("Cleared all indexed local knowledge folders and passages.")

    def build_context_snippet(self, query: str, top_k: int = 3) -> str:
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        return "\n---\n".join(f"[{h['name']}] {h['excerpt']}" for h in hits)


knowledge_store = LocalKnowledgeStore()


def local_knowledge_control(action: str = "list", path: str = "", query: str = "") -> Dict[str, Any]:
    """Manage user-selected local knowledge folders: add_folder, remove_folder, list, reindex, search."""
    act = (action or "list").strip().lower().replace("-", "_").replace(" ", "_")
    if act in ("list", "folders", "status"):
        folders = knowledge_store.list_folders()
        if not folders:
            return _ok("No folders are indexed yet. Choose a folder with 'index folder <path>' to enable local knowledge search.", folders=[])
        return _ok(f"Indexed {len(folders)} local folder(s): " + ", ".join(folders), folders=folders)
    if act in ("add", "add_folder", "index", "index_folder"):
        if not path:
            return _err("Please specify a folder path to index.")
        if not _check_perm("document_access", f"Index local folder for knowledge search: '{path}'"):
            return _err("Document access permission was denied.", permission_denied=True)
        return knowledge_store.add_folder(path)
    if act in ("remove", "remove_folder", "unindex"):
        if not path:
            return _err("Please specify a folder path to remove.")
        return knowledge_store.remove_folder(path)
    if act in ("reindex", "refresh"):
        if not _check_perm("document_access", "Re-index user-selected local knowledge folders"):
            return _err("Document access permission was denied.", permission_denied=True)
        return knowledge_store.reindex()
    if act in ("clear", "clear_all", "reset"):
        return knowledge_store.clear()
    if act in ("search", "query", "find"):
        if not query:
            return _err("Please provide a search query.")
        if not _check_perm("document_access", f"Search indexed local knowledge for '{query}'"):
            return _err("Document access permission was denied.", permission_denied=True)
        hits = knowledge_store.search(query, top_k=5)
        if not hits:
            return _ok(f"No matching passages found in indexed local folders for '{query}'.", results=[])
        summary = "; ".join(f"{h['name']} (score {h['score']})" for h in hits)
        return _ok(f"Found {len(hits)} local knowledge match(es): {summary}", results=hits)
    return _err(f"Unknown local_knowledge_control action '{action}'.")


def search_local_files(query: str, folder: str = "documents", max_results: int = 20) -> Dict[str, Any]:
    """Search for local files by filename substring inside a user folder (Documents, Downloads, Desktop, Workspace, or custom path)."""
    q = (query or "").strip().lower()
    if not q:
        return _err("Please provide a filename or keyword to search for.")
    root = _resolve_path(folder or "documents")
    if not root.is_dir():
        return _err(f"Search folder does not exist: {root}")
    if not _check_perm("document_access", f"Search local files for '{query}' in '{root}'"):
        return _err("Document access permission was denied.", permission_denied=True)
    matches: List[Dict[str, str]] = []
    scanned = 0
    try:
        for root_dir, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in _SKIP_DIR_NAMES]
            for name in dirs + files:
                scanned += 1
                if scanned > 6000:
                    break
                if q in name.lower():
                    full_p = Path(root_dir) / name
                    matches.append({"name": name, "path": str(full_p), "type": "folder" if full_p.is_dir() else "file"})
                    if len(matches) >= max(1, min(50, int(max_results))):
                        break
            if scanned > 6000 or len(matches) >= max(1, min(50, int(max_results))):
                break
        if not matches:
            return _ok(f"No files matching '{query}' were found in {root}.", results=[])
        preview = ", ".join(m["name"] for m in matches[:10])
        return _ok(f"Found {len(matches)} match(es) in {root}: {preview}", results=matches)
    except Exception as exc:
        return _err(f"Local file search failed: {exc}")


# ---- Controlled AST-Restricted Local Python Execution ----
class _SandboxTimeout(BaseException):
    """Inherits from BaseException so user 'except Exception:' blocks cannot swallow sandbox timeouts."""


def _safe_range(*args):
    r = range(*args)
    if len(r) > 100_000:
        raise ValueError(f"range() size {len(r)} exceeds safe sandbox limit (100,000).")
    return r


def _safe_pow(a, b, mod=None):
    if isinstance(b, (int, float)) and abs(b) > 1000 and not (isinstance(a, (int, float)) and abs(a) <= 1):
        raise ValueError(f"Exponent {b} exceeds safe sandbox limit (1000).")
    return pow(a, b) if mod is None else pow(a, b, mod)


def _safe_mult(a, b):
    if isinstance(a, (str, bytes, list, tuple)) and isinstance(b, int):
        if len(a) * max(0, b) > 200_000:
            raise ValueError("Sequence repetition exceeds safe sandbox memory limit (200,000 items).")
    elif isinstance(b, (str, bytes, list, tuple)) and isinstance(a, int):
        if len(b) * max(0, a) > 200_000:
            raise ValueError("Sequence repetition exceeds safe sandbox memory limit (200,000 items).")
    return a * b


def _safe_lshift(a, b):
    if isinstance(b, int) and b > 1000:
        raise ValueError(f"Bit shift {b} exceeds safe sandbox limit (1000).")
    return a << b


class _SafeMathTransformer(ast.NodeTransformer):
    """Rewrites Pow (**), Mult (*), and LShift (<<) into memory-safe helper calls."""

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, ast.Pow):
            return ast.copy_location(
                ast.Call(func=ast.Name(id="_safe_pow", ctx=ast.Load()), args=[node.left, node.right], keywords=[]),
                node,
            )
        if isinstance(node.op, ast.Mult):
            return ast.copy_location(
                ast.Call(func=ast.Name(id="_safe_mult", ctx=ast.Load()), args=[node.left, node.right], keywords=[]),
                node,
            )
        if isinstance(node.op, ast.LShift):
            return ast.copy_location(
                ast.Call(func=ast.Name(id="_safe_lshift", ctx=ast.Load()), args=[node.left, node.right], keywords=[]),
                node,
            )
        return node


_BLOCKED_AST_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "len": len,
    "list": list, "max": max, "min": min, "pow": _safe_pow, "range": _safe_range,
    "reversed": reversed, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
    "RuntimeError": RuntimeError,
}


def _validate_safe_python_ast(code: str) -> Optional[str]:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return f"Syntax error: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, _BLOCKED_AST_NODES):
            return "Imports and global/nonlocal statements are not allowed in the safe Python sandbox."
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"Access to dunder attribute '{node.attr}' is blocked in the safe Python sandbox."
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id.startswith("_safe_")):
            return f"Access to restricted identifier '{node.id}' is blocked in the safe Python sandbox."
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                return "Bare 'except:' clauses are not allowed in the safe Python sandbox (catch specific Exception types)."
            if isinstance(node.type, ast.Name) and node.type.id == "BaseException":
                return "Catching BaseException is not allowed in the safe Python sandbox."
    return None


def run_safe_python(code: str, timeout_seconds: float = 2.0) -> Dict[str, Any]:
    """Execute small, pure-Python computation snippets in a restricted AST sandbox with a strict timeout.
    No filesystem, no network, no imports, no subprocesses, and protected against memory/CPU bombs."""
    src = str(code or "").strip()
    if not src:
        return _err("No Python code provided.")
    if len(src) > 4000:
        return _err("Code snippet is too long (max 4000 characters).")
    err = _validate_safe_python_ast(src)
    if err:
        return _err(err)

    transformed_tree = _SafeMathTransformer().visit(ast.parse(src, mode="exec"))
    ast.fix_missing_locations(transformed_tree)

    result_box: Dict[str, Any] = {}
    deadline = time.monotonic() + max(0.2, min(5.0, float(timeout_seconds)))

    def _worker():
        out_buf = io.StringIO()

        def _safe_print(*args, **kwargs):
            if out_buf.tell() > 20_000:
                raise ValueError("Stdout output exceeded safe sandbox limit (20 KB).")
            print(*args, file=out_buf, **{k: v for k, v in kwargs.items() if k in ("sep", "end")})

        def _trace_deadline(frame, event, arg):
            if time.monotonic() > deadline:
                raise _SandboxTimeout(f"Python sandbox timed out after {timeout_seconds}s.")
            return _trace_deadline

        env_builtins = dict(_SAFE_BUILTINS)
        env_builtins["print"] = _safe_print
        safe_globals = {
            "__builtins__": env_builtins,
            "math": math,
            "_safe_pow": _safe_pow,
            "_safe_mult": _safe_mult,
            "_safe_lshift": _safe_lshift,
        }
        safe_locals: Dict[str, Any] = {}
        import sys as _sys
        _sys.settrace(_trace_deadline)
        try:
            compiled = compile(transformed_tree, "<nova_safe_python>", "exec")
            exec(compiled, safe_globals, safe_locals)
            result_box["stdout"] = out_buf.getvalue().strip()[:2000]
            # Capture non-private scalar/small results
            preview_vars = {
                k: repr(v)[:200]
                for k, v in safe_locals.items()
                if not k.startswith("_") and not callable(v)
            }
            result_box["variables"] = preview_vars
            result_box["ok"] = True
        except _SandboxTimeout as exc:
            result_box["ok"] = False
            result_box["error"] = str(exc)
        except Exception as exc:
            result_box["ok"] = False
            result_box["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            _sys.settrace(None)

    t = threading.Thread(target=_worker, name="NovaSafePython", daemon=True)
    t.start()
    t.join(timeout=max(0.2, min(5.0, float(timeout_seconds))))
    if t.is_alive():
        return _err(f"Python sandbox timed out after {timeout_seconds}s.")
    if not result_box.get("ok"):
        return _err(result_box.get("error", "Safe Python execution failed."))

    stdout = result_box.get("stdout", "")
    vars_out = result_box.get("variables", {})
    summary = stdout if stdout else (json.dumps(vars_out) if vars_out else "Executed cleanly with no output.")
    return _ok(summary, stdout=stdout, variables=vars_out)
