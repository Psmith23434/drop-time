#!/usr/bin/env python3
"""
drop_time_scraper.py
====================
Fetches the exact drop time for pending-delete .com/.net/etc. domains.
Tries multiple techniques in order of reliability:

  Technique 1 - nodriver   : Undetected Chrome, solves Cloudflare Turnstile,
                              reads the EXACT drop time from Dynadot backorder page.
                              Requires: pip install nodriver
                              Requires: Google Chrome / Chromium / Edge / Brave installed.
                              Pass --browser-path "C:\\path\\to\\chrome.exe" if not default.

  Technique 2 - curl-cffi  : TLS/JA3 fingerprint impersonation (Chrome 124).
                              Fast, no browser needed. Works IF Cloudflare is
                              ever relaxed. Currently blocked by CF Managed
                              Challenge on Dynadot - included for future use.
                              Requires: pip install curl-cffi

  Technique 3 - RDAP        : IANA public RDAP API, no auth, no cost.
                              Finds the pendingDelete entry date then adds the
                              TLD-specific drop window offset.
                              Confidence: ESTIMATED (no library needed).
                              NOTE: accuracy depends on registry publishing the
                              pendingDelete event date. Falls back to last-changed
                              date if not available, which reduces accuracy.

  Technique 4 - WHOIS       : Raw socket WHOIS query, no library needed.
                              Reads Updated Date and adds TLD drop window offset.
                              Confidence: ESTIMATED (~+-12h accuracy).

Usage:
  python drop_time_scraper.py zenithpicks.com example.com
  python drop_time_scraper.py --technique rdap zenithpicks.com
  python drop_time_scraper.py --technique whois zenithpicks.com
  python drop_time_scraper.py --json zenithpicks.com
  python drop_time_scraper.py --browser-path "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" zenithpicks.com

Install only what you need:
  pip install nodriver           # Technique 1 (best accuracy)
  pip install curl-cffi          # Technique 2 (fast, currently blocked on Dynadot)
  # Techniques 3 & 4 require only Python stdlib

Chrome binary auto-detection order (Technique 1):
  1. --browser-path CLI argument
  2. CHROME_PATH environment variable
  3. Common Windows paths (Chrome, Edge, Brave, Chromium)
  4. Common macOS paths
  5. Common Linux paths
  6. PATH lookup (google-chrome, chromium-browser, microsoft-edge, brave-browser)
"""

from __future__ import annotations
import argparse
import asyncio
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


# --- TLD drop window table ---------------------------------------------------
# (pending_delete_days, typical_drop_hour_utc, typical_drop_minute_utc)
TLD_DROP_WINDOWS: dict[str, tuple[int, int, int]] = {
    "com":  (5, 15, 30),
    "net":  (5, 15, 30),
    "org":  (5, 16,  0),
    "info": (5, 16,  0),
    "biz":  (5, 16,  0),
    "co":   (5, 18,  0),
    "io":   (5, 16,  0),
    "me":   (5, 16,  0),
    "us":   (5, 16,  0),
    "mobi": (5, 16,  0),
    "name": (5, 16,  0),
    "pro":  (5, 16,  0),
}
DEFAULT_DROP_WINDOW = (5, 16, 0)

WHOIS_SERVERS: dict[str, str] = {
    "com":  "whois.verisign-grs.com",
    "net":  "whois.verisign-grs.com",
    "org":  "whois.pir.org",
    "info": "whois.afilias.net",
    "biz":  "whois.neulevel.biz",
    "io":   "whois.nic.io",
    "co":   "whois.nic.co",
    "me":   "whois.nic.me",
    "us":   "whois.nic.us",
}

# Common Chrome-family binary paths per OS
CHROME_PATHS_WINDOWS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Chromium\Application\chrome.exe",
    r"C:\Program Files (x86)\Chromium\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
]
CHROME_PATHS_MAC = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
CHROME_PATHS_LINUX = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/microsoft-edge",
    "/usr/bin/brave-browser",
    "/snap/bin/chromium",
]
CHROME_PATH_CMDS = [
    "google-chrome", "google-chrome-stable",
    "chromium-browser", "chromium",
    "microsoft-edge", "brave-browser",
]

PST = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


# =============================================================================
# Chrome binary auto-detection
# =============================================================================

def _find_chrome_binary(user_path: Optional[str] = None) -> Optional[str]:
    """
    Returns path to a usable Chrome-family binary, or None if not found.
    Search order:
      1. user_path argument (--browser-path CLI flag)
      2. CHROME_PATH environment variable
      3. OS-specific common install paths
      4. PATH lookup for known binary names
    """
    for candidate in [user_path, os.environ.get("CHROME_PATH")]:
        if candidate and Path(candidate).is_file():
            return candidate

    if sys.platform == "win32":
        paths = CHROME_PATHS_WINDOWS
    elif sys.platform == "darwin":
        paths = CHROME_PATHS_MAC
    else:
        paths = CHROME_PATHS_LINUX

    for p in paths:
        if Path(p).is_file():
            return p

    for cmd in CHROME_PATH_CMDS:
        found = shutil.which(cmd)
        if found:
            return found

    return None


# =============================================================================
# Result object
# =============================================================================

class DropResult:
    def __init__(self, domain: str):
        self.domain       : str                = domain.lower().strip()
        self.drop_dt_utc  : Optional[datetime] = None
        self.drop_dt_pst  : Optional[datetime] = None
        self.raw_text     : Optional[str]       = None
        self.technique    : Optional[str]       = None
        self.confidence   : str                = "unknown"
        self.error        : Optional[str]       = None

    def set_dt(self, dt: datetime, technique: str, confidence: str, raw: str = "") -> None:
        self.technique  = technique
        self.confidence = confidence
        self.raw_text   = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        self.drop_dt_utc = dt.astimezone(UTC)
        self.drop_dt_pst = dt.astimezone(PST)

    def display(self) -> str:
        icon = {"exact": "[EXACT]", "estimated": "[EST] ", "failed": "[FAIL]"}.get(self.confidence, "[ ?? ]")
        if self.drop_dt_utc:
            utc_s = self.drop_dt_utc.strftime("%Y-%m-%d %H:%M UTC")
            pst_s = self.drop_dt_pst.strftime("%Y-%m-%d %H:%M PST")
            return (
                f"{icon}  {self.domain}\n"
                f"   Drop Time  : {utc_s}  ({pst_s})\n"
                f"   Confidence : {self.confidence.upper()}\n"
                f"   Technique  : {self.technique}\n"
                f"   Raw source : {self.raw_text or '-'}"
            )
        return f"[FAIL]  {self.domain}  --  FAILED: {self.error}"

    def to_dict(self) -> dict:
        return {
            "domain":     self.domain,
            "drop_utc":   self.drop_dt_utc.isoformat() if self.drop_dt_utc else None,
            "drop_pst":   self.drop_dt_pst.isoformat() if self.drop_dt_pst else None,
            "confidence": self.confidence,
            "technique":  self.technique,
            "raw":        self.raw_text,
            "error":      self.error,
        }


# =============================================================================
# Shared helpers
# =============================================================================

def _normalize_domain(domain: str) -> str:
    domain = domain.lower().strip()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].split("?")[0]
    return domain


def _parse_dynadot_dt(raw: str) -> datetime:
    """Parse '2026/04/08 10:45 PST' (or UTC/GMT) into an aware datetime."""
    raw = raw.strip()
    m = re.search(
        r"(\d{4})[/\-](\d{2})[/\-](\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?\s*(PST|PDT|UTC|GMT)?",
        raw, re.IGNORECASE,
    )
    if not m:
        raise ValueError(f"Cannot parse datetime from: {raw!r}")
    Y, Mo, D, h, mi = (int(m.group(i)) for i in (1, 2, 3, 4, 5))
    tz_str = (m.group(7) or "PST").upper()
    tz_map = {
        "PST": ZoneInfo("America/Los_Angeles"),
        "PDT": ZoneInfo("America/Los_Angeles"),
        "UTC": UTC,
        "GMT": UTC,
    }
    return datetime(Y, Mo, D, h, mi, 0, tzinfo=tz_map.get(tz_str, UTC))


# =============================================================================
# Technique 1 - nodriver (real undetected Chrome, solves CF Turnstile)
# =============================================================================

async def _fetch_nodriver(domain: str, browser_path: Optional[str] = None) -> DropResult:
    """
    Launches a real Chrome/Edge/Brave instance via nodriver with auto-detected binary.

    nodriver.start() silently returns None when given a non-Chrome binary (Edge, Brave)
    on some versions. We work around this by building the Config object directly and
    calling Browser.create(), which is the stable low-level API.
    """
    result = DropResult(domain)
    tmp_profile: Optional[str] = None
    try:
        import nodriver as uc  # pip install nodriver

        chrome_bin = _find_chrome_binary(browser_path)
        if not chrome_bin:
            result.error = (
                "Chrome/Edge/Brave not found. Install one of them, "
                "or pass --browser-path \"C:\\path\\to\\browser.exe\""
            )
            return result

        url = (
            f"https://www.dynadot.com/market/backorder/{domain}"
            f"?rscbo=expireddomains"
        )
        print(f"      Using browser: {chrome_bin}", flush=True)
        print("      Launching browser...", flush=True)

        # Create a fresh temp profile so Edge/Brave don't hit single-instance locks
        tmp_profile = tempfile.mkdtemp(prefix="uc_drop_")

        # Build the Config object directly — avoids uc.start() returning None for
        # non-Chrome binaries (Edge, Brave) on certain nodriver versions.
        config = uc.Config(
            headless=True,
            browser_executable_path=chrome_bin,
            user_data_dir=tmp_profile,
            browser_args=[
                "--no-sandbox",
                "--disable-gpu",
                "--window-size=1280,900",
                "--disable-extensions",
                "--disable-dev-shm-usage",
            ],
        )
        browser = await uc.Browser.create(config)

        if browser is None:
            result.error = (
                "nodriver: Browser.create() returned None. "
                "The browser binary may be incompatible. Try --browser-path to point "
                "at a different Chrome/Chromium build."
            )
            return result

        page = await browser.get(url)

        # JS string uses raw string (r""") to avoid Python SyntaxWarning on \d etc.
        JS_EXTRACT = r"""
            (() => {
                const divs = document.querySelectorAll('.domain-info-text');
                for (const div of divs) {
                    const spans = div.querySelectorAll('span');
                    for (let i = 0; i < spans.length; i++) {
                        if (spans[i].innerText.includes('Drop Time') && spans[i+1]) {
                            return spans[i+1].innerText.trim();
                        }
                    }
                }
                const body = document.body.innerText;
                const m = body.match(/(\d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}\s*(?:PST|PDT|UTC))/);
                return m ? m[1] : null;
            })()
        """

        drop_text: Optional[str] = None
        for attempt in range(60):  # poll up to 30s
            await asyncio.sleep(0.5)
            try:
                js_result = await page.evaluate(JS_EXTRACT)
                if js_result and re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}", js_result):
                    drop_text = js_result
                    break
            except Exception:
                pass

        await browser.stop()

        if drop_text:
            dt = _parse_dynadot_dt(drop_text)
            result.set_dt(dt, "nodriver / Browser (Dynadot)", "exact", drop_text)
        else:
            result.error = (
                "nodriver: page loaded but drop-time element not found. "
                "Dynadot may have changed their DOM structure."
            )

    except ImportError:
        result.error = "nodriver not installed -- run: pip install nodriver"
    except Exception as e:
        result.error = f"nodriver exception: {type(e).__name__}: {e}"
    finally:
        # Clean up temp profile
        if tmp_profile and Path(tmp_profile).exists():
            try:
                shutil.rmtree(tmp_profile, ignore_errors=True)
                print(f"      cleaned up temp profile {tmp_profile}", flush=True)
            except Exception:
                pass
    return result


# =============================================================================
# Technique 2 - curl-cffi (TLS/JA3 impersonation, Chrome 124 fingerprint)
# =============================================================================

def _fetch_curl_cffi(domain: str) -> DropResult:
    """
    Chrome 124 TLS fingerprint impersonation.
    Blocked by Dynadot's Cloudflare Managed Challenge today (requires JS execution).
    Included for future use / other registrar sites.
    """
    result = DropResult(domain)
    try:
        from curl_cffi import requests as cffi_req  # pip install curl-cffi

        url = (
            f"https://www.dynadot.com/market/backorder/{domain}"
            f"?rscbo=expireddomains"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.expireddomains.net/",
        }
        resp = cffi_req.get(url, headers=headers, impersonate="chrome124", timeout=20, allow_redirects=True)

        if resp.status_code == 403 or "Just a moment" in resp.text:
            result.error = (
                "curl-cffi: Cloudflare Managed Challenge active -- "
                "JS execution required. Use nodriver instead."
            )
            return result

        html = resp.text
        nuxt_m = re.search(r'"dropTime"\s*:\s*"([^"]+)"', html)
        if nuxt_m:
            dt = _parse_dynadot_dt(nuxt_m.group(1))
            result.set_dt(dt, "curl-cffi / TLS impersonation (Dynadot)", "exact", nuxt_m.group(1))
            return result

        raw_m = re.search(r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}\s*(?:PST|PDT|UTC|GMT))", html)
        if raw_m:
            dt = _parse_dynadot_dt(raw_m.group(1))
            result.set_dt(dt, "curl-cffi / TLS impersonation (Dynadot)", "exact", raw_m.group(1))
        else:
            result.error = "curl-cffi: bypassed CF but drop time not in HTML (SPA not server-rendered)"

    except ImportError:
        result.error = "curl-cffi not installed -- run: pip install curl-cffi"
    except Exception as e:
        result.error = f"curl-cffi exception: {type(e).__name__}: {e}"
    return result


# =============================================================================
# Technique 3 - RDAP (IANA public API, estimated result)
# =============================================================================

def _fetch_rdap(domain: str) -> DropResult:
    """
    IANA RDAP bootstrap -> authoritative RDAP server -> pendingDelete event date.

    ACCURACY NOTE:
    Verisign (.com/.net) does NOT publish a dedicated pendingDelete event in RDAP.
    Only 'last changed' is available, which is when the registrar submitted the
    delete -- not when pendingDelete officially started. This means the estimate
    can be off by 1-3 days. For exact times, use nodriver (Technique 1).
    """
    result = DropResult(domain)
    try:
        tld = domain.rsplit(".", 1)[-1].lower()

        with urllib.request.urlopen("https://data.iana.org/rdap/dns.json", timeout=10) as r:
            bootstrap = json.loads(r.read())

        rdap_base: Optional[str] = None
        for entry in bootstrap.get("services", []):
            if tld in [t.lower() for t in entry[0]]:
                rdap_base = entry[1][0].rstrip("/")
                break

        if not rdap_base:
            result.error = f"RDAP: no server registered for .{tld} in IANA bootstrap"
            return result

        req = urllib.request.Request(
            f"{rdap_base}/domain/{domain}",
            headers={"Accept": "application/rdap+json, application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())

        status     = [s.lower() for s in data.get("status", [])]
        is_pending = any("pending" in s for s in status)

        pending_dt: Optional[datetime] = None
        expiry_dt:  Optional[datetime] = None
        updated_dt: Optional[datetime] = None

        for ev in data.get("events", []):
            action = ev.get("eventAction", "").lower()
            raw_dt = ev.get("eventDate", "")
            if not raw_dt:
                continue
            try:
                dt_parsed = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
            except ValueError:
                continue
            if "pending" in action or "deletion" in action:
                pending_dt = dt_parsed
            elif action in ("expiration", "expiry", "expires"):
                expiry_dt = dt_parsed
            elif action in ("last changed", "last update", "updated"):
                updated_dt = dt_parsed

        days, hour, minute = TLD_DROP_WINDOWS.get(tld, DEFAULT_DROP_WINDOW)

        if pending_dt:
            drop_day = (pending_dt + timedelta(days=days)).date()
            drop_dt  = datetime(drop_day.year, drop_day.month, drop_day.day, hour, minute, 0, tzinfo=UTC)
            result.set_dt(drop_dt, "RDAP pendingDelete event + TLD window", "estimated",
                          f"pendingDelete entered: {pending_dt.isoformat()} -> +{days}d ~{hour:02d}:{minute:02d} UTC")

        elif is_pending and updated_dt:
            drop_day = (updated_dt + timedelta(days=days)).date()
            drop_dt  = datetime(drop_day.year, drop_day.month, drop_day.day, hour, minute, 0, tzinfo=UTC)
            result.set_dt(
                drop_dt,
                "RDAP last-changed + TLD window  ⚠ LOW ACCURACY",
                "estimated",
                f"last-updated: {updated_dt.isoformat()} -> +{days}d ~{hour:02d}:{minute:02d} UTC  "
                f"[WARNING: Verisign does not expose pendingDelete date in RDAP. "
                f"'last-changed' = registrar-delete timestamp, NOT pendingDelete entry. "
                f"Actual drop may be 1-3 days later than shown. Use nodriver for exact time.]",
            )

        elif is_pending and expiry_dt:
            pending_start = expiry_dt + timedelta(days=35)
            drop_day      = (pending_start + timedelta(days=days)).date()
            drop_dt       = datetime(drop_day.year, drop_day.month, drop_day.day, hour, minute, 0, tzinfo=UTC)
            result.set_dt(drop_dt, "RDAP expiry estimate + TLD window", "estimated",
                          f"expiry: {expiry_dt.isoformat()} -> +35d redemption -> +{days}d")

        else:
            result.error = f"RDAP: domain found, status={status}, but no usable date event available"

    except urllib.error.HTTPError as e:
        result.error = (
            "RDAP: domain not found (already dropped or not in pendingDelete)"
            if e.code == 404 else f"RDAP HTTP {e.code}: {e.reason}"
        )
    except Exception as e:
        result.error = f"RDAP exception: {type(e).__name__}: {e}"
    return result


# =============================================================================
# Technique 4 - Raw WHOIS socket + TLD drop window
# =============================================================================

def _fetch_whois(domain: str) -> DropResult:
    result = DropResult(domain)
    try:
        tld        = domain.rsplit(".", 1)[-1].lower()
        whois_host = WHOIS_SERVERS.get(tld, f"whois.nic.{tld}")

        with socket.create_connection((whois_host, 43), timeout=12) as sock:
            sock.sendall(f"{domain}\r\n".encode("ascii"))
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        whois_text = b"".join(chunks).decode("utf-8", errors="replace")

        updated_dt: Optional[datetime] = None
        for line in whois_text.splitlines():
            if any(k in line.lower() for k in ("updated date", "last-updated", "last updated")):
                m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+\-]\d{2}:\d{2})?)", line)
                if not m:
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                if m:
                    raw_s = m.group(1)
                    updated_dt = datetime.fromisoformat(raw_s.replace("Z", "+00:00"))
                    if updated_dt.tzinfo is None:
                        updated_dt = updated_dt.replace(tzinfo=UTC)
                    break

        if not updated_dt:
            result.error = f"WHOIS ({whois_host}): could not extract Updated Date"
            return result

        days, hour, minute = TLD_DROP_WINDOWS.get(tld, DEFAULT_DROP_WINDOW)
        drop_day = (updated_dt + timedelta(days=days)).date()
        drop_dt  = datetime(drop_day.year, drop_day.month, drop_day.day, hour, minute, 0, tzinfo=UTC)
        result.set_dt(
            drop_dt,
            f"WHOIS ({whois_host}) + TLD window",
            "estimated",
            f"Updated Date: {updated_dt.date().isoformat()} -> +{days}d ~{hour:02d}:{minute:02d} UTC  "
            f"[WARNING: may be off by 1-3 days]",
        )

    except socket.gaierror as e:
        result.error = f"WHOIS: DNS resolution failed: {e}"
    except socket.timeout:
        result.error = "WHOIS: connection timed out"
    except Exception as e:
        result.error = f"WHOIS exception: {type(e).__name__}: {e}"
    return result


# =============================================================================
# Auto-orchestrator
# =============================================================================

async def get_drop_time(
    domain: str,
    technique: str = "auto",
    browser_path: Optional[str] = None,
) -> DropResult:
    domain = _normalize_domain(domain)

    if technique == "nodriver":  return await _fetch_nodriver(domain, browser_path)
    if technique == "curl_cffi": return _fetch_curl_cffi(domain)
    if technique == "rdap":      return _fetch_rdap(domain)
    if technique == "whois":     return _fetch_whois(domain)

    print(f"    +- [1/4] nodriver (Chrome + Cloudflare bypass)...", flush=True)
    r = await _fetch_nodriver(domain, browser_path)
    if r.drop_dt_utc:
        print(f"    +- success")
        return r
    print(f"    |     x {r.error}")

    for label, fn in [
        ("[2/4] curl-cffi (TLS impersonation)", _fetch_curl_cffi),
        ("[3/4] RDAP (IANA public API)",         _fetch_rdap),
        ("[4/4] WHOIS (raw socket)",              _fetch_whois),
    ]:
        print(f"    +- {label}...", flush=True)
        r2 = fn(domain)
        if r2.drop_dt_utc:
            print(f"    +- success")
            return r2
        print(f"    |     x {r2.error}")

    r.error = "All four techniques failed."
    return r


# =============================================================================
# CLI
# =============================================================================

async def _main() -> None:
    parser = argparse.ArgumentParser(
        prog="drop_time_scraper",
        description="Fetch exact/estimated drop time for pending-delete domains.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python drop_time_scraper.py zenithpicks.com\n"
            "  python drop_time_scraper.py --technique rdap zenithpicks.com\n"
            '  python drop_time_scraper.py --browser-path "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" zenithpicks.com\n'
        ),
    )
    parser.add_argument("domains", nargs="+", metavar="DOMAIN")
    parser.add_argument(
        "--technique", "-t",
        choices=["auto", "nodriver", "curl_cffi", "rdap", "whois"],
        default="auto",
    )
    parser.add_argument(
        "--browser-path", "-b",
        metavar="PATH",
        default=None,
        help=(
            'Path to Chrome/Chromium/Edge/Brave executable. '
            'Also reads CHROME_PATH env var. '
            'Example: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"'
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results: list[DropResult] = []
    for domain in args.domains:
        print(f"\nLooking up: {domain}")
        r = await get_drop_time(domain, args.technique, args.browser_path)
        results.append(r)

    print("\n" + "=" * 62)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2, default=str))
    else:
        for r in results:
            print(r.display())
            print()


if __name__ == "__main__":
    asyncio.run(_main())
