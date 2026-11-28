"""Video and lecture understanding agent for Glass Nova.

Analyzes playing video frames, lecture slides, whiteboard notes, and timestamps.
Uses Gemini multimodal vision for conceptual summaries and visual breakdown.
Checks media_access permissions before inspecting media content.
"""
import os, io, time
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List
from PIL import Image, ImageGrab
from dotenv import load_dotenv
from google import genai
from google.genai import types

from permissions import permissions
from screen_agent import get_active_window_title

ROOT = (
    Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
load_dotenv(Path(os.environ.get("APPDATA") or Path.home()) / "PC_Complete_Assistant" / ".env", override=False)
load_dotenv(ROOT / ".env", override=False)
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.5-flash")

_client = None

def get_client() -> genai.Client:
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY is missing from .env")
        _client = genai.Client(api_key=key)
    return _client

from screen_agent import capture_screen_jpeg, get_active_window_title, get_vision_models, remember_working_vision_model

def capture_video_frame(max_dimension: int = 1400) -> bytes:
    """Captures current frame from desktop / active player window."""
    return capture_screen_jpeg(max_dimension=max_dimension)

def sample_video_frames(samples: int = 2, interval_seconds: float = 1.0) -> List[bytes]:
    """Captures a sequence of frames over a short interval to capture video progression."""
    frames = []
    for i in range(samples):
        frames.append(capture_video_frame())
        if i < samples - 1:
            time.sleep(interval_seconds)
    return frames

def analyze_video(query: str = "Explain what the teacher or video is explaining.",
                  multi_frame: bool = False,
                  timestamp_hint: Optional[str] = None) -> Dict[str, Any]:
    """
    Main video understanding entrypoint.
    Captures visible video playback, identifies slides/whiteboard, and explains via Gemini vision.
    """
    active_window = get_active_window_title()
    action_desc = f"Analyze video playback in '{active_window}': '{query}'"

    if not permissions.request_permission("media_access", action_desc, {"window": active_window}):
        return {
            "ok": False,
            "verified": False,
            "message": "Media content observation permission was denied.",
            "permission_denied": True
        }
    if not permissions.request_permission("screen_view", f"Capture screen frame for video analysis in '{active_window}'", {"window": active_window}):
        return {
            "ok": False,
            "verified": False,
            "message": "Screen viewing permission was denied.",
            "permission_denied": True
        }

    try:
        captured_frames: List[bytes] = []
        if multi_frame:
            captured_frames = sample_video_frames(samples=2, interval_seconds=1.2)
        else:
            captured_frames = [capture_video_frame()]

        system_instruction = (
            "You are Nova's video and lecture understanding specialist. "
            f"The video is playing in the window: '{active_window}'. "
            "Inspect the visible lecture slides, whiteboard, video presenter, diagrams, or captions. "
            "Synthesize the key points being taught or explained. "
            "If timestamps are visible, reference them. "
            "Explain difficult concepts step by step in simple conversational language. "
            "Accurately scope your analysis to the observed frame(s) without inventing unobserved content."
        )

        user_prompt = f"User Question: {query}\nActive Window: {active_window}"
        if timestamp_hint:
            user_prompt += f"\nTimestamp hint: {timestamp_hint}"

        answer = ""
        engine = "gemini"
        try:
            client = get_client()
            parts = [types.Part.from_bytes(data=fb, mime_type="image/jpeg") for fb in captured_frames]
            parts.append(types.Part.from_text(text=user_prompt))

            models = get_vision_models()
            response = None
            last_err = None
            for m_name in models:
                try:
                    response = client.models.generate_content(
                        model=m_name,
                        contents=parts,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.3,
                            max_output_tokens=800
                        )
                    )
                    remember_working_vision_model(m_name)
                    break
                except Exception as e:
                    last_err = e
                    continue
            if response is None:
                raise last_err or RuntimeError("Online video analysis failed across all models.")
            answer = (response.text or "").strip()
        except Exception as online_err:
            # Offline fallback: use local Ollama vision model or local OCR on the captured frame
            from nova_offline_ai import OllamaClient
            local_res = OllamaClient().analyze_image_local(
                image_bytes=captured_frames[-1],
                question=f"{user_prompt}\nExplain the visible video frame, slide, or whiteboard accurately without guessing.",
                require_screen_permission=False,
            )
            if local_res.get("ok") and local_res.get("message"):
                answer = str(local_res["message"]).strip()
                engine = local_res.get("model") or local_res.get("engine") or "local_vision"
            else:
                return {
                    "ok": False,
                    "verified": False,
                    "message": (
                        f"Video analysis could not run online ({online_err}) and no local vision/OCR model is available offline. "
                        "Install a compact local vision model (e.g. 'ollama pull moondream') or reconnect to Gemini."
                    ),
                }

        if not answer:
            return {
                "ok": False,
                "verified": False,
                "message": "I observed the video frame, but could not extract any recognizable slide, whiteboard, or caption content.",
            }

        return {
            "ok": True,
            "verified": True,
            "engine": engine,
            "message": answer,
            "active_window": active_window,
            "query": query
        }
    except Exception as e:
        return {
            "ok": False,
            "verified": False,
            "message": f"Video analysis failed: {e}"
        }
