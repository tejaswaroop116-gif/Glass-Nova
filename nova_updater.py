"""nova_updater.py - Safe, verified one-click GitHub Releases updater for Glass Nova.

Security guarantees:
- Checks ONLY the official tejaswaroop30/Glass-Nova GitHub Releases API over HTTPS.
- Strictly validates release URLs, asset download URLs, and HTTPS redirect hosts.
- Downloads strictly into a dedicated temp directory (%TEMP%/GlassNova/updates), never into the app/dev folder.
- Verifies downloaded installer size, PE header, and official SHA-256 digest before allowing execution.
- NEVER executes an unverified installer, NEVER silently installs or elevates, and NEVER modifies user data.
- Re-verifies SHA-256 immediately before launching the interactive Inno Setup installer (TOCTOU-safe).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
import webbrowser


APP_VERSION = "1.1.0-beta"
GITHUB_OWNER = "tejaswaroop30"
GITHUB_REPO = "Glass-Nova"
OFFICIAL_REPO_PREFIX = f"/{GITHUB_OWNER.lower()}/{GITHUB_REPO.lower()}/"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

_TRUSTED_REDIRECT_HOSTS = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})
_INSTALLER_NAME_RE = re.compile(r"^GlassNova-Setup-[0-9A-Za-z._-]+\.exe$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_BODY_SHA256_RE = re.compile(r"SHA-?256\s*[:=]\s*`?([0-9a-fA-F]{64})`?", re.IGNORECASE)
_MAX_INSTALLER_BYTES = 512 * 1024 * 1024  # 512 MB hard safety cap

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


def format_size_mb(num_bytes: int) -> str:
    """Format a byte count cleanly as human-readable MB."""
    if not num_bytes or num_bytes <= 0:
        return "Unknown size"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def parse_semver(version_str: str) -> Optional[tuple[int, int, int, int, tuple[tuple[int, Any], ...]]]:
    """Parse a semantic version string into a comparable tuple."""
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
    """Compare two semantic versions (-1 if v1 < v2, 0 if equal, 1 if v1 > v2)."""
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


def is_trusted_asset_url(url: str) -> bool:
    """Validate that a download URL points strictly to an official GlassNova-Setup-*.exe release asset."""
    if not is_trusted_release_url(url):
        return False
    parsed = urlparse(url.strip())
    path = parsed.path or ""
    if not path.lower().startswith(f"{OFFICIAL_REPO_PREFIX}releases/download/"):
        return False
    filename = path.rsplit("/", 1)[-1]
    return bool(_INSTALLER_NAME_RE.match(filename))


def is_trusted_redirect_url(url: str) -> bool:
    """Allow HTTPS redirects ONLY to official GitHub asset CDN hosts."""
    if not isinstance(url, str):
        return False
    raw = url.strip()
    if not raw or len(raw) > 2048:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host not in _TRUSTED_REDIRECT_HOSTS:
        return False
    if host == "github.com":
        return is_trusted_release_url(raw)
    return True


class _SafeGitHubRedirectHandler(HTTPRedirectHandler):
    """Refuses any HTTP redirect outside official GitHub HTTPS asset hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_trusted_redirect_url(newurl):
            raise URLError(f"Blocked redirect to untrusted host: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_trusted_release_url(url: str) -> bool:
    """Open a validated official Glass-Nova release URL in the user's default browser."""
    target = url if is_trusted_release_url(url) else RELEASES_PAGE_URL
    if not is_trusted_release_url(target):
        return False
    return bool(webbrowser.open(target, new=2))


def _normalize_sha256_digest(raw: Any) -> str:
    """Extract a validated uppercase 64-char SHA-256 hex digest from GitHub API 'sha256:<hex>' or raw hex."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.lower().startswith("sha256:"):
        text = text[7:].strip()
    return text.upper() if _SHA256_HEX_RE.match(text) else ""


def _extract_body_sha256(body: str) -> str:
    """Extract an optional SHA-256 digest published in release notes text."""
    if not isinstance(body, str):
        return ""
    m = _BODY_SHA256_RE.search(body)
    return m.group(1).upper() if m else ""


def _select_installer_asset(payload: dict[str, Any], notes: str) -> tuple[str, int, str, str]:
    """Find the official Windows installer asset (name, size, download_url, sha256) in a release payload."""
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return "", 0, "", ""
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not _INSTALLER_NAME_RE.match(name):
            continue
        dl_url = str(item.get("browser_download_url") or "").strip()
        if not is_trusted_asset_url(dl_url):
            continue
        try:
            size = max(0, int(item.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        digest = _normalize_sha256_digest(item.get("digest")) or _extract_body_sha256(notes)
        return name, size, dl_url, digest
    return "", 0, "", ""


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
    asset_name: str = ""
    asset_size: int = 0
    asset_download_url: str = ""
    asset_sha256: str = ""

    @property
    def has_update(self) -> bool:
        return self.status == "update_available"

    @property
    def has_downloadable_installer(self) -> bool:
        return bool(
            self.has_update
            and self.asset_name
            and is_trusted_asset_url(self.asset_download_url)
        )


@dataclass(frozen=True)
class DownloadResult:
    status: str  # "verified" | "cancelled" | "corrupted" | "unverified" | "error"
    file_path: str = ""
    sha256: str = ""
    size_bytes: int = 0
    verified: bool = False
    message: str = ""


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
    asset_name, asset_size, asset_url, asset_sha256 = _select_installer_asset(payload, notes_raw)

    if is_update_available(current_version, tag):
        size_hint = f" · {format_size_mb(asset_size)}" if asset_size > 0 else ""
        return UpdateCheckResult(
            status="update_available",
            current_version=current_version,
            latest_version=tag,
            release_name=rel_name,
            release_notes=notes,
            release_url=safe_url,
            published_at=published,
            message=f"Update available: {tag} (installed: v{current_version.lstrip('vV')}){size_hint}.",
            asset_name=asset_name,
            asset_size=asset_size,
            asset_download_url=asset_url,
            asset_sha256=asset_sha256,
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
        asset_name=asset_name,
        asset_size=asset_size,
        asset_download_url=asset_url,
        asset_sha256=asset_sha256,
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
            return resp.read(262144)

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


# =============================================================================
# VERIFIED TEMPORARY DOWNLOAD, DIGEST CHECK & ONE-CLICK INSTALLER LAUNCH
# =============================================================================

def get_update_temp_dir() -> Path:
    """Return dedicated temporary directory for downloaded update installers (never app/dev dir)."""
    d = Path(tempfile.gettempdir()) / "GlassNova" / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def cleanup_stale_update_files(temp_dir: Optional[Path] = None, keep_path: Optional[Path] = None) -> int:
    """Safely remove stale .part and old GlassNova-Setup-*.exe files inside the update temp folder."""
    target_dir = Path(temp_dir).resolve() if temp_dir else get_update_temp_dir()
    if not target_dir.is_dir():
        return 0
    keep_resolved = Path(keep_path).resolve() if keep_path else None
    removed = 0
    for child in target_dir.iterdir():
        if not child.is_file():
            continue
        if keep_resolved is not None and child.resolve() == keep_resolved:
            continue
        name = child.name
        if name.endswith(".part") or _INSTALLER_NAME_RE.match(name):
            try:
                child.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def compute_file_sha256(path: str | Path) -> str:
    """Compute uppercase SHA-256 hex digest of a file using streaming 64 KB chunks."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def verify_installer_file(
    path: str | Path,
    expected_sha256: str = "",
    expected_size: int = 0,
) -> tuple[bool, str, str]:
    """Verify downloaded installer exists, has valid PE header, matches expected size, and matches SHA-256."""
    p = Path(path)
    if not p.is_file():
        return False, "", "Downloaded installer file was not found."

    size = p.stat().st_size
    if size <= 0:
        return False, "", "Downloaded installer file is empty."
    if size > _MAX_INSTALLER_BYTES:
        return False, "", "Downloaded file exceeds maximum allowed installer size."
    if expected_size > 0 and size != expected_size:
        return False, "", f"Incomplete download: expected {expected_size} bytes, got {size} bytes."

    try:
        with p.open("rb") as f:
            magic = f.read(2)
    except OSError as exc:
        return False, "", f"Could not read downloaded installer: {exc}"
    if magic != b"MZ":
        return False, "", "Downloaded file is not a valid Windows executable (missing PE header)."

    actual_sha = compute_file_sha256(p)
    expected_norm = _normalize_sha256_digest(expected_sha256)
    if not expected_norm:
        return (
            False,
            actual_sha,
            "No official SHA-256 digest was published for this release; refusing to execute an unverified installer.",
        )
    if actual_sha != expected_norm:
        return (
            False,
            actual_sha,
            f"SHA-256 checksum mismatch (expected {expected_norm[:12]}…, got {actual_sha[:12]}…).",
        )
    return True, actual_sha, f"Verified SHA-256 ({actual_sha[:12]}…)."


def download_update_installer(
    update_result: UpdateCheckResult,
    dest_dir: Optional[Path] = None,
    on_progress: Optional[Callable[[int, int, int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    opener: Optional[Any] = None,
    timeout: float = 20.0,
) -> DownloadResult:
    """Download and verify the official Glass Nova installer into a temporary folder.

    Never raises uncaught exceptions; deletes partial or corrupted files automatically.
    """
    url = str(update_result.asset_download_url or "").strip()
    name = str(update_result.asset_name or "").strip()
    expected_sha = _normalize_sha256_digest(update_result.asset_sha256)
    expected_size = max(0, int(update_result.asset_size or 0))

    if not name or not _INSTALLER_NAME_RE.match(name) or not is_trusted_asset_url(url):
        return DownloadResult(
            status="error",
            message="Release does not contain a trusted official Windows installer URL.",
        )

    if not expected_sha:
        return DownloadResult(
            status="unverified",
            message="No official SHA-256 digest was published for this release. Use 'Open Releases Page' to download manually.",
        )

    target_dir = Path(dest_dir).resolve() if dest_dir else get_update_temp_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    cleanup_stale_update_files(target_dir)

    part_path = target_dir / f"{name}.part"
    final_path = target_dir / name

    def _remove_part():
        try:
            if part_path.exists():
                part_path.unlink()
        except OSError:
            pass

    url_opener = opener if opener is not None else build_opener(_SafeGitHubRedirectHandler())
    req = Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"GlassNova/{APP_VERSION}",
        },
    )

    downloaded = 0
    try:
        with url_opener.open(req, timeout=timeout) as resp:
            hdr_len = 0
            try:
                hdr_len = int(resp.headers.get("Content-Length") or 0) if getattr(resp, "headers", None) else 0
            except (TypeError, ValueError):
                hdr_len = 0
            total = expected_size or hdr_len

            if total > _MAX_INSTALLER_BYTES:
                return DownloadResult(status="error", message="Installer size exceeds safety limit.")

            with part_path.open("wb") as out_f:
                while True:
                    if is_cancelled and is_cancelled():
                        out_f.close()
                        _remove_part()
                        return DownloadResult(status="cancelled", message="Download cancelled by user.")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > _MAX_INSTALLER_BYTES:
                        out_f.close()
                        _remove_part()
                        return DownloadResult(status="error", message="Download exceeded maximum safe size.")
                    out_f.write(chunk)
                    if on_progress:
                        pct = int(min(100, (downloaded * 100) / total)) if total > 0 else 0
                        on_progress(downloaded, total, pct)
    except HTTPError as exc:
        _remove_part()
        return DownloadResult(status="error", message=f"Download failed (HTTP {exc.code}).")
    except (URLError, TimeoutError, OSError) as exc:
        _remove_part()
        return DownloadResult(status="error", message=f"Download interrupted: {exc}")
    except Exception as exc:
        _remove_part()
        return DownloadResult(status="error", message=f"Unexpected download error: {exc}")

    if is_cancelled and is_cancelled():
        _remove_part()
        return DownloadResult(status="cancelled", message="Download cancelled by user.")

    ok, actual_sha, reason = verify_installer_file(
        part_path,
        expected_sha256=expected_sha,
        expected_size=expected_size,
    )
    if not ok:
        _remove_part()
        return DownloadResult(
            status="corrupted",
            sha256=actual_sha,
            size_bytes=downloaded,
            verified=False,
            message=f"Verification failed: {reason}",
        )

    try:
        os.replace(part_path, final_path)
    except OSError as exc:
        _remove_part()
        return DownloadResult(status="error", message=f"Could not finalize downloaded installer: {exc}")

    return DownloadResult(
        status="verified",
        file_path=str(final_path),
        sha256=actual_sha,
        size_bytes=final_path.stat().st_size,
        verified=True,
        message=f"Download verified · SHA-256 {actual_sha[:12]}…",
    )


def detect_installed_app_dir() -> Optional[Path]:
    """Return the existing packaged installation directory when running frozen, else None (never returns dev folder)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def build_installer_launch_args(
    installer_path: str | Path,
    install_dir: Optional[str | Path] = None,
    allowed_dir: Optional[Path] = None,
) -> list[str]:
    """Build safe, interactive (non-silent) Inno Setup launch arguments for a verified temp installer."""
    p = Path(installer_path).resolve()
    base_dir = Path(allowed_dir).resolve() if allowed_dir else get_update_temp_dir()
    if p.parent != base_dir:
        raise ValueError("Installer path must reside inside Glass Nova's temporary update directory.")
    if not _INSTALLER_NAME_RE.match(p.name):
        raise ValueError(f"Unexpected installer filename: {p.name}")
    if not p.is_file():
        raise FileNotFoundError(f"Installer not found: {p}")

    args = [
        str(p),
        "/CLOSEAPPLICATIONS",
        "/RESTARTAPPLICATIONS",
    ]
    if install_dir is not None:
        clean_dir = str(Path(install_dir).resolve())
        if clean_dir:
            args.append(f"/DIR={clean_dir}")
    return args


def launch_verified_installer(
    download_result: DownloadResult,
    install_dir: Optional[str | Path] = None,
    allowed_dir: Optional[Path] = None,
    popen_fn: Optional[Callable[..., Any]] = None,
) -> tuple[bool, str]:
    """Re-verify SHA-256 on disk and launch the interactive installer with shell=False.

    Never executes unverified files; never silently installs or elevates.
    """
    if not download_result or not download_result.verified or download_result.status != "verified":
        return False, "Refusing to launch: installer has not passed integrity verification."
    if not download_result.file_path or not download_result.sha256:
        return False, "Refusing to launch: missing verified installer path or SHA-256 digest."

    ok, _sha, reason = verify_installer_file(
        download_result.file_path,
        expected_sha256=download_result.sha256,
        expected_size=download_result.size_bytes,
    )
    if not ok:
        return False, f"Pre-launch verification failed: {reason}"

    target_install_dir = install_dir if install_dir is not None else detect_installed_app_dir()
    try:
        args = build_installer_launch_args(
            download_result.file_path,
            install_dir=target_install_dir,
            allowed_dir=allowed_dir,
        )
    except Exception as exc:
        return False, f"Invalid installer launch configuration: {exc}"

    launcher = popen_fn or subprocess.Popen
    kwargs: dict[str, Any] = {"shell": False}
    if sys.platform == "win32" and popen_fn is None:
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        launcher(args, **kwargs)
    except Exception as exc:
        return False, f"Could not launch Windows installer: {exc}"
    return True, "Installer launched. Glass Nova will now close so the update can complete."
