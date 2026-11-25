"""nova_offline_ai.py - Local Ollama runtime, 4 GB VRAM resource manager, context manager, local vision, and Offline Capability Manager.

Designed specifically to keep Glass Nova fast, capable, and memory-safe on laptops such as
16 GB RAM + RTX 3050 (4 GB VRAM), using only the Python standard library (+ optional psutil).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from permissions import permissions

DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_OFFLINE_MODEL = os.getenv("NOVA_OFFLINE_MODEL", "").strip()
DEFAULT_OFFLINE_VISION_MODEL = os.getenv("NOVA_OFFLINE_VISION_MODEL", "").strip()

# Quantized models well-suited for 4 GB VRAM + 16 GB RAM laptops
RECOMMENDED_4GB_TEXT_MODELS = (
    "qwen2.5:3b",
    "llama3.2:3b",
    "phi3:mini",
    "gemma3:4b",
    "qwen2.5-coder:3b",
    "gemma2:2b",
    "llama3.2:1b",
)

RECOMMENDED_4GB_VISION_MODELS = (
    "moondream",
    "qwen2.5vl:3b",
    "llava-phi3",
    "minicpm-v",
    "gemma3:4b",
)

_VISION_MODEL_HINTS = (
    "llava", "moondream", "vision", "vl", "minicpm-v", "bakllava", "gemma3", "pixtral",
)

_STATE_DIR = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "PC_Complete_Assistant"
_CONFIG_FILE = _STATE_DIR / "offline_ai_config.json"


def _is_vision_model_name(name: str) -> bool:
    low = (name or "").lower()
    return any(h in low for h in _VISION_MODEL_HINTS)


def _estimate_model_vram_gb(model_name: str, size_bytes: int = 0) -> float:
    """Estimate approximate VRAM footprint in GB from model size_bytes or parameter tag."""
    if size_bytes and size_bytes > 0:
        # Quantized weights + small KV cache overhead (~1.2x file size)
        return round((size_bytes / (1024 ** 3)) * 1.2, 2)
    low = (model_name or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", low)
    if m:
        params_b = float(m.group(1))
        # Q4_K_M quantization is ~0.65 GB per billion parameters + KV overhead
        return round(params_b * 0.72, 2)
    return 2.2


def detect_hardware_profile() -> Dict[str, Any]:
    """Detect RAM (via psutil) and NVIDIA GPU/VRAM (via nvidia-smi) safely without freezing."""
    profile: Dict[str, Any] = {
        "ram_total_gb": None,
        "ram_available_gb": None,
        "gpu_name": None,
        "gpu_vram_total_mb": None,
        "gpu_vram_free_mb": None,
        "low_vram_profile": True,
        "warnings": [],
    }
    try:
        import psutil
        vm = psutil.virtual_memory()
        profile["ram_total_gb"] = round(vm.total / (1024 ** 3), 1)
        profile["ram_available_gb"] = round(vm.available / (1024 ** 3), 1)
        if profile["ram_available_gb"] < 3.0:
            profile["warnings"].append(
                f"Low available RAM ({profile['ram_available_gb']} GB). Prefer compact 1B-3B quantized models or close heavy apps."
            )
    except Exception:
        pass

    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            res = subprocess.run(
                [smi, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                line = res.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    profile["gpu_name"] = parts[0]
                    profile["gpu_vram_total_mb"] = int(float(parts[1]))
                    profile["gpu_vram_free_mb"] = int(float(parts[2]))
                    profile["low_vram_profile"] = profile["gpu_vram_total_mb"] <= 4600
                    if profile["gpu_vram_total_mb"] <= 4600:
                        profile["warnings"].append(
                            f"{profile['gpu_name']} ({profile['gpu_vram_total_mb']} MB VRAM) detected: using 4 GB VRAM-safe context (num_ctx=4096) and single-model residency."
                        )
        except Exception:
            pass
    return profile


class ConversationContextManager:
    """Maintains conversational continuity across Online/Offline switches with rolling compression.

    Keeps prompt size bounded so 4 GB VRAM GPUs never run out of KV-cache memory.
    """

    def __init__(self, max_turns: int = 12, max_context_chars: int = 9000):
        self.max_turns = max(4, int(max_turns))
        self.max_context_chars = max(2000, int(max_context_chars))
        self._lock = threading.RLock()
        self.rolling_summary: str = ""
        self.turns: List[Dict[str, str]] = []

    def add_turn(self, role: str, content: str) -> None:
        role_clean = "assistant" if role in ("model", "assistant") else "user"
        text = " ".join(str(content or "").strip().split())
        if not text:
            return
        trimmed = text[:2000]
        with self._lock:
            if self.turns and self.turns[-1]["role"] == role_clean and self.turns[-1]["content"] == trimmed:
                return
            self.turns.append({"role": role_clean, "content": trimmed})
            self._compress_if_needed()

    def _compress_if_needed(self) -> None:
        total_chars = sum(len(t["content"]) for t in self.turns) + len(self.rolling_summary)
        while (len(self.turns) > self.max_turns or total_chars > self.max_context_chars) and len(self.turns) > 4:
            dropped = self.turns.pop(0)
            snippet = f"{dropped['role']}: {dropped['content'][:160]}"
            self.rolling_summary = (self.rolling_summary + " | " + snippet).strip(" |")
            if len(self.rolling_summary) > 1800:
                self.rolling_summary = "..." + self.rolling_summary[-1750:]
            total_chars = sum(len(t["content"]) for t in self.turns) + len(self.rolling_summary)

    def build_messages(self, system_prompt: str, user_prompt: str, extra_context: str = "") -> List[Dict[str, str]]:
        with self._lock:
            sys_parts = [system_prompt.strip()]
            if self.rolling_summary:
                sys_parts.append(f"Compressed earlier conversation context:\n{self.rolling_summary}")
            if extra_context:
                sys_parts.append(f"Relevant local knowledge / memory:\n{extra_context[:3000]}")
            msgs: List[Dict[str, str]] = [{"role": "system", "content": "\n\n".join(sys_parts)}]
            recent = [dict(t) for t in self.turns[-self.max_turns:]]
            clean_prompt = " ".join(str(user_prompt or "").strip().split())
            if recent and recent[-1]["role"] == "user" and recent[-1]["content"] == clean_prompt[:2000]:
                recent = recent[:-1]
            msgs.extend(recent)
            msgs.append({"role": "user", "content": str(user_prompt or "").strip()})
            return msgs

    def clear(self) -> None:
        with self._lock:
            self.rolling_summary = ""
            self.turns.clear()


class OllamaClient:
    """Lightweight stdlib HTTP client for local Ollama with VRAM-safe single-model management."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, timeout: float = 45.0):
        self.base_url = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
        self.timeout = float(timeout)
        self._lock = threading.RLock()
        self.preferred_model: str = DEFAULT_OFFLINE_MODEL
        self.preferred_vision_model: str = DEFAULT_OFFLINE_VISION_MODEL
        self.cpu_only: bool = False
        self._active_loaded_model: str = ""
        self._models_cache: Optional[List[Dict[str, Any]]] = None
        self._models_cache_ts: float = 0.0
        self._load_config()

    def _load_config(self) -> None:
        try:
            if _CONFIG_FILE.is_file():
                data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if not self.preferred_model and data.get("model"):
                        self.preferred_model = str(data["model"]).strip()
                    if not self.preferred_vision_model and data.get("vision_model"):
                        self.preferred_vision_model = str(data["vision_model"]).strip()
                    if "cpu_only" in data:
                        self.cpu_only = bool(data["cpu_only"])
        except Exception:
            pass

    def save_config(self) -> None:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp_file = _CONFIG_FILE.with_suffix(".json.tmp")
            tmp_file.write_text(
                json.dumps(
                    {
                        "model": self.preferred_model,
                        "vision_model": self.preferred_vision_model,
                        "cpu_only": self.cpu_only,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(tmp_file, _CONFIG_FILE)
        except Exception:
            pass

    def _http_json(self, path: str, payload: Optional[dict] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

    def is_available(self, timeout: float = 1.5) -> bool:
        try:
            now = time.monotonic()
            with self._lock:
                if self._models_cache is not None and (now - self._models_cache_ts) < 4.0:
                    return True
            self._http_json("/api/tags", timeout=timeout)
            return True
        except Exception:
            with self._lock:
                self._models_cache = None
                self._models_cache_ts = 0.0
            return False

    def list_models(self, timeout: float = 2.0, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Return installed Ollama models with name, size_bytes, estimated_vram_gb, and is_vision."""
        now = time.monotonic()
        with self._lock:
            if not force_refresh and self._models_cache is not None and (now - self._models_cache_ts) < 4.0:
                return list(self._models_cache)
        try:
            data = self._http_json("/api/tags", timeout=timeout)
            out = []
            for m in data.get("models") or []:
                name = str(m.get("name") or "").strip()
                if not name:
                    continue
                size = int(m.get("size") or 0)
                out.append(
                    {
                        "name": name,
                        "size_bytes": size,
                        "size_gb": round(size / (1024 ** 3), 2) if size else None,
                        "estimated_vram_gb": _estimate_model_vram_gb(name, size),
                        "is_vision": _is_vision_model_name(name),
                    }
                )
            with self._lock:
                self._models_cache = out
                self._models_cache_ts = time.monotonic()
            return list(out)
        except Exception:
            with self._lock:
                self._models_cache = None
                self._models_cache_ts = 0.0
            return []

    @staticmethod
    def _match_installed_name(preferred: str, installed_names: List[str]) -> Optional[str]:
        pref = (preferred or "").strip().lower()
        if not pref:
            return None
        for n in installed_names:
            if n.lower() == pref:
                return n
        for n in installed_names:
            nl = n.lower()
            if nl == f"{pref}:latest" or nl.startswith(pref + ":"):
                return n
        return None

    def select_best_model(self, vision: bool = False, max_vram_gb: float = 3.8) -> Optional[str]:
        """Select the best installed model respecting user preference and 4 GB VRAM safety."""
        models = self.list_models()
        if not models:
            return None
        names = [m["name"] for m in models]

        if vision:
            matched_vis = self._match_installed_name(self.preferred_vision_model, names)
            if matched_vis:
                return matched_vis
            matched_pref = self._match_installed_name(self.preferred_model, names)
            if matched_pref and _is_vision_model_name(matched_pref):
                return matched_pref
            vision_models = [m for m in models if m["is_vision"]]
            if not vision_models:
                return None
            # Prefer models that fit safely in 4 GB VRAM
            vision_models.sort(key=lambda m: (m["estimated_vram_gb"] > max_vram_gb, m["estimated_vram_gb"]))
            return vision_models[0]["name"]

        matched_text = self._match_installed_name(self.preferred_model, names)
        if matched_text:
            return matched_text

        # Prefer recommended 4 GB VRAM quantized models if installed
        for rec in RECOMMENDED_4GB_TEXT_MODELS:
            for n in names:
                if n == rec or n.startswith(rec.split(":")[0] + ":"):
                    return n

        # Otherwise pick the smallest non-embedding model that fits comfortably
        candidates = [m for m in models if "embed" not in m["name"].lower()]
        if not candidates:
            return None
        candidates.sort(key=lambda m: (m["estimated_vram_gb"] > max_vram_gb, m["estimated_vram_gb"]))
        return candidates[0]["name"]

    def unload_model(self, model_name: str) -> bool:
        """Ask Ollama to immediately unload a model from VRAM (keep_alive=0)."""
        if not model_name:
            return False
        try:
            self._http_json(
                "/api/generate",
                payload={"model": model_name, "prompt": "", "keep_alive": 0, "stream": False},
                timeout=4.0,
            )
            with self._lock:
                if self._active_loaded_model == model_name:
                    self._active_loaded_model = ""
            return True
        except Exception:
            return False

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        num_ctx: int = 4096,
        keep_alive: str = "3m",
        cpu_fallback: bool = True,
    ) -> Dict[str, Any]:
        """Run a local Ollama chat completion with single-model VRAM protection and optional CPU fallback."""
        chosen = model or self.select_best_model(vision=False)
        if not chosen:
            return {
                "ok": False,
                "error": "No local Ollama model is installed or Ollama is not running. Run 'ollama pull qwen2.5:3b' to enable local AI.",
            }

        # On 4 GB VRAM laptops, unload any previously active different model before loading the new one
        with self._lock:
            prev = self._active_loaded_model
        if prev and prev != chosen:
            self.unload_model(prev)

        def _invoke(force_cpu: bool) -> Dict[str, Any]:
            options: Dict[str, Any] = {
                "temperature": float(temperature),
                "num_ctx": int(num_ctx),
            }
            if force_cpu or self.cpu_only:
                options["num_gpu"] = 0
            payload = {
                "model": chosen,
                "messages": messages,
                "stream": False,
                "keep_alive": keep_alive,
                "options": options,
            }
            res = self._http_json("/api/chat", payload=payload)
            msg_obj = res.get("message") or {}
            content = str(msg_obj.get("content") or "").strip()
            with self._lock:
                self._active_loaded_model = chosen
            return {
                "ok": True,
                "model": chosen,
                "content": content,
                "cpu_mode": bool(force_cpu or self.cpu_only),
            }

        try:
            return _invoke(force_cpu=self.cpu_only)
        except Exception as exc:
            # Automatic CPU fallback if GPU VRAM allocation fails
            if cpu_fallback and not self.cpu_only:
                try:
                    out = _invoke(force_cpu=True)
                    out["warning"] = f"GPU inference failed ({exc}); completed using CPU fallback."
                    return out
                except Exception as exc2:
                    return {"ok": False, "model": chosen, "error": f"Local Ollama inference failed: {exc2}"}
            return {"ok": False, "model": chosen, "error": f"Local Ollama inference failed: {exc}"}

    def analyze_image_local(
        self,
        image_bytes: bytes,
        question: str = "Describe what is visible in this image or screenshot.",
        require_screen_permission: bool = True,
    ) -> Dict[str, Any]:
        """Analyze a screenshot or local image using an installed Ollama vision model (with optional OCR fallback)."""
        if require_screen_permission:
            if not permissions.request_permission("screen_view", f"Local offline image/screen analysis: '{question}'"):
                return {"ok": False, "verified": False, "permission_denied": True, "message": "Screen viewing permission was denied."}

        if not image_bytes:
            return {"ok": False, "verified": False, "message": "No image data was provided."}

        vision_model = self.select_best_model(vision=True)
        if vision_model:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            messages = [
                {
                    "role": "user",
                    "content": str(question or "Describe this image concisely.").strip(),
                    "images": [b64],
                }
            ]
            res = self.chat(messages=messages, model=vision_model, temperature=0.2, num_ctx=2048, keep_alive="90s")
            if res.get("ok") and res.get("content"):
                return {
                    "ok": True,
                    "verified": True,
                    "model": vision_model,
                    "message": res["content"],
                }

        # Fallback: local OCR via pytesseract if installed
        try:
            import io
            import pytesseract
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            ocr_text = (pytesseract.image_to_string(img) or "").strip()
            if ocr_text:
                return {
                    "ok": True,
                    "verified": True,
                    "engine": "pytesseract",
                    "text": ocr_text[:4000],
                    "message": f"Extracted text via local OCR:\n{ocr_text[:1500]}",
                }
        except Exception:
            pass

        return {
            "ok": False,
            "verified": False,
            "message": (
                "No local vision model or OCR engine is currently available offline. "
                "Install a compact Ollama vision model (e.g. 'ollama pull moondream') to enable offline visual analysis."
            ),
        }


class OfflineCapabilityManager:
    """Inspects and reports real offline capabilities, installed models, engines, and 4 GB VRAM guidance."""

    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ollama = ollama_client or OllamaClient()

    def _check_module(self, name: str) -> bool:
        try:
            __import__(name)
            return True
        except Exception:
            return False

    def inspect(self) -> Dict[str, Any]:
        hw = detect_hardware_profile()
        ollama_bin = shutil.which("ollama")
        ollama_running = self.ollama.is_available(timeout=1.2)
        models = self.ollama.list_models(timeout=1.8) if ollama_running else []
        text_models = [m for m in models if "embed" not in m["name"].lower()]
        vision_models = [m for m in models if m["is_vision"]]

        has_vosk = self._check_module("vosk")
        has_pypdf = self._check_module("pypdf")
        has_pymupdf = self._check_module("fitz")
        has_tesseract = self._check_module("pytesseract") and bool(shutil.which("tesseract"))
        has_sapi = os.name == "nt"

        warnings = list(hw.get("warnings") or [])
        missing = []
        if not ollama_bin:
            missing.append("Ollama runtime (install from https://ollama.com)")
        elif not ollama_running:
            warnings.append("Ollama is installed but its background service is not running (start Ollama to enable local AI).")
        elif not text_models:
            missing.append("Local LLM model (recommended for 4 GB VRAM: 'ollama pull qwen2.5:3b')")
        if not vision_models and not has_tesseract:
            missing.append("Local vision/OCR model (optional: 'ollama pull moondream')")

        selected_text = self.ollama.select_best_model(vision=False) if ollama_running else None
        selected_vision = self.ollama.select_best_model(vision=True) if ollama_running else None
        if selected_text:
            sel_meta = next((m for m in models if m["name"] == selected_text), None)
            if sel_meta and hw.get("gpu_vram_total_mb") and sel_meta["estimated_vram_gb"] > (hw["gpu_vram_total_mb"] / 1024.0):
                warnings.append(
                    f"Model '{selected_text}' (~{sel_meta['estimated_vram_gb']} GB) exceeds GPU VRAM; Ollama will offload layers to RAM or CPU fallback."
                )

        capabilities = {
            "local_ai_chat": bool(ollama_running and selected_text),
            "offline_agent_planning": bool(ollama_running and selected_text),
            "deterministic_windows_commands": True,
            "offline_wake_word": has_vosk,
            "offline_voice_commands": has_vosk,
            "offline_tts": has_sapi,
            "local_pdf_reading": bool(has_pypdf or has_pymupdf),
            "local_knowledge_indexing": True,
            "local_memory_and_tasks": True,
            "local_vision": bool(vision_models),
            "local_ocr": has_tesseract,
            "safe_python_sandbox": True,
        }

        return {
            "ok": True,
            "verified": True,
            "hardware": hw,
            "ollama": {
                "installed": bool(ollama_bin),
                "running": ollama_running,
                "models": models,
                "selected_text_model": selected_text,
                "selected_vision_model": selected_vision,
                "cpu_only": self.ollama.cpu_only,
                "recommended_4gb_models": list(RECOMMENDED_4GB_TEXT_MODELS[:4]),
                "recommended_4gb_vision": list(RECOMMENDED_4GB_VISION_MODELS[:3]),
            },
            "speech": {"vosk": has_vosk, "windows_sapi_tts": has_sapi},
            "ocr_and_docs": {"pypdf": has_pypdf, "pymupdf": has_pymupdf, "tesseract_ocr": has_tesseract},
            "capabilities": capabilities,
            "warnings": warnings,
            "missing": missing,
        }

    def format_summary(self, report: Optional[Dict[str, Any]] = None) -> str:
        r = report or self.inspect()
        hw = r["hardware"]
        ol = r["ollama"]
        caps = r["capabilities"]
        lines = ["Offline Capabilities Summary:"]
        if hw.get("ram_total_gb"):
            gpu_str = f", GPU: {hw['gpu_name']} ({hw['gpu_vram_total_mb']} MB VRAM)" if hw.get("gpu_name") else ", GPU: CPU mode"
            lines.append(f"• Hardware: {hw['ram_total_gb']} GB RAM ({hw['ram_available_gb']} GB free){gpu_str}")
        if ol["running"] and ol["selected_text_model"]:
            lines.append(f"• Local AI (Ollama): Ready — active model '{ol['selected_text_model']}' ({len(ol['models'])} installed)")
        elif ol["installed"]:
            lines.append("• Local AI (Ollama): Installed, but no running model detected (start Ollama & run 'ollama pull qwen2.5:3b')")
        else:
            lines.append("• Local AI (Ollama): Not installed (deterministic offline commands still work)")
        lines.append(f"• Local Vision / OCR: {'Ready (' + ol['selected_vision_model'] + ')' if ol['selected_vision_model'] else ('OCR only' if caps['local_ocr'] else 'Not installed (optional: ollama pull moondream)')}")
        lines.append(f"• Offline Voice: Wake word={'Yes' if caps['offline_wake_word'] else 'No'}, Offline TTS={'Yes' if caps['offline_tts'] else 'No'}")
        lines.append("• Local Tools: Windows commands, apps, folders, file search, PDF/docs, local knowledge index, memory/tasks, safe Python sandbox")
        if r["warnings"]:
            lines.append("• Resource Notes: " + " ".join(r["warnings"]))
        return "\n".join(lines)
