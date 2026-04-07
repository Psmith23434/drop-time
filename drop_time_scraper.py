#!/usr/bin/env python3
"""
drop_time_scraper.py
====================
Fetches the EXACT drop time for pending-delete domains from two sources:

  Source 1 - Dynadot backorder page  (primary)
             URL  : https://www.dynadot.com/market/backorder/{domain}
             HTML : <div class="domain-info-text">
                      <span style="opacity:0.4">Drop Time:</span>
                      <span style="font-size:24px">2026/04/08 10:45 PST</span>
                    </div>
             Note : Cloudflare challenge fires on first visit -- a checkbox
                    CAPTCHA appears; click it once.  The persistent browser
                    profile remembers the CF cookie so subsequent runs are
                    instant (no challenge).

  Source 2 - ExpiredDomains.net per-domain detail page  (fallback)
             URL  : https://member.expireddomains.net/domain/{domain}
             HTML : <table class="base1 small listing">
                      <tr><td>Added to List</td><td>2026-04-04</td></tr>
                      <tr>
                        <td>End Date</td>
                        <td><a class="verified1">2026-04-08</a></td>
                      </tr>
                    </table>
             Note : Requires an ExpiredDomains.net account.  Log in once in
                    the browser window that opens -- the persistent profile
                    keeps you logged in for future runs.

Both sources give exact drop times sourced from registrar data.

Usage:
  python drop_time_scraper.py zenithpicks.com reviewindex.com
  python drop_time_scraper.py --source dynadot zenithpicks.com
  python drop_time_scraper.py --source expireddomains zenithpicks.com
  python drop_time_scraper.py --json zenithpicks.com
  python drop_time_scraper.py --browser-path "C:\\path\\to\\msedge.exe" zenithpicks.com

Notes on Edge + nodriver:
  - headless=False is used intentionally.  Cloudflare (Dynadot) and
    ExpiredDomains.net both detect --headless mode even with nodriver.
    The browser window opens minimised (1x1 px) and closes when done.
  - Persistent profile location:
      Windows : %%APPDATA%%\\DropTimeSniper\\browser_profile
      macOS   : ~/Library/Application Support/DropTimeSniper/browser_profile
      Linux   : ~/.config/DropTimeSniper/browser_profile
"""

from __future__ import annotations
import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

PST = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Logging helpers  (source-tagged, timestamped)
# ---------------------------------------------------------------------------

def _log(tag: str, msg: str) -> None:
    """Print a timestamped, source-tagged log line to stdout."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{tag}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Persistent browser profile  (survives CF challenges between runs)
# ---------------------------------------------------------------------------

def _profile_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "DropTimeSniper" / "browser_profile"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Browser binary auto-detection
# ---------------------------------------------------------------------------

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


def _find_chrome_binary(user_path: Optional[str] = None) -> Optional[str]:
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


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

class DropResult:
    def __init__(self, domain: str):
        self.domain      : str                = domain.lower().strip()
        self.drop_dt_utc : Optional[datetime] = None
        self.drop_dt_pst : Optional[datetime] = None
        self.raw_text    : Optional[str]       = None
        self.source      : Optional[str]       = None
        self.confidence  : str                = "unknown"
        self.error       : Optional[str]       = None

    def set_dt(self, dt: datetime, source: str, confidence: str, raw: str = "") -> None:
        self.source     = source
        self.confidence = confidence
        self.raw_text   = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        self.drop_dt_utc = dt.astimezone(UTC)
        self.drop_dt_pst = dt.astimezone(PST)

    def display(self) -> str:
        icon = {"exact": "[EXACT]", "estimated": "[EST] ", "failed": "[FAIL]",
                "unknown": "[ ?? ]"}.get(self.confidence, "[ ?? ]")
        if self.drop_dt_utc:
            utc_s = self.drop_dt_utc.strftime("%Y-%m-%d %H:%M UTC")
            pst_s = self.drop_dt_pst.strftime("%Y-%m-%d %H:%M PST")  # type: ignore[union-attr]
            return (
                f"{icon}  {self.domain}\n"
                f"   Drop Time  : {utc_s}  ({pst_s})\n"
                f"   Confidence : {self.confidence.upper()}\n"
                f"   Source     : {self.source}\n"
                f"   Raw        : {self.raw_text or '-'}"
            )
        return f"[FAIL]  {self.domain}  --  {self.error}"

    def to_dict(self) -> dict:
        return {
            "domain":     self.domain,
            "drop_utc":   self.drop_dt_utc.isoformat() if self.drop_dt_utc else None,
            "drop_pst":   self.drop_dt_pst.isoformat() if self.drop_dt_pst else None,
            "confidence": self.confidence,
            "source":     self.source,
            "raw":        self.raw_text,
            "error":      self.error,
        }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalize_domain(domain: str) -> str:
    domain = domain.lower().strip()
    domain = re.sub(r"^https?://", "", domain)
    return domain.split("/")[0].split("?")[0]


def _parse_dt(raw: str) -> datetime:
    """
    Parse drop-time strings from both sources.
      Dynadot        : '2026/04/08 10:45 PST'
      ExpiredDomains : '2026-04-08'  or  '2026-04-08 10:45:00 UTC'
    """
    raw = raw.strip()
    m = re.search(
        r"(\d{4})[/\-](\d{2})[/\-](\d{2})"
        r"(?:[T\s]+(\d{2}):(\d{2})(?::(\d{2}))?)?"
        r"(?:\s*(PST|PDT|UTC|GMT))?",
        raw, re.IGNORECASE,
    )
    if not m:
        raise ValueError(f"Cannot parse datetime: {raw!r}")
    Y, Mo, D = int(m.group(1)), int(m.group(2)), int(m.group(3))
    h  = int(m.group(4)) if m.group(4) else 0
    mi = int(m.group(5)) if m.group(5) else 0
    tz_str = (m.group(7) or "UTC").upper()
    tz_map = {
        "PST": ZoneInfo("America/Los_Angeles"),
        "PDT": ZoneInfo("America/Los_Angeles"),
        "UTC": UTC,
        "GMT": UTC,
    }
    return datetime(Y, Mo, D, h, mi, 0, tzinfo=tz_map.get(tz_str, UTC))


async def _make_browser(chrome_bin: str, profile_dir: str):
    """
    Launch a nodriver browser.

    headless=False is intentional:
      - Cloudflare (Dynadot) detects headless even with nodriver on Edge.
      - ExpiredDomains.net also trips on headless.
      - Window opens at 1x1 px (bottom-left corner) and closes when done.

    Persistent profile: CF challenge cookies and ED login survive between runs.
    """
    import nodriver as uc
    config = uc.Config(
        headless=False,
        browser_executable_path=chrome_bin,
        user_data_dir=profile_dir,
        browser_args=[
            "--no-sandbox",
            "--disable-gpu",
            "--window-size=1,1",
            "--window-position=0,0",
            "--disable-extensions",
            "--disable-dev-shm-usage",
        ],
    )
    return await uc.Browser.create(config)


async def _safe_get(browser, url: str, retries: int = 2):
    """browser.get() on Edge can return None on the first call; retry."""
    for attempt in range(retries + 1):
        page = await browser.get(url)
        if page is not None:
            return page
        if attempt < retries:
            await asyncio.sleep(3)
    return None


# ---------------------------------------------------------------------------
# Source 1 -- Dynadot backorder page
# ---------------------------------------------------------------------------
#
# Confirmed HTML:
#   <div class="domain-info-text" data-v-5ee84fcc="">
#     <span style="opacity:0.4;" data-v-5ee84fcc="">Drop Time:</span>
#     <span style="font-size:24px;" data-v-5ee84fcc="">2026/04/08 10:45 PST</span>
#   </div>
#
# The Vue data-v-* scoped attributes don't affect CSS class selectors.
# We find every .domain-info-text container, look for the span labelled
# 'Drop Time:' (spans[0]), and return the NEXT sibling span (spans[1]).
# ---------------------------------------------------------------------------

_DYNADOT_JS = """
(function() {
    var containers = document.querySelectorAll('.domain-info-text');
    for (var i = 0; i < containers.length; i++) {
        var spans = containers[i].querySelectorAll('span');
        if (spans.length >= 2) {
            var label = (spans[0].innerText || spans[0].textContent || '').trim();
            if (label.indexOf('Drop Time') !== -1) {
                var val = (spans[1].innerText || spans[1].textContent || '').trim();
                if (val) return val;
            }
        }
    }
    // Fallback: regex over body text
    var body = document.body ? (document.body.innerText || document.body.textContent || '') : '';
    var m = body.match(/(\\d{4}\\/\\d{2}\\/\\d{2}\\s+\\d{2}:\\d{2}\\s*(?:PST|PDT|UTC))/i);
    return m ? m[1].trim() : null;
})()
"""


async def _fetch_dynadot(domain: str, browser_path: Optional[str] = None) -> DropResult:
    """
    Loads https://www.dynadot.com/market/backorder/{domain}
    and extracts the 'Drop Time' span value.

    First-run note: Cloudflare shows a checkbox CAPTCHA.  Click it in the
    browser window that opens.  The persistent profile caches the CF cookie
    so subsequent runs skip the challenge entirely.
    """
    SOURCE = "Dynadot"
    result = DropResult(domain)
    browser = None
    try:
        import nodriver as uc  # noqa: F401

        chrome_bin = _find_chrome_binary(browser_path)
        if not chrome_bin:
            result.error = (
                "No browser found. Install Chrome/Edge/Brave or set CHROME_PATH."
            )
            _log(SOURCE, f"ERROR  {result.error}")
            return result

        _log(SOURCE, f"Browser  : {chrome_bin}")
        _log(SOURCE, f"Profile  : {_profile_dir()}")
        _log(SOURCE, f"Fetching : https://www.dynadot.com/market/backorder/{domain}")

        profile = str(_profile_dir())
        browser = await _make_browser(chrome_bin, profile)
        if browser is None:
            result.error = "Browser.create() returned None."
            _log(SOURCE, f"ERROR  {result.error}")
            return result

        url = f"https://www.dynadot.com/market/backorder/{domain}"
        page = await _safe_get(browser, url)
        if page is None:
            result.error = "browser.get() returned None after retries."
            _log(SOURCE, f"ERROR  {result.error}")
            return result

        _log(SOURCE, "Page loaded -- polling for Drop Time element (max 30s)...")
        _log(SOURCE, "  >> If a Cloudflare checkbox appears, click it in the browser window.")

        drop_text: Optional[str] = None
        for tick in range(60):      # 60 × 0.5s = 30 s
            await asyncio.sleep(0.5)
            try:
                val = await page.evaluate(_DYNADOT_JS)
                if val and re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}", str(val)):
                    drop_text = str(val).strip()
                    break
            except Exception:
                pass
            if tick > 0 and tick % 10 == 0:
                _log(SOURCE, f"  ... still waiting ({tick // 2}s elapsed)")

        if drop_text:
            dt = _parse_dt(drop_text)
            result.set_dt(dt, "Dynadot backorder page", "exact", drop_text)
            _log(SOURCE, f"SUCCESS  Raw     : {drop_text}")
            _log(SOURCE, f"         UTC     : {result.drop_dt_utc.strftime('%Y-%m-%d %H:%M UTC')}")
            _log(SOURCE, f"         PST     : {result.drop_dt_pst.strftime('%Y-%m-%d %H:%M PST')}")
            _log(SOURCE, f"         Source  : {result.source}")
        else:
            result.error = (
                "'Drop Time' element not found after 30s. "
                "Domain may not be in pendingDelete, or Dynadot changed their DOM."
            )
            _log(SOURCE, f"ERROR  {result.error}")

    except ImportError:
        result.error = "nodriver not installed -- run: pip install nodriver"
        _log(SOURCE, f"ERROR  {result.error}")
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        _log(SOURCE, f"ERROR  {result.error}")
    finally:
        if browser is not None:
            try:
                await asyncio.sleep(0.3)
                await browser.stop()
            except Exception:
                pass
    return result


# ---------------------------------------------------------------------------
# Source 2 -- ExpiredDomains.net per-domain detail page
# ---------------------------------------------------------------------------
#
# Confirmed HTML (member.expireddomains.net/domain/{domain}):
#
#   <table class="base1 small listing">
#     <tbody>
#       <tr><td>Added to List</td><td>2026-04-04</td></tr>   <- NOT this row
#       <tr>
#         <td>End Date</td>                                  <- match THIS label
#         <td><a class="verified1">2026-04-08</a></td>       <- return this text
#       </tr>
#     </tbody>
#   </table>
#
# BUG THAT WAS FIXED:
#   The old code used  label === 'End Date'  but innerText frequently has a
#   trailing newline ("End Date\n"), causing the match to fail silently.
#   The loop then fell through and the NEXT date found was "Added to List"
#   -> "2026-04-04" (the wrong date).
#
# Fix: explicitly trim() the label before comparing, AND use textContent as
# fallback in case innerText is undefined (e.g. hidden elements).
# ---------------------------------------------------------------------------

_EXPDOM_JS = """
(function() {
    // Walk every row in any table.base1 on this page
    var rows = document.querySelectorAll('table.base1 tr');
    for (var i = 0; i < rows.length; i++) {
        var cells = rows[i].querySelectorAll('td');
        if (cells.length < 2) continue;

        // FIX: trim() both innerText and textContent before comparing.
        // innerText can include trailing newlines; textContent is the fallback.
        var label = '';
        if (typeof cells[0].innerText !== 'undefined') {
            label = cells[0].innerText.trim();
        }
        if (!label && typeof cells[0].textContent !== 'undefined') {
            label = cells[0].textContent.trim();
        }

        // Only match the 'End Date' row -- not 'Added to List' or any other row
        if (label !== 'End Date') continue;

        // Prefer <a class="verified1"> -- its title says 'Date is most likely accurate'
        var link = cells[1].querySelector('a.verified1');
        if (link) {
            var linkText = '';
            if (typeof link.innerText !== 'undefined') linkText = link.innerText.trim();
            if (!linkText && typeof link.textContent !== 'undefined') linkText = link.textContent.trim();
            if (linkText) return linkText;
        }

        // Fallback: plain cell text
        var cellText = '';
        if (typeof cells[1].innerText !== 'undefined') cellText = cells[1].innerText.trim();
        if (!cellText && typeof cells[1].textContent !== 'undefined') cellText = cells[1].textContent.trim();
        return cellText || null;
    }
    return null;
})()
"""


async def _fetch_expireddomains(domain: str, browser_path: Optional[str] = None) -> DropResult:
    """
    Loads https://member.expireddomains.net/domain/{domain}
    and reads the 'End Date' row (NOT 'Added to List') from table.base1.

    Requires an ExpiredDomains.net login.  On first run the login page may
    appear -- log in manually; the persistent profile keeps you signed in.
    """
    SOURCE = "ExpiredDomains"
    result = DropResult(domain)
    browser = None
    try:
        import nodriver as uc  # noqa: F401

        chrome_bin = _find_chrome_binary(browser_path)
        if not chrome_bin:
            result.error = "No browser found. Install Chrome/Edge/Brave or set CHROME_PATH."
            _log(SOURCE, f"ERROR  {result.error}")
            return result

        _log(SOURCE, f"Browser  : {chrome_bin}")
        _log(SOURCE, f"Profile  : {_profile_dir()}")
        _log(SOURCE, f"Fetching : https://member.expireddomains.net/domain/{domain}")

        profile = str(_profile_dir())
        browser = await _make_browser(chrome_bin, profile)
        if browser is None:
            result.error = "Browser.create() returned None."
            _log(SOURCE, f"ERROR  {result.error}")
            return result

        url = f"https://member.expireddomains.net/domain/{domain}"
        page = await _safe_get(browser, url)
        if page is None:
            result.error = "browser.get() returned None after retries."
            _log(SOURCE, f"ERROR  {result.error}")
            return result

        _log(SOURCE, "Page loaded -- polling for 'End Date' row in table.base1 (max 40s)...")
        _log(SOURCE, "  >> If the login page appears, sign in to ExpiredDomains.net.")

        drop_text: Optional[str] = None
        for tick in range(80):      # 80 × 0.5s = 40 s
            await asyncio.sleep(0.5)
            try:
                val = await page.evaluate(_EXPDOM_JS)
                if val and re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}", str(val)):
                    drop_text = str(val).strip()
                    break
            except Exception:
                pass
            if tick > 0 and tick % 10 == 0:
                _log(SOURCE, f"  ... still waiting ({tick // 2}s elapsed)")

        if drop_text:
            dt = _parse_dt(drop_text)
            result.set_dt(dt, "ExpiredDomains.net detail page", "exact", drop_text)
            _log(SOURCE, f"SUCCESS  Raw     : {drop_text}")
            _log(SOURCE, f"         UTC     : {result.drop_dt_utc.strftime('%Y-%m-%d %H:%M UTC')}")
            _log(SOURCE, f"         PST     : {result.drop_dt_pst.strftime('%Y-%m-%d %H:%M PST')}")
            _log(SOURCE, f"         Source  : {result.source}")
        else:
            result.error = (
                "'End Date' row not found after 40s. "
                "Domain may not be listed, login may be needed, "
                "or the page structure changed."
            )
            _log(SOURCE, f"ERROR  {result.error}")

    except ImportError:
        result.error = "nodriver not installed -- run: pip install nodriver"
        _log(SOURCE, f"ERROR  {result.error}")
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        _log(SOURCE, f"ERROR  {result.error}")
    finally:
        if browser is not None:
            try:
                await asyncio.sleep(0.3)
                await browser.stop()
            except Exception:
                pass
    return result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def get_drop_time(
    domain: str,
    source: str = "auto",
    browser_path: Optional[str] = None,
) -> DropResult:
    """
    source='auto'           : Dynadot first, fall back to ExpiredDomains
    source='dynadot'        : Dynadot only
    source='expireddomains' : ExpiredDomains only
    """
    domain = _normalize_domain(domain)

    if source == "dynadot":
        _log("AUTO", f"Source forced: Dynadot  |  domain: {domain}")
        return await _fetch_dynadot(domain, browser_path)
    if source == "expireddomains":
        _log("AUTO", f"Source forced: ExpiredDomains  |  domain: {domain}")
        return await _fetch_expireddomains(domain, browser_path)

    _log("AUTO", f"domain: {domain}  |  trying Dynadot first...")
    r = await _fetch_dynadot(domain, browser_path)
    if r.drop_dt_utc:
        _log("AUTO", f"Final source used: {r.source}")
        return r
    _log("AUTO", f"Dynadot failed ({r.error}) -- falling back to ExpiredDomains...")

    r2 = await _fetch_expireddomains(domain, browser_path)
    if r2.drop_dt_utc:
        _log("AUTO", f"Final source used: {r2.source}")
        return r2
    _log("AUTO", "Both sources failed.")

    r.error = f"Both sources failed. Dynadot: {r.error} | ExpiredDomains: {r2.error}"
    return r


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def _main() -> None:
    parser = argparse.ArgumentParser(
        prog="drop_time_scraper",
        description="Fetch exact drop time for pending-delete domains.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python drop_time_scraper.py zenithpicks.com reviewindex.com\n"
            "  python drop_time_scraper.py --source dynadot reviewindex.com\n"
            "  python drop_time_scraper.py --source expireddomains zenithpicks.com\n"
            '  python drop_time_scraper.py --browser-path "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" zenithpicks.com\n'
        ),
    )
    parser.add_argument("domains", nargs="+", metavar="DOMAIN")
    parser.add_argument(
        "--source", "-s",
        choices=["auto", "dynadot", "expireddomains"],
        default="auto",
        help="Which source to use (default: auto = Dynadot first, then ExpiredDomains)",
    )
    parser.add_argument("--browser-path", "-b", metavar="PATH", default=None,
                        help="Path to Chrome/Edge/Brave executable")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON instead of human-readable text")
    args = parser.parse_args()

    results: list[DropResult] = []
    for domain in args.domains:
        _log("MAIN", f"{'='*54}")
        _log("MAIN", f"Looking up: {domain}")
        r = await get_drop_time(domain, args.source, args.browser_path)
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
