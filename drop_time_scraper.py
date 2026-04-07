#!/usr/bin/env python3
"""
drop_time_scraper.py
====================
Fetches the EXACT drop time for pending-delete domains from two sources:

  Source 1 - Dynadot backorder page (primary, most reliable)
             URL: https://www.dynadot.com/market/backorder/{domain}?rscbo=expireddomains
             Reads the exact "Drop Time" field directly.
             Requires: pip install nodriver
             Requires: Chrome / Edge / Brave / Chromium installed.

  Source 2 - ExpiredDomains.net bulk domain search (fast cross-check)
             URL: https://www.expireddomains.net/domain-name-search/?q={domain}&fwhois=22
             Reads the "Droptime" column from the results table (no login required for
             basic pending-delete lookups at low volume -- ~5 domains/day is fine).
             Requires: pip install nodriver  (same browser session, no extra deps)

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
"""

from __future__ import annotations
import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

PST = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc

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
    Parse drop-time strings from both Dynadot and ExpiredDomains.net.
    Handles:
      Dynadot:          '2026/04/08 10:45 PST'
      ExpiredDomains:   '2026-04-08 10:45:00 UTC'  or  '2026-04-08'
    """
    raw = raw.strip()
    m = re.search(
        r"(\d{4})[/\-](\d{2})[/\-](\d{2})"
        r"(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?"
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


async def _make_browser(chrome_bin: str, tmp_profile: str):
    """
    Create a nodriver Browser using Browser.create(Config(...)).
    This works with Edge, Brave, and Chromium -- not just chrome.exe --
    because it bypasses nodriver's internal binary-name check.
    """
    import nodriver as uc
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
    return await uc.Browser.create(config)


# ---------------------------------------------------------------------------
# Source 1 -- Dynadot backorder page
# ---------------------------------------------------------------------------

async def _fetch_dynadot(domain: str, browser_path: Optional[str] = None) -> DropResult:
    """
    Loads https://www.dynadot.com/market/backorder/{domain}?rscbo=expireddomains
    and extracts the exact 'Drop Time' value.

    The page is behind Cloudflare Managed Challenge so a real browser is required.
    nodriver strips all automation fingerprints so the challenge passes transparently.
    """
    result = DropResult(domain)
    tmp_profile: Optional[str] = None
    try:
        import nodriver as uc  # noqa: F401  (import check)

        chrome_bin = _find_chrome_binary(browser_path)
        if not chrome_bin:
            result.error = (
                "No browser found. Install Chrome/Edge/Brave or pass "
                '--browser-path "C:\\path\\to\\browser.exe"'
            )
            return result

        print(f"      Browser : {chrome_bin}", flush=True)
        print("      Launching...", flush=True)
        tmp_profile = tempfile.mkdtemp(prefix="uc_dynadot_")
        browser = await _make_browser(chrome_bin, tmp_profile)

        if browser is None:
            result.error = (
                "Browser.create() returned None -- binary may be incompatible. "
                "Try --browser-path pointing at a different Chromium build."
            )
            return result

        url = f"https://www.dynadot.com/market/backorder/{domain}?rscbo=expireddomains"
        page = await browser.get(url)

        JS = r"""
            (() => {
                // Try structured DOM first (.domain-info-text spans)
                const divs = document.querySelectorAll('.domain-info-text');
                for (const div of divs) {
                    const spans = div.querySelectorAll('span');
                    for (let i = 0; i < spans.length; i++) {
                        if (spans[i].innerText.includes('Drop Time') && spans[i+1]) {
                            return spans[i+1].innerText.trim();
                        }
                    }
                }
                // Fallback: regex over full page text
                const m = document.body.innerText.match(
                    /(\d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}\s*(?:PST|PDT|UTC))/
                );
                return m ? m[1] : null;
            })()
        """

        drop_text: Optional[str] = None
        for _ in range(60):        # poll up to 30 s
            await asyncio.sleep(0.5)
            try:
                val = await page.evaluate(JS)
                if val and re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}", val):
                    drop_text = val
                    break
            except Exception:
                pass

        await browser.stop()

        if drop_text:
            dt = _parse_dt(drop_text)
            result.set_dt(dt, "Dynadot backorder page", "exact", drop_text)
        else:
            result.error = (
                "Dynadot: page loaded but drop-time element not found. "
                "Domain may not be in pendingDelete, or Dynadot changed their DOM."
            )

    except ImportError:
        result.error = "nodriver not installed -- run: pip install nodriver"
    except Exception as e:
        result.error = f"Dynadot error: {type(e).__name__}: {e}"
    finally:
        if tmp_profile and Path(tmp_profile).exists():
            shutil.rmtree(tmp_profile, ignore_errors=True)
    return result


# ---------------------------------------------------------------------------
# Source 2 -- ExpiredDomains.net bulk search
# ---------------------------------------------------------------------------

async def _fetch_expireddomains(domain: str, browser_path: Optional[str] = None) -> DropResult:
    """
    Loads https://www.expireddomains.net/domain-name-search/?q={domain}&fwhois=22
    and reads the 'Droptime' column from the results table.

    ExpiredDomains.net aggregates drop times from registrar data and shows them
    in a plain HTML table -- no login required for low-volume lookups (~5/day).
    The site has basic bot protection but nodriver handles it cleanly.

    fwhois=22  filters to 'pending delete' status only.
    """
    result = DropResult(domain)
    tmp_profile: Optional[str] = None
    try:
        import nodriver as uc  # noqa: F401

        chrome_bin = _find_chrome_binary(browser_path)
        if not chrome_bin:
            result.error = (
                "No browser found. Install Chrome/Edge/Brave or pass "
                '--browser-path "C:\\path\\to\\browser.exe"'
            )
            return result

        print(f"      Browser : {chrome_bin}", flush=True)
        print("      Launching...", flush=True)
        tmp_profile = tempfile.mkdtemp(prefix="uc_expdom_")
        browser = await _make_browser(chrome_bin, tmp_profile)

        if browser is None:
            result.error = "Browser.create() returned None -- try --browser-path"
            return result

        url = (
            f"https://www.expireddomains.net/domain-name-search/"
            f"?q={domain}&fwhois=22"
        )
        page = await browser.get(url)

        # ExpiredDomains uses a regular HTML table.
        # We look for the row matching our domain and grab the Droptime cell.
        JS = r"""
            (() => {
                // Find the results table
                const rows = document.querySelectorAll('table.base1 tbody tr, #listdomains tbody tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (!cells.length) continue;
                    // First meaningful cell is the domain name link
                    const domainCell = row.querySelector('td a.namelink, td.field_domain a');
                    if (!domainCell) continue;
                    const rowDomain = domainCell.innerText.trim().toLowerCase();
                    if (!rowDomain.includes(QUERY_DOMAIN)) continue;
                    // Walk cells to find one that looks like a date/time
                    for (const cell of cells) {
                        const t = cell.innerText.trim();
                        if (/\d{4}[-\/]\d{2}[-\/]\d{2}/.test(t)) {
                            return t;
                        }
                    }
                }
                return null;
            })()
        """.replace("QUERY_DOMAIN", f'"{domain}"')

        drop_text: Optional[str] = None
        for _ in range(80):        # poll up to 40 s (ED can be slow)
            await asyncio.sleep(0.5)
            try:
                val = await page.evaluate(JS)
                if val and re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}", val):
                    drop_text = val
                    break
            except Exception:
                pass

        await browser.stop()

        if drop_text:
            dt = _parse_dt(drop_text)
            result.set_dt(dt, "ExpiredDomains.net search table", "exact", drop_text)
        else:
            result.error = (
                "ExpiredDomains.net: domain not found in results table. "
                "It may not be listed yet, or the row format changed."
            )

    except ImportError:
        result.error = "nodriver not installed -- run: pip install nodriver"
    except Exception as e:
        result.error = f"ExpiredDomains.net error: {type(e).__name__}: {e}"
    finally:
        if tmp_profile and Path(tmp_profile).exists():
            shutil.rmtree(tmp_profile, ignore_errors=True)
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
    source='auto'           : try Dynadot first, fall back to ExpiredDomains.net
    source='dynadot'        : Dynadot only
    source='expireddomains' : ExpiredDomains.net only
    """
    domain = _normalize_domain(domain)

    if source == "dynadot":
        return await _fetch_dynadot(domain, browser_path)
    if source == "expireddomains":
        return await _fetch_expireddomains(domain, browser_path)

    # auto: Dynadot first (most reliable), then ExpiredDomains.net
    print("    +- [1/2] Dynadot backorder page...", flush=True)
    r = await _fetch_dynadot(domain, browser_path)
    if r.drop_dt_utc:
        print("    +- success")
        return r
    print(f"    |     x {r.error}")

    print("    +- [2/2] ExpiredDomains.net...", flush=True)
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
        description="Fetch exact drop time for pending-delete domains (Dynadot + ExpiredDomains.net).",
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
        help="Which source to use (default: auto = Dynadot first, ExpiredDomains.net as fallback)",
    )
    parser.add_argument(
        "--browser-path", "-b",
        metavar="PATH",
        default=None,
        help='Path to Chrome/Edge/Brave/Chromium executable. Also reads CHROME_PATH env var.',
    )
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
