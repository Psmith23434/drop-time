#!/usr/bin/env python3
"""
drop_time_scraper.py
====================
Fetches the exact drop time for pending-delete .com/.net/etc. domains.
Tries multiple techniques in order of reliability:

  Technique 1 — nodriver   : Undetected Chrome, solves Cloudflare Turnstile,
                              reads the EXACT drop time from Dynadot backorder page.
                              Requires: pip install nodriver
                              Requires: Google Chrome installed on the machine.

  Technique 2 — curl-cffi  : TLS/JA3 fingerprint impersonation (Chrome 124).
                              Fast, no browser needed. Works IF Cloudflare is
                              ever relaxed. Currently still blocked by CF Managed
                              Challenge on Dynadot — included for future use.
                              Requires: pip install curl-cffi

  Technique 3 — RDAP        : IANA public RDAP API, no auth, no cost.
                              Finds the pendingDelete entry date then adds the
                              TLD-specific drop window offset.
                              Confidence: ESTIMATED (no library needed).

  Technique 4 — WHOIS       : Raw socket WHOIS query, no library needed.
                              Reads Updated Date and adds TLD drop window offset.
                              Confidence: ESTIMATED (~+-12h accuracy).

Usage:
  python drop_time_scraper.py zenithpicks.com example.com
  python drop_time_scraper.py --technique rdap zenithpicks.com
  python drop_time_scraper.py --technique whois zenithpicks.com
  python drop_time_scraper.py --json zenithpicks.com

Install only what you need:
  pip install nodriver           # Technique 1 (best accuracy)
  pip install curl-cffi          # Technique 2 (fast, currently blocked)
  # Techniques 3 & 4 require only Python stdlib
"""

from __future__ import annotations
import argparse
import asyncio
import json
import re
import socket
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


# ─── TLD drop window table ────────────────────────────────────────────────────
# (pending_delete_days, typical_drop_hour_utc, typical_drop_minute_utc)
# Source: registry-published schedules + community observation
TLD_DROP_WINDOWS: dict[str, tuple[int, int, int]] = {
    "com":  (5, 15, 30),   # Verisign batch ~15:00-16:00 UTC
    "net":  (5, 15, 30),
    "org":  (5, 16,  0),   # PIR / Afilias
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

PST = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


# ═════════════════════════════════════════════════════════════════════════════
# Result object
# ═════════════════════════════════════════════════════════════════════════════

class DropResult:
    def __init__(self, domain: str):
        self.domain       : str                = domain.lower().strip()
        self.drop_dt_utc  : Optional[datetime] = None
        self.drop_dt_pst  : Optional[datetime] = None
        self.raw_text     : Optional[str]       = None
        self.technique    : Optional[str]       = None
        self.confidence   : str                = "unknown"  # exact | estimated | failed
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
        icon = {"exact": "🟢", "estimated": "🟡", "failed": "🔴"}.get(self.confidence, "⚪")
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
        return f"🔴  {self.domain}  —  FAILED: {self.error}"

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


# ═════════════════════════════════════════════════════════════════════════════
# Shared helper
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# Technique 1 — nodriver (real undetected Chrome, solves CF Turnstile)
# ═════════════════════════════════════════════════════════════════════════════

async def _fetch_nodriver(domain: str) -> DropResult:
    """
    Launches a real Chrome instance via nodriver.
    nodriver patches Chrome to avoid headless detection fingerprints
    (navigator.webdriver, CDP exposure, etc.) and can solve Cloudflare
    Managed Challenge by simply running JS like a real browser.

    After CF clears, waits for Vue/Nuxt to hydrate the .domain-info-text
    component and reads the drop-time span directly from the live DOM.
    """
    result = DropResult(domain)
    try:
        import nodriver as uc  # pip install nodriver

        url = (
            f"https://www.dynadot.com/market/backorder/{domain}"
            f"?rscbo=expireddomains"
        )
        print("      Launching Chrome…", flush=True)
        browser = await uc.start(
            headless=True,
            browser_args=[
                "--no-sandbox",
                "--disable-gpu",
                "--window-size=1280,900",
            ],
        )
        page = await browser.get(url)

        # Poll up to 30s for Vue hydration + CF challenge to clear
        drop_text: Optional[str] = None
        for attempt in range(60):
            await asyncio.sleep(0.5)
            try:
                js_result = await page.evaluate("""
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
                        // Strategy B: look for date-like pattern anywhere on page
                        const body = document.body.innerText;
                        const m = body.match(/(\d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}\s*(?:PST|PDT|UTC))/);
                        return m ? m[1] : null;
                    })()
                """)
                if js_result and re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}", js_result):
                    drop_text = js_result
                    break
            except Exception:
                pass

        await browser.stop()

        if drop_text:
            dt = _parse_dynadot_dt(drop_text)
            result.set_dt(dt, "nodriver / Chrome (Dynadot)", "exact", drop_text)
        else:
            result.error = (
                "nodriver: Chrome loaded the page but the drop-time element was not found. "
                "Dynadot may have changed their DOM structure."
            )

    except ImportError:
        result.error = "nodriver not installed — run: pip install nodriver"
    except Exception as e:
        result.error = f"nodriver exception: {type(e).__name__}: {e}"
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Technique 2 — curl-cffi (TLS/JA3 impersonation, Chrome 124 fingerprint)
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_curl_cffi(domain: str) -> DropResult:
    """
    Uses curl-cffi to send an HTTP request with a byte-perfect Chrome 124
    TLS ClientHello + HTTP/2 SETTINGS frames, bypassing passive bot detection.

    Dynadot currently deploys Cloudflare Managed Challenge, which requires
    actual JS execution. This technique is blocked TODAY but included because:
    - Dynadot sometimes rotates CF security levels
    - It is the correct first step before nodriver in a lighter-weight pipeline
    - It works immediately if Dynadot ever drops to CF Bot Fight Mode (no JS challenge)
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
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
        }
        resp = cffi_req.get(
            url,
            headers=headers,
            impersonate="chrome124",
            timeout=20,
            allow_redirects=True,
        )

        if resp.status_code == 403 or "Just a moment" in resp.text:
            result.error = (
                "curl-cffi: Cloudflare Managed Challenge active — "
                "JS execution required. Use nodriver instead."
            )
            return result

        html = resp.text
        nuxt_m = re.search(r'"dropTime"\s*:\s*"([^"]+)"', html)
        if nuxt_m:
            drop_text = nuxt_m.group(1)
            dt = _parse_dynadot_dt(drop_text)
            result.set_dt(dt, "curl-cffi / TLS impersonation (Dynadot)", "exact", drop_text)
            return result

        raw_m = re.search(r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}\s*(?:PST|PDT|UTC|GMT))", html)
        if raw_m:
            drop_text = raw_m.group(1)
            dt = _parse_dynadot_dt(drop_text)
            result.set_dt(dt, "curl-cffi / TLS impersonation (Dynadot)", "exact", drop_text)
        else:
            result.error = (
                "curl-cffi: bypassed CF but drop time not in HTML "
                "(SPA not server-rendered for this route)"
            )

    except ImportError:
        result.error = "curl-cffi not installed — run: pip install curl-cffi"
    except Exception as e:
        result.error = f"curl-cffi exception: {type(e).__name__}: {e}"
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Technique 3 — RDAP (IANA public API, no auth, estimated result)
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_rdap(domain: str) -> DropResult:
    """
    Two-step RDAP query:
      1. IANA RDAP DNS bootstrap → find authoritative RDAP server for TLD
      2. Query that server for domain data → extract pendingDelete event date
    Then adds TLD-specific drop window offset to produce the estimated drop time.
    No external libraries needed (pure stdlib urllib).
    """
    result = DropResult(domain)
    try:
        tld = domain.rsplit(".", 1)[-1].lower()

        bootstrap_url = "https://data.iana.org/rdap/dns.json"
        with urllib.request.urlopen(bootstrap_url, timeout=10) as r:
            bootstrap = json.loads(r.read())

        rdap_base: Optional[str] = None
        for entry in bootstrap.get("services", []):
            tlds_list, urls_list = entry[0], entry[1]
            if tld in [t.lower() for t in tlds_list]:
                rdap_base = urls_list[0].rstrip("/")
                break

        if not rdap_base:
            result.error = f"RDAP: no server registered for .{tld} in IANA bootstrap"
            return result

        rdap_url = f"{rdap_base}/domain/{domain}"
        req = urllib.request.Request(
            rdap_url,
            headers={"Accept": "application/rdap+json, application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())

        status  = [s.lower() for s in data.get("status", [])]
        events  = data.get("events", [])

        pending_dt: Optional[datetime] = None
        expiry_dt:  Optional[datetime] = None
        updated_dt: Optional[datetime] = None

        for ev in events:
            action  = ev.get("eventAction", "").lower()
            raw_dt  = ev.get("eventDate", "")
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

        is_pending = any("pending" in s for s in status)

        if pending_dt:
            days, hour, minute = TLD_DROP_WINDOWS.get(tld, DEFAULT_DROP_WINDOW)
            drop_day = (pending_dt + timedelta(days=days)).date()
            drop_dt  = datetime(drop_day.year, drop_day.month, drop_day.day,
                                hour, minute, 0, tzinfo=UTC)
            raw_s = (
                f"RDAP pendingDelete entered: {pending_dt.isoformat()} "
                f"→ +{days}d ~{hour:02d}:{minute:02d} UTC"
            )
            result.set_dt(drop_dt, "RDAP pendingDelete event + TLD window", "estimated", raw_s)

        elif is_pending and updated_dt:
            days, hour, minute = TLD_DROP_WINDOWS.get(tld, DEFAULT_DROP_WINDOW)
            drop_day = (updated_dt + timedelta(days=days)).date()
            drop_dt  = datetime(drop_day.year, drop_day.month, drop_day.day,
                                hour, minute, 0, tzinfo=UTC)
            raw_s = (
                f"RDAP last-updated (status=pendingDelete): {updated_dt.isoformat()} "
                f"→ +{days}d ~{hour:02d}:{minute:02d} UTC"
            )
            result.set_dt(drop_dt, "RDAP last-changed + TLD window", "estimated", raw_s)

        elif is_pending and expiry_dt:
            days, hour, minute = TLD_DROP_WINDOWS.get(tld, DEFAULT_DROP_WINDOW)
            pending_start = expiry_dt + timedelta(days=35)
            drop_day      = (pending_start + timedelta(days=days)).date()
            drop_dt       = datetime(drop_day.year, drop_day.month, drop_day.day,
                                     hour, minute, 0, tzinfo=UTC)
            raw_s = (
                f"RDAP expiry: {expiry_dt.isoformat()} "
                f"→ +35d redemption → +{days}d drop estimate"
            )
            result.set_dt(drop_dt, "RDAP expiry estimate + TLD window", "estimated", raw_s)

        else:
            result.error = (
                f"RDAP: domain found, status={status}, "
                f"but no pendingDelete/expiry event date available"
            )

    except urllib.error.HTTPError as e:
        if e.code == 404:
            result.error = "RDAP: domain not found (may already be dropped or not in pendingDelete)"
        else:
            result.error = f"RDAP HTTP {e.code}: {e.reason}"
    except Exception as e:
        result.error = f"RDAP exception: {type(e).__name__}: {e}"
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Technique 4 — Raw WHOIS socket + TLD drop window
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_whois(domain: str) -> DropResult:
    """
    Opens a raw TCP socket to the authoritative WHOIS server for the TLD,
    sends the domain name, reads the response, and extracts Updated Date.
    Then adds TLD-specific drop window offset to estimate drop time.
    No external libraries required.
    """
    result = DropResult(domain)
    try:
        tld         = domain.rsplit(".", 1)[-1].lower()
        whois_host  = WHOIS_SERVERS.get(tld, f"whois.nic.{tld}")

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
            ll = line.lower()
            if any(k in ll for k in ("updated date", "last-updated", "last updated")):
                m = re.search(
                    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+\-]\d{2}:\d{2})?)",
                    line,
                )
                if not m:
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                if m:
                    raw_s = m.group(1)
                    updated_dt = datetime.fromisoformat(
                        raw_s.replace("Z", "+00:00")
                    )
                    if updated_dt.tzinfo is None:
                        updated_dt = updated_dt.replace(tzinfo=UTC)
                    break

        if not updated_dt:
            result.error = f"WHOIS ({whois_host}): could not extract Updated Date"
            return result

        days, hour, minute = TLD_DROP_WINDOWS.get(tld, DEFAULT_DROP_WINDOW)
        drop_day = (updated_dt + timedelta(days=days)).date()
        drop_dt  = datetime(
            drop_day.year, drop_day.month, drop_day.day,
            hour, minute, 0, tzinfo=UTC,
        )
        raw_s = (
            f"WHOIS Updated Date: {updated_dt.date().isoformat()} "
            f"→ +{days}d ~{hour:02d}:{minute:02d} UTC"
        )
        result.set_dt(drop_dt, f"WHOIS ({whois_host}) + TLD window", "estimated", raw_s)

    except socket.gaierror as e:
        result.error = f"WHOIS: DNS resolution failed for WHOIS server: {e}"
    except socket.timeout:
        result.error = "WHOIS: connection timed out"
    except Exception as e:
        result.error = f"WHOIS exception: {type(e).__name__}: {e}"
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Auto-orchestrator
# ═════════════════════════════════════════════════════════════════════════════

async def get_drop_time(domain: str, technique: str = "auto") -> DropResult:
    """
    Main entry point. Returns a DropResult with the best available drop time.
    technique options: "auto" | "nodriver" | "curl_cffi" | "rdap" | "whois"
    """
    domain = _normalize_domain(domain)

    if technique == "nodriver":  return await _fetch_nodriver(domain)
    if technique == "curl_cffi": return _fetch_curl_cffi(domain)
    if technique == "rdap":      return _fetch_rdap(domain)
    if technique == "whois":     return _fetch_whois(domain)

    # AUTO: cascade
    print(f"    ┌─ [1/4] nodriver (Chrome + Cloudflare bypass)…", flush=True)
    r = await _fetch_nodriver(domain)
    if r.drop_dt_utc:
        print(f"    └─ ✓ success")
        return r
    print(f"    │     ✗ {r.error}")

    for label, fn in [
        ("[2/4] curl-cffi (TLS impersonation)", _fetch_curl_cffi),
        ("[3/4] RDAP (IANA public API)",         _fetch_rdap),
        ("[4/4] WHOIS (raw socket)",              _fetch_whois),
    ]:
        print(f"    ├─ {label}…", flush=True)
        r2 = fn(domain)
        if r2.drop_dt_utc:
            print(f"    └─ ✓ success")
            return r2
        print(f"    │     ✗ {r2.error}")

    r.error = "All four techniques failed."
    return r


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

async def _main() -> None:
    parser = argparse.ArgumentParser(
        prog="drop_time_scraper",
        description="Fetch exact/estimated drop time for pending-delete domains.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("domains", nargs="+", metavar="DOMAIN")
    parser.add_argument(
        "--technique", "-t",
        choices=["auto", "nodriver", "curl_cffi", "rdap", "whois"],
        default="auto",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results: list[DropResult] = []
    for domain in args.domains:
        print(f"\n🔍  {domain}")
        r = await get_drop_time(domain, args.technique)
        results.append(r)

    sep = "═" * 62
    print(f"\n{sep}")
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2, default=str))
    else:
        for r in results:
            print(r.display())
            print()


if __name__ == "__main__":
    asyncio.run(_main())
