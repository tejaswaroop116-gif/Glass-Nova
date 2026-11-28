"""nova_updater.py - Safe, read-only GitHub Releases update checker for Glass Nova.

Security guarantees:
- Checks ONLY the official tejaswaroop30/Glass-Nova GitHub Releases API over HTTPS.
- Strictly validates release URLs before exposing or opening them in the default browser.
- NEVER downloads, unpacks, or executes installers automatically.
- NEVER modifies user settings, API keys, or DPAPI-encrypted workflows.
- Handles offline environments, rate limits, timeouts, and malformed payloads without crashing.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import webbrowser


APP_VERSION = "1.1.0-beta"
GITHUB_OWNER = "tejaswaroop30"
GITHUB_REPO = "Glass-Nova"
OFFICIAL_REPO_PREFIX = f"/{GITHUB_OWNER.lower()}/{GITHUB_REPO.lower()}/"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

_SEMVER_RE = re.compile(
    r"^\s*[vV]?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\s*$"
)
_PRERELEASE_ORDER = {
    "dev": 0,
    "alpha": 1,
    "a": 1,
    "beta": 2,
    "b": 2,
    "rc": 3,
}


def get_current_version() -> str:
    """Return the currently installed Glass Nova semantic version string."""
    return APP_VERSION


def parse_semver(version_str: str) -> Optional[tuple[int, int, int, int, tuple[tuple[int, Any], ...]]]:
    """Parse a semantic version string into a comparable tuple.

    Ordering rules:
    - Numeric major.minor.patch compared numerically (v1.10.0 > v1.2.0).
    - Stable releases sort higher than prereleases of the same core version (1.0.0 > 1.0.0-beta).
    - Prerelease identifiers compare known stages (alpha < beta < rc) and numeric suffixes.
    Returns None if the string is not a valid semantic version.
    """
    if not isinstance(version_str, str):
        return None
    m = _SEMVER_RE.match(version_str)
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3) or 0)
    pre_raw = m.group(4)

    if not pre_raw:
        return (major, minor, patch, 1, ())

    parts: list[tuple[int, Any]] = []
    for token in pre_raw.split("."):
        low = token.lower()
        if low.isdigit():
            parts.append((1, int(low)))
        elif low in _PRERELEASE_ORDER:
            parts.append((0, _PRERELEASE_ORDER[low]))
        else:
            parts.append((2, low))
    return (major, minor, patch, 0, tuple(parts))


def compare_versions(v1: str, v2: str) -> int:
    """Compare two semantic versions.

    Returns:
      -1 if v1 < v2
       0 if v1 == v2
       1 if v1 > v2
    Raises ValueError if either version string is invalid.
    """
    p1 = parse_semver(v1)
    p2 = parse_semver(v2)
    if p1 is None or p2 is None:
        raise ValueError(f"Invalid semantic version comparison: '{v1}' vs '{v2}'")
    if p1 < p2:
        return -1
    if p1 > p2:
        return 1
    return 0


def is_update_available(current_version: str, latest_version: str) -> bool:
    """Return True strictly when latest_version is a valid semantic version newer than current_version."""
    try:
        return compare_versions(current_version, latest_version) < 0
    except ValueError:
        return False


def is_trusted_release_url(url: str) -> bool:
    """Validate that a release URL points strictly to the official Glass-Nova GitHub repository over HTTPS."""
    if not isinstance(url, str):
        return False
    raw = url.strip()
    if not raw or len(raw) > 512:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host != "github.com":
        return False
    path_low = (parsed.path or "").lower()
    if not (
        path_low == f"/{GITHUB_OWNER.lower()}/{GITHUB_REPO.lower()}/releases"
        or path_low.startswith(f"{OFFICIAL_REPO_PREFIX}releases/")
    ):
        return False
    if ".." in parsed.path or any(c in raw for c in ("\r", "\n", " ", "\"", "'", "<", ">")):
        return False
    return True


def open_trusted_release_url(url: str) -> bool:
    """Open a validated official Glass-Nova release URL in the user's default browser."""
    target = url if is_trusted_release_url(url) else RELEASES_PAGE_URL
    if not is_trusted_release_url(target):
        return False
    return bool(webbrowser.open(target, new=2))


@dataclass(frozen=True)
class UpdateCheckResult:
    status: str  # "up_to_date" | "update_available" | "offline" | "rate_limited" | "error"
    current_version: str
    latest_version: str = ""
    release_name: str = ""
    release_notes: str = ""
    release_url: str = RELEASES_PAGE_URL
    published_at: str = ""
    message: str = ""

    @property
    def has_update(self) -> bool:
        return self.status == "update_available"


def parse_github_release_payload(payload: Any, current_version: str = APP_VERSION) -> UpdateCheckResult:
    """Parse and validate a GitHub Releases API JSON object into an UpdateCheckResult."""
    if not isinstance(payload, dict):
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            message="Received malformed release data from GitHub.",
        )

    tag = str(payload.get("tag_name") or "").strip()
    if not tag or parse_semver(tag) is None:
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            message="Latest GitHub release did not contain a valid semantic version tag.",
        )

    html_url = str(payload.get("html_url") or "").strip()
    safe_url = html_url if is_trusted_release_url(html_url) else RELEASES_PAGE_URL
    rel_name = str(payload.get("name") or tag).strip()[:160]
    notes_raw = str(payload.get("body") or "").strip()
    notes = notes_raw[:4000] if notes_raw else "No release notes provided."
    published = str(payload.get("published_at") or "").strip()[:32]

    if is_update_available(current_version, tag):
        return UpdateCheckResult(
            status="update_available",
            current_version=current_version,
            latest_version=tag,
            release_name=rel_name,
            release_notes=notes,
            release_url=safe_url,
            published_at=published,
            message=f"Update available: {tag} (installed: v{current_version.lstrip('vV')}).",
        )

    return UpdateCheckResult(
        status="up_to_date",
        current_version=current_version,
        latest_version=tag,
        release_name=rel_name,
        release_notes=notes,
        release_url=safe_url,
        published_at=published,
        message=f"Glass Nova is up to date (v{current_version.lstrip('vV')}).",
    )


def check_for_updates(
    current_version: str = APP_VERSION,
    timeout: float = 8.0,
    fetcher: Optional[Callable[[str, float], bytes]] = None,
) -> UpdateCheckResult:
    """Query the official Glass-Nova GitHub Releases API safely without ever raising."""
    def _default_fetch(url: str, to: float) -> bytes:
        req = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"GlassNova/{current_version}",
            },
        )
        with urlopen(req, timeout=to) as resp:
            return resp.read(262144)  # Cap read at 256 KB

    fetch_fn = fetcher or _default_fetch
    try:
        raw_bytes = fetch_fn(RELEASES_API_URL, timeout)
    except HTTPError as exc:
        if exc.code in (403, 429):
            return UpdateCheckResult(
                status="rate_limited",
                current_version=current_version,
                message="GitHub API rate limit reached. You can still check the Releases page in your browser.",
            )
        if exc.code == 404:
            return UpdateCheckResult(
                status="error",
                current_version=current_version,
                message="No published GitHub release was found yet.",
            )
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            message=f"GitHub returned HTTP {exc.code} while checking for updates.",
        )
    except (URLError, TimeoutError, OSError):
        return UpdateCheckResult(
            status="offline",
            current_version=current_version,
            message="Could not reach GitHub (offline or network unavailable).",
        )
    except Exception:
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            message="Unexpected error while checking for updates.",
        )

    try:
        payload = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    except Exception:
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            message="Could not parse GitHub release response (invalid JSON).",
        )

    return parse_github_release_payload(payload, current_version=current_version)
