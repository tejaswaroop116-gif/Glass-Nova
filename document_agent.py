"""PDF and document understanding agent for Glass Nova.

Extracts text across all pages, passes native PDF bytes for multimodal/scanned OCR,
explains diagrams/tables, lists all modules/chapters, and optionally writes results to Notepad.
Checks document_access permissions before inspecting user files.
"""
import os, io, re, glob, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from google import genai
from google.genai import types

from permissions import permissions
from screen_agent import get_client, get_vision_models, remember_working_vision_model

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.5-flash")


def _get_all_open_window_titles() -> List[str]:
    """Return titles of all visible top-level windows (including background PDF viewers behind Notepad)."""
    titles = []
    try:
        from screen_agent import get_active_window_title
        fg = get_active_window_title()
        if fg and fg != "Desktop":
            titles.append(fg)
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                if user32.GetWindowTextW(hwnd, buf, 256) > 0:
                    t = buf.value.strip()
                    if t and t not in titles:
                        titles.append(t)
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return titles


def find_target_document(hint_path: Optional[str] = None, query: Optional[str] = None) -> Optional[Path]:
    """Finds target document by path, fuzzy filename match, last referenced file, open viewer window titles, open Explorer folders, or recent PDFs."""
    import tools
    if hint_path:
        if os.path.exists(hint_path) and os.path.isfile(hint_path):
            p = Path(hint_path)
            tools._last_referenced_file = p
            return p
        resolved = tools._find_user_file(hint_path, default_exts=(".pdf", ".docx", ".txt", ".md"))
        if resolved and resolved.is_file():
            return resolved

    if tools._last_referenced_file and tools._last_referenced_file.is_file():
        return tools._last_referenced_file

    # Check active and background window titles (e.g., WPS Office / Edge / Chrome / Acrobat behind Notepad)
    for w_title in _get_all_open_window_titles():
        m = re.search(r"([\w\-\.\s]+\.pdf)", w_title, re.IGNORECASE)
        if m:
            pdf_name = m.group(1).strip()
            resolved = tools._find_user_file(pdf_name, default_exts=(".pdf",))
            if resolved and resolved.is_file():
                return resolved
        if any(viewer in w_title.lower() for viewer in ("wps", "acrobat", "pdf", "foxit")):
            prefix = w_title.split(" - ")[0].strip()
            if prefix:
                resolved = tools._find_user_file(prefix, default_exts=(".pdf",))
                if resolved and resolved.is_file():
                    return resolved

    # Check if the user's query itself names a specific PDF
    if query:
        q_lower = query.lower()
        if ".pdf" in q_lower or "machine learning" in q_lower:
            resolved = tools._find_user_file(query, default_exts=(".pdf",))
            if resolved and resolved.is_file():
                return resolved

    # Prioritize PDFs inside currently open File Explorer folders or the last opened folder
    priority_folders = []
    for d in tools._get_open_explorer_folders():
        if d.is_dir() and d not in priority_folders:
            priority_folders.append(d)
    if tools._last_opened_folder and tools._last_opened_folder.is_dir() and tools._last_opened_folder not in priority_folders:
        priority_folders.append(tools._last_opened_folder)

    for folder in priority_folders:
        try:
            pdfs = list(folder.glob("*.pdf"))
            if pdfs:
                pdfs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                tools._last_referenced_file = pdfs[0]
                return pdfs[0]
        except Exception:
            continue

    # Check common folders for the most recently modified PDF
    candidates = []
    for folder in [
        Path.home() / "Documents",
        Path.home() / "Downloads",
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Documents",
        Path.home() / "OneDrive" / "Desktop",
    ]:
        if folder.exists():
            candidates.extend(folder.glob("*.pdf"))

    if candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        tools._last_referenced_file = candidates[0]
        return candidates[0]

    return None


def _extract_docx_text(file_path: Path) -> str:
    """Extract text paragraphs from a .docx file without external dependencies."""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        paragraphs = []
        for p in root.iter():
            if p.tag.endswith("}p"):
                texts = [t.text for t in p.iter() if t.tag.endswith("}t") and t.text]
                if texts:
                    paragraphs.append("".join(texts))
        return "\n".join(paragraphs)
    except Exception:
        return ""


def extract_pdf_content(file_path: Path, max_pages: int = 300) -> Dict[str, Any]:
    """Extracts text across the whole PDF/document and builds an index of all module/unit/chapter headings."""
    suffix = file_path.suffix.lower()
    if suffix in (".txt", ".md", ".csv", ".py", ".json", ".log"):
        txt = file_path.read_text(encoding="utf-8", errors="ignore")
        return {
            "text": txt,
            "headings": "",
            "total_pages": 1,
            "pages_read": 1,
            "is_scanned": False,
            "embedded_images": []
        }
    if suffix == ".docx":
        txt = _extract_docx_text(file_path)
        return {
            "text": txt,
            "headings": "",
            "total_pages": 1,
            "pages_read": 1,
            "is_scanned": False,
            "embedded_images": []
        }

    from pypdf import PdfReader
    reader = PdfReader(str(file_path))
    total_pages = len(reader.pages)
    pages_to_read = min(total_pages, max_pages)

    extracted_pages = []
    headings_index = []
    scanned_count = 0
    heading_pat = re.compile(
        r"\b(module|unit|chapter|part|section|syllabus|lecture|topic)\s*[\-\:\#\.]?\s*(\d+|[ivx]+)\b",
        re.IGNORECASE
    )

    for i in range(pages_to_read):
        page = reader.pages[i]
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        stripped = text.strip()
        if len(stripped) < 40:
            scanned_count += 1
        else:
            raw_lines = [" ".join(ln.split()) for ln in stripped.splitlines() if ln.strip()]
            for idx, clean_line in enumerate(raw_lines):
                if len(clean_line) <= 180 and heading_pat.search(clean_line):
                    # Also include the following line if the heading line is short (e.g., "MODULE - 1" followed by title on next line)
                    combined = clean_line
                    if len(clean_line) <= 35 and (idx + 1) < len(raw_lines):
                        combined = f"{clean_line} — {raw_lines[idx + 1][:140]}"
                    entry = f"[Page {i + 1}] {combined}"
                    if entry not in headings_index:
                        headings_index.append(entry)
        extracted_pages.append(f"--- PAGE {i + 1} ---\n{stripped}")

    full_text = "\n\n".join(extracted_pages)
    return {
        "text": full_text,
        "headings": "\n".join(headings_index[:150]),
        "total_pages": total_pages,
        "pages_read": pages_to_read,
        "is_scanned": scanned_count > (pages_to_read // 2),
        "embedded_images": []
    }


def _should_auto_write_to_notepad(query: str, write_to_notepad: bool = False) -> bool:
    if write_to_notepad:
        return True
    q = (query or "").lower()
    return any(
        phrase in q
        for phrase in (
            "on notepad", "in notepad", "to notepad", "into notepad",
            "write on notepad", "write in notepad", "type on notepad", "type in notepad",
            "put on notepad", "put in notepad",
        )
    )


def analyze_document(path: Optional[str] = None,
                     query: str = "Explain this PDF simply and summarize its key concepts.",
                     page_number: Optional[int] = None,
                     write_to_notepad: bool = False) -> Dict[str, Any]:
    """
    Main document understanding entrypoint.
    Inspects, reads the entire PDF (native PDF bytes + extracted text across all pages),
    generates explanations via Gemini, and optionally writes the output directly into Notepad.
    """
    target = find_target_document(path, query=query)
    if not target or not target.exists():
        # Fall back to visual screen understanding if an online or browser PDF is open on screen
        try:
            from screen_agent import analyze_screen
            return analyze_screen(query=f"Explain the document/PDF shown on the screen: {query}")
        except Exception:
            return {
                "ok": False,
                "message": "No document could be found on disk or on screen. Please specify the PDF path."
            }

    action_desc = f"Read and explain document '{target.name}'"
    if not permissions.request_permission("document_access", action_desc, {"path": str(target)}):
        return {
            "ok": False,
            "message": f"Permission to access document '{target.name}' was denied.",
            "permission_denied": True
        }

    try:
        content_info = extract_pdf_content(target)
        client = get_client()

        headings_block = ""
        if content_info.get("headings"):
            headings_block = f"Detected Module / Unit / Chapter Headings Across All Pages:\n{content_info['headings']}\n\n"

        prompt = (
            f"Document Title: {target.name}\n"
            f"Total Pages: {content_info['total_pages']} (Read all {content_info['pages_read']} pages)\n"
            f"User Request: {query}\n\n"
            f"{headings_block}"
            f"Full Extracted Text Across Pages:\n{content_info['text'][:140000]}"
        )

        text_part = types.Part.from_text(text=prompt)
        parts_with_pdf = None
        if target.suffix.lower() == ".pdf":
            try:
                fsize = target.stat().st_size
                if 100 < fsize <= 20 * 1024 * 1024:
                    pdf_bytes = target.read_bytes()
                    if pdf_bytes.startswith(b"%PDF-"):
                        parts_with_pdf = [
                            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                            text_part
                        ]
            except Exception:
                parts_with_pdf = None

        system_instruction = (
            "You are Nova's academic and document analysis specialist. "
            "You have full access to the ENTIRE PDF document across all pages (both the native PDF pages and the extracted text). "
            "Never say you can only see Module 1 or only the visible part of the document — examine all pages from start to finish. "
            "When asked to list all modules, units, chapters, or topics, scan the entire document and headings index to list ALL of them (e.g. Modules 1 through 5) with their complete titles. "
            "If asked to summarize, provide clean bullet points. "
            "If asked for exam/revision questions, provide high-yield questions with brief answers. "
            "Explain technical diagrams, formulas, or concepts step by step."
        )

        models = get_vision_models()
        response = None
        last_err = None
        for m_name in models:
            # Try with native PDF bytes + extracted text first (handles scanned/image pages & tables across the whole PDF)
            if parts_with_pdf is not None:
                try:
                    response = client.models.generate_content(
                        model=m_name,
                        contents=parts_with_pdf,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.2,
                            max_output_tokens=2048
                        )
                    )
                    remember_working_vision_model(m_name)
                    break
                except Exception as e:
                    last_err = e

            # Fallback to extracted text + headings index
            try:
                response = client.models.generate_content(
                    model=m_name,
                    contents=[text_part],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.2,
                        max_output_tokens=2048
                    )
                )
                remember_working_vision_model(m_name)
                break
            except Exception as e:
                last_err = e
                continue

        if response is None:
            raise last_err or RuntimeError("Document analysis failed across all models.")

        answer = (response.text or "").strip()
        if not answer:
            answer = f"I extracted the content from {target.name}, but could not generate a summary."

        written_to_notepad = False
        if _should_auto_write_to_notepad(query, write_to_notepad):
            try:
                from desktop_agent import write_in_notepad
                np_res = write_in_notepad(answer, open_if_closed=True)
                written_to_notepad = bool(np_res.get("ok"))
            except Exception:
                written_to_notepad = False

        return {
            "ok": True,
            "message": answer,
            "file_name": target.name,
            "total_pages": content_info["total_pages"],
            "written_to_notepad": written_to_notepad,
            "query": query
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Failed to analyze document {target.name}: {e}"
        }
