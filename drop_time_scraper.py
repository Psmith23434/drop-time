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
                        <td>
                          <a class="verified1"
                             title="Date is most likely accurate"
                             href="...">2026-04-09</a>
                        </td>
                      </tr>
                    </table>

             Detection strategy (two-tier, fastest wins):

               Tier 1 -- a.verified1  (fastest, most reliable)
                 Any <a class="verified1"> anywhere on the page whose text
                 looks like a date is unambiguously the registrar-confirmed
                 end date.  ExpiredDomains only stamps this class on ONE
                 element per page.  Accepted immediately as soon as the
                 element is present in the DOM.

               Tier 2 -- "End Date" row label  (fallback)
                 If a.verified1 is absent (some domains show the date
                 without the verified badge), fall back to scanning
                 table.base1 rows for the label "End Date" exactly.

             Note : Requires an ExpiredDomains.net account.  Log in once in
                    the browser window that opens -- the persistent profile
                    keeps you logged in for future runs.

Both sources give exact drop times sourced from registrar data.

Usage:
  python drop_time_scraper.py zenithpicks.com reviewindex.com
  python drop_time_scraper.py --source dynadot zenithpicks.com
  python drop_time_scraper.py --source expireddomains zenithpicks.com
  python drop_time_scraper.py --json zenithpicks.com
  python drop_time_scraper.py --debug zenithpicks.com
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

# Global debug flag -- set by --debug CLI arg
DEBUG = False


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log(tag: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{tag}] {msg}", flush=True)

def _dbg(tag: str, msg: str) -> None:
    if DEBUG:
        _log(tag + "/DBG", msg)


# ---------------------------------------------------------------------------
# Persistent browser profile
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
    for attempt in range(retries + 1):
        page = await browser.get(url)
        if page is not None:
            return page
        if attempt < retries:
            await asyncio.sleep(3)
    return None


# ---------------------------------------------------------------------------
# Source 1 -- Dynadot
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
    var body = document.body ? (document.body.innerText || document.body.textContent || '') : '';
    var m = body.match(/(\\d{4}\\/\\d{2}\\/\\d{2}\\s+\\d{2}:\\d{2}\\s*(?:PST|PDT|UTC))/i);
    return m ? m[1].trim() : null;
})()
"""


async def _fetch_dynadot(domain: str, browser_path: Optional[str] = None) -> DropResult:
    SOURCE = "Dynadot"
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
        _log(SOURCE, f"Fetching : https://www.dynadot.com/market/backorder/{domain}")

        browser = await _make_browser(chrome_bin, str(_profile_dir()))
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
        for tick in range(60):
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
# Source 2 -- ExpiredDomains.net
# ---------------------------------------------------------------------------
#
# DETECTION STRATEGY -- two-tier, fastest wins:
#
#   Tier 1  a.verified1  (primary)
#   -------
#   <a class="verified1" title="Date is most likely accurate">2026-04-09</a>
#
#   ExpiredDomains stamps class="verified1" on exactly ONE anchor per page --
#   the registrar-confirmed end date.  It is unambiguous: no label matching,
#   no row-position assumptions.  As soon as this element appears in the DOM
#   we accept its text and stop polling.  This is also faster than waiting for
#   the full table to render because the verified anchor tends to be injected
#   by the same XHR that populates the End Date row.
#
#   Tier 2  "End Date" row label  (fallback)
#   -------
#   If a.verified1 is absent (some domains show the plain date without the
#   badge), we scan table.base1 rows for the label "End Date" (trimmed,
#   exact match).  This was the previous strategy and is kept as a safety net.
#
#   PREVIOUS BUG (now fixed by Tier 1):
#   The page renders in two DOM stages:
#     Stage 1 (fast): table appears with only "Added to List: 2026-04-04"
#     Stage 2 (slow): "End Date / a.verified1" row injected by a second XHR
#   Old code accepted the first date-shaped string in table.base1 -- always
#   the wrong "Added to List" date from Stage 1.
#   Tier 1 is immune: a.verified1 does not exist until Stage 2 completes.
# ---------------------------------------------------------------------------

# Returns JSON: { verified: str|null, endDateRow: str|null, rows: [[label,val],...] }
_EXPDOM_JS = """
(function() {
    var out = { verified: null, endDateRow: null, rows: [] };

    // --- Tier 1: a.verified1 anywhere on the page -----------------------
    // title="Date is most likely accurate" -- unambiguous registrar date.
    // No row-label matching needed; ED uses this class on exactly one anchor.
    var links = document.querySelectorAll('a.verified1');
    for (var v = 0; v < links.length; v++) {
        var txt = (links[v].innerText || links[v].textContent || '').trim();
        // Must look like a date (YYYY-MM-DD or YYYY/MM/DD)
        if (/\\d{4}[\\/-]\\d{2}[\\/-]\\d{2}/.test(txt)) {
            out.verified = txt;
            break;   // only one per page -- no need to keep scanning
        }
    }

    // --- Tier 2: End Date row in table.base1 (fallback) -----------------
    var rows = document.querySelectorAll('table.base1 tr');
    for (var i = 0; i < rows.length; i++) {
        var cells = rows[i].querySelectorAll('td');
        if (cells.length < 2) continue;
        var label  = (cells[0].innerText || cells[0].textContent || '').trim();
        var rawVal = (cells[1].innerText || cells[1].textContent || '').trim();
        out.rows.push([label, rawVal.substring(0, 40)]);
        if (label === 'End Date' && rawVal) {
            out.endDateRow = rawVal;
        }
    }

    return JSON.stringify(out);
})()
"""


async def _fetch_expireddomains(domain: str, browser_path: Optional[str] = None) -> DropResult:
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

        browser = await _make_browser(chrome_bin, str(_profile_dir()))
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

        _log(SOURCE, "Page loaded -- waiting for a.verified1 or 'End Date' row (max 40s)...")
        _log(SOURCE, "  >> If the login page appears, sign in to ExpiredDomains.net.")

        drop_text  : Optional[str] = None
        drop_method: str           = "unknown"
        last_rows  : list          = []

        for tick in range(80):      # 80 x 0.5s = 40s
            await asyncio.sleep(0.5)
            try:
                raw_json = await page.evaluate(_EXPDOM_JS)
                if not raw_json:
                    continue
                data = json.loads(str(raw_json))
                last_rows = data.get("rows", [])

                _dbg(SOURCE,
                     f"tick={tick}  "
                     f"verified={data.get('verified')!r}  "
                     f"endDateRow={data.get('endDateRow')!r}  "
                     f"rows={last_rows}")

                # Tier 1: a.verified1 -- registrar-confirmed, accept immediately
                if data.get("verified"):
                    drop_text   = data["verified"]
                    drop_method = "a.verified1 (registrar-confirmed)"
                    _log(SOURCE, f"Tier-1 hit: a.verified1 found at tick {tick} ({tick * 0.5:.1f}s)")
                    break

                # Tier 2: "End Date" row label -- plain date without verified badge
                if data.get("endDateRow"):
                    drop_text   = data["endDateRow"]
                    drop_method = "'End Date' row label (fallback)"
                    _log(SOURCE, f"Tier-2 hit: End Date row found at tick {tick} ({tick * 0.5:.1f}s)")
                    break

            except Exception as exc:
                _dbg(SOURCE, f"tick={tick}  eval error: {exc}")

            if tick > 0 and tick % 10 == 0:
                labels = [r[0] for r in last_rows]
                _log(SOURCE,
                     f"  ... still waiting ({tick // 2}s elapsed)  "
                     f"rows so far: {labels}")

        if drop_text:
            # Strip stray whitespace / newlines the DOM may inject
            drop_text = drop_text.strip()
            dt = _parse_dt(drop_text)
            result.set_dt(dt, f"ExpiredDomains.net ({drop_method})", "exact", drop_text)
            _log(SOURCE, f"SUCCESS  Raw     : {drop_text}")
            _log(SOURCE, f"         Method  : {drop_method}")
            _log(SOURCE, f"         UTC     : {result.drop_dt_utc.strftime('%Y-%m-%d %H:%M UTC')}")
            _log(SOURCE, f"         PST     : {result.drop_dt_pst.strftime('%Y-%m-%d %H:%M PST')}")
        else:
            rows_seen = [r[0] for r in last_rows]
            result.error = (
                f"Neither a.verified1 nor 'End Date' row found after 40s. "
                f"Rows visible in table.base1: {rows_seen}. "
                f"Domain may not be listed, login may be needed, "
                f"or the page structure changed."
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
    global DEBUG
    parser = argparse.ArgumentParser(
        prog="drop_time_scraper",
        description="Fetch exact drop time for pending-delete domains.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python drop_time_scraper.py zenithpicks.com reviewindex.com\n"
            "  python drop_time_scraper.py --source dynadot reviewindex.com\n"
            "  python drop_time_scraper.py --source expireddomains zenithpicks.com\n"
            "  python drop_time_scraper.py --debug zenithpicks.com\n"
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
    parser.add_argument("--debug", action="store_true",
                        help="Print verified/endDateRow/rows on every poll tick")
    args = parser.parse_args()
    DEBUG = args.debug

    results: list[DropResult] = []
    for domain in args.domains:
        _log("MAIN", "=" * 54)
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
