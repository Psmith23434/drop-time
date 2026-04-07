#!/usr/bin/env python3
"""
drop_time_scraper.py
====================
Fetches the EXACT drop time for pending-delete domains from two sources:

  Source 1 - Dynadot backorder page  (primary)
             URL  : https://www.dynadot.com/market/backorder/{domain}
             HTML : <div class="domain-info-text">
                      <span ...>Drop Time:</span>
                      <span style="font-size:24px;">2026/04/08 10:45 PST</span>
                    </div>
             Selector: .domain-info-text span:nth-of-type(2)  (second span = the value)

  Source 2 - ExpiredDomains.net per-domain detail page  (fallback)
             URL  : https://member.expireddomains.net/domain/{domain}
             HTML : <table class="base1 small listing">
                      <tr><td>End Date</td>
                          <td><a class="verified1">2026-04-08</a></td></tr>
                    </table>
             Selector: find <tr> whose first <td> = "End Date",
                       then read the <a class="verified1"> (or bare <td>) in that row.

Both sources give exact drop times sourced from registrar data.
At 5 domains/day, both are well within polite usage limits.

Usage:
  python drop_time_scraper.py zenithpicks.com reviewindex.com
  python drop_time_scraper.py --source dynadot zenithpicks.com
  python drop_time_scraper.py --source expireddomains zenithpicks.com
  python drop_time_scraper.py --json zenithpicks.com
  python drop_time_scraper.py --browser-path "C:\\path\\to\\msedge.exe" zenithpicks.com

Chrome binary auto-detection order:
  1. --browser-path CLI argument
  2. CHROME_PATH environment variable
  3. Common Windows paths (Chrome, Edge, Brave, Chromium)
  4. Common macOS / Linux paths
  5. PATH lookup

Notes on Edge + nodriver:
  - headless=False is used intentionally.  Cloudflare (Dynadot) and
    ExpiredDomains.net both detect --headless mode even with nodriver.
    The browser window opens minimised and closes when done.
  - A persistent profile dir is used so Cloudflare challenge cookies
    survive between runs.  Location: %APPDATA%/DropTimeSniper/profile
    (Windows) or ~/.config/DropTimeSniper/profile (Linux/macOS).
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
        icon = {"exact": "[EXACT]", "estimated": "[EST] ", "failed": "[FAIL]"}.get(
            self.confidence, "[ ?? ]"
        )
        if self.drop_dt_utc:
            utc_s = self.drop_dt_utc.strftime("%Y-%m-%d %H:%M UTC")
            pst_s = self.drop_dt_pst.strftime("%Y-%m-%d %H:%M PST")
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
      - Cloudflare (used by Dynadot) detects headless even with nodriver on Edge.
      - ExpiredDomains.net has its own bot check that also trips on headless.
      - With headless=False the window opens minimised and closes when done.

    A persistent profile is used so CF challenge cookies survive between runs.
    """
    import nodriver as uc
    config = uc.Config(
        headless=False,                          # must be False for CF + ED
        browser_executable_path=chrome_bin,
        user_data_dir=profile_dir,               # persistent, not tempfile
        browser_args=[
            "--no-sandbox",
            "--disable-gpu",
            "--window-size=1,1",                 # minimised - 1x1 px window
            "--window-position=0,0",
            "--disable-extensions",
            "--disable-dev-shm-usage",
        ],
    )
    return await uc.Browser.create(config)


async def _safe_get(browser, url: str, retries: int = 2):
    """
    browser.get() on Edge sometimes returns None on the first call.
    Retry up to `retries` times with a short back-off.
    """
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
# Exact HTML (confirmed):
#   <div class="domain-info-text" data-v-5ee84fcc="">
#     <span style="opacity:0.4;" data-v-5ee84fcc="">Drop Time:</span>
#     <span style="font-size:24px;" data-v-5ee84fcc="">2026/04/08 10:45 PST</span>
#   </div>
#
# Strategy:
#   1. querySelector('.domain-info-text')  -- finds the container div
#      Note: Vue scoped data-v-* attributes don't affect CSS class selectors.
#   2. Within that div, grab the SECOND span (index 1) -- that is always the value.
#   3. Regex fallback over body text if the selector misses (e.g. DOM change).
# ---------------------------------------------------------------------------

# JS injected into the Dynadot page
_DYNADOT_JS = """
(function() {
    // Primary: .domain-info-text  second <span> is always the drop-time value
    var containers = document.querySelectorAll('.domain-info-text');
    for (var i = 0; i < containers.length; i++) {
        var spans = containers[i].querySelectorAll('span');
        // spans[0] = label ("Drop Time:"), spans[1] = value ("2026/04/08 10:45 PST")
        if (spans.length >= 2) {
            var label = spans[0].innerText || spans[0].textContent || '';
            if (label.indexOf('Drop Time') !== -1) {
                var val = (spans[1].innerText || spans[1].textContent || '').trim();
                if (val) return val;
            }
        }
    }
    // Fallback: regex over entire visible body text
    var bodyText = document.body ? (document.body.innerText || document.body.textContent || '') : '';
    var m = bodyText.match(/(\\d{4}\\/\\d{2}\\/\\d{2}\\s+\\d{2}:\\d{2}\\s*(?:PST|PDT|UTC))/i);
    return m ? m[1].trim() : null;
})()
"""


async def _fetch_dynadot(domain: str, browser_path: Optional[str] = None) -> DropResult:
    """
    Loads https://www.dynadot.com/market/backorder/{domain}
    and reads the 'Drop Time' span.
    """
    result = DropResult(domain)
    browser = None
    try:
        import nodriver as uc  # noqa: F401

        chrome_bin = _find_chrome_binary(browser_path)
        if not chrome_bin:
            result.error = (
                "No browser found. Install Chrome/Edge/Brave or set "
                "CHROME_PATH env var."
            )
            return result

        print(f"      Browser : {chrome_bin}", flush=True)
        print("      Launching (Dynadot)...", flush=True)

        profile = str(_profile_dir())
        browser = await _make_browser(chrome_bin, profile)
        if browser is None:
            result.error = "Browser.create() returned None -- binary may be unsupported."
            return result

        # Correct URL: no query string required
        url = f"https://www.dynadot.com/market/backorder/{domain}"
        page = await _safe_get(browser, url)
        if page is None:
            result.error = (
                "browser.get() returned None after retries. "
                "Edge may need a warm-up -- try again or use --browser-path chrome.exe"
            )
            return result

        drop_text: Optional[str] = None
        # Poll up to 30 s (page may need to pass CF challenge first)
        for _ in range(60):
            await asyncio.sleep(0.5)
            try:
                val = await page.evaluate(_DYNADOT_JS)
                if val and re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}", str(val)):
                    drop_text = str(val).strip()
                    break
            except Exception:
                pass

        if drop_text:
            dt = _parse_dt(drop_text)
            result.set_dt(dt, "Dynadot backorder page", "exact", drop_text)
        else:
            result.error = (
                "Dynadot: page loaded but 'Drop Time' not found. "
                "Domain may not be in pendingDelete, or Dynadot changed their DOM."
            )

    except ImportError:
        result.error = "nodriver not installed -- run: pip install nodriver"
    except Exception as e:
        result.error = f"Dynadot error: {type(e).__name__}: {e}"
    finally:
        if browser is not None:
            try:
                await asyncio.sleep(0.3)   # let nodriver finish cleanup
                await browser.stop()
            except Exception:
                pass
    return result


# ---------------------------------------------------------------------------
# Source 2 -- ExpiredDomains.net per-domain detail page
# ---------------------------------------------------------------------------
#
# Exact HTML (confirmed):
#   URL: https://member.expireddomains.net/domain/{domain}
#
#   <table class="base1 small listing">
#     <tbody>
#       <tr><td>Added to List</td><td>2026-04-04</td></tr>
#       <tr>
#         <td>End Date</td>
#         <td><a href="..." class="verified1" title="Date is most likely accurate">2026-04-08</a></td>
#       </tr>
#       <tr><td>Backorder</td><td>...</td></tr>
#     </tbody>
#   </table>
#
# Strategy:
#   Walk all <tr> elements in table.base1.
#   Find the row where the first <td> innerText == "End Date".
#   In that row, return the text of the second <td>
#   (either the <a class="verified1"> text or plain td text).
# ---------------------------------------------------------------------------

_EXPDOM_JS = """
(function() {
    var rows = document.querySelectorAll('table.base1 tr');
    for (var i = 0; i < rows.length; i++) {
        var cells = rows[i].querySelectorAll('td');
        if (cells.length < 2) continue;
        var label = (cells[0].innerText || cells[0].textContent || '').trim();
        if (label === 'End Date') {
            // Prefer the <a class="verified1"> text -- it's the confirmed date
            var link = cells[1].querySelector('a.verified1');
            if (link) {
                return (link.innerText || link.textContent || '').trim();
            }
            // Fallback: any text in the cell
            return (cells[1].innerText || cells[1].textContent || '').trim();
        }
    }
    return null;
})()
"""


async def _fetch_expireddomains(domain: str, browser_path: Optional[str] = None) -> DropResult:
    """
    Loads https://member.expireddomains.net/domain/{domain}
    and reads the 'End Date' row from the detail table.
    """
    result = DropResult(domain)
    browser = None
    try:
        import nodriver as uc  # noqa: F401

        chrome_bin = _find_chrome_binary(browser_path)
        if not chrome_bin:
            result.error = (
                "No browser found. Install Chrome/Edge/Brave or set "
                "CHROME_PATH env var."
            )
            return result

        print(f"      Browser : {chrome_bin}", flush=True)
        print("      Launching (ExpiredDomains)...", flush=True)

        profile = str(_profile_dir())
        browser = await _make_browser(chrome_bin, profile)
        if browser is None:
            result.error = "Browser.create() returned None -- binary may be unsupported."
            return result

        # Correct URL: per-domain detail page on member. subdomain
        url = f"https://member.expireddomains.net/domain/{domain}"
        page = await _safe_get(browser, url)
        if page is None:
            result.error = "browser.get() returned None after retries."
            return result

        drop_text: Optional[str] = None
        # Poll up to 40 s -- ExpiredDomains can be slow + may need login prompt
        for _ in range(80):
            await asyncio.sleep(0.5)
            try:
                val = await page.evaluate(_EXPDOM_JS)
                if val and re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}", str(val)):
                    drop_text = str(val).strip()
                    break
            except Exception:
                pass

        if drop_text:
            dt = _parse_dt(drop_text)
            result.set_dt(dt, "ExpiredDomains.net detail page", "exact", drop_text)
        else:
            result.error = (
                "ExpiredDomains.net: 'End Date' row not found. "
                "Domain may not be listed, you may need to log in, "
                "or the page structure changed."
            )

    except ImportError:
        result.error = "nodriver not installed -- run: pip install nodriver"
    except Exception as e:
        result.error = f"ExpiredDomains.net error: {type(e).__name__}: {e}"
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
    source='auto'           : Dynadot first, fall back to ExpiredDomains.net
    source='dynadot'        : Dynadot only
    source='expireddomains' : ExpiredDomains.net only
    """
    domain = _normalize_domain(domain)

    if source == "dynadot":
        return await _fetch_dynadot(domain, browser_path)
    if source == "expireddomains":
        return await _fetch_expireddomains(domain, browser_path)

    print("    +- [1/2] Dynadot backorder page...", flush=True)
    r = await _fetch_dynadot(domain, browser_path)
    if r.drop_dt_utc:
        print("    +- success")
        return r
    print(f"    |     x {r.error}")

    print("    +- [2/2] ExpiredDomains.net detail page...", flush=True)
    r2 = await _fetch_expireddomains(domain, browser_path)
    if r2.drop_dt_utc:
        print("    +- success")
        return r2
    print(f"    |     x {r2.error}")

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
            "  python drop_time_scraper.py --source expireddomains zenithpicks.com\n"
            '  python drop_time_scraper.py --browser-path "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" zenithpicks.com\n'
        ),
    )
    parser.add_argument("domains", nargs="+", metavar="DOMAIN")
    parser.add_argument(
        "--source", "-s",
        choices=["auto", "dynadot", "expireddomains"],
        default="auto",
    )
    parser.add_argument("--browser-path", "-b", metavar="PATH", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results: list[DropResult] = []
    for domain in args.domains:
        print(f"\nLooking up: {domain}")
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
