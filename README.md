# drop-time

Fetches the **exact or estimated drop time** for pending-delete domains (`.com`, `.net`, `.org`, and more). Designed to feed the [domain-snipe](https://github.com/Psmith23434/domain-snipe) sniping tool — run once per domain to know exactly when to pull the trigger.

---

## How it works

Four techniques are tried in order. The first success wins.

| # | Technique | Accuracy | Needs |
|---|---|---|---|
| 1 | **nodriver** — real undetected Chrome, solves Cloudflare Turnstile | 🟢 EXACT | `pip install nodriver` + Chrome |
| 2 | **curl-cffi** — Chrome 124 TLS/JA3 fingerprint (bypasses passive CF) | 🟢 EXACT (if CF relaxed) | `pip install curl-cffi` |
| 3 | **RDAP** — IANA public API, pendingDelete event + TLD offset | 🟡 ESTIMATED ±12h | stdlib only |
| 4 | **WHOIS** — raw TCP socket + TLD drop window offset | 🟡 ESTIMATED ±12h | stdlib only |

> Technique 1 reads the **exact drop time** directly from Dynadot's backorder page DOM after bypassing Cloudflare with a real Chrome instance.

---

## Install

```bash
# Best accuracy (requires Chrome installed)
pip install nodriver

# Also useful (TLS impersonation fallback)
pip install curl-cffi

# Techniques 3 & 4 need no install
```

---

## Usage

```bash
# Look up 1–3 domains (auto mode)
python drop_time_scraper.py zenithpicks.com
python drop_time_scraper.py zenithpicks.com example.com anotherdomain.com

# Force a specific technique
python drop_time_scraper.py --technique rdap   zenithpicks.com
python drop_time_scraper.py --technique whois  zenithpicks.com
python drop_time_scraper.py --technique nodriver zenithpicks.com

# JSON output (pipe into domain-snipe or other tools)
python drop_time_scraper.py --json zenithpicks.com
```

---

## Example output

```
🔍  zenithpicks.com
    ┌─ [1/4] nodriver (Chrome + Cloudflare bypass)…
    └─ ✓ success

══════════════════════════════════════════════════════════════
🟢  zenithpicks.com
   Drop Time  : 2026-04-08 18:45 UTC  (2026-04-08 10:45 PST)
   Confidence : EXACT
   Technique  : nodriver / Chrome (Dynadot)
   Raw source : 2026/04/08 10:45 PST
```

```json
[
  {
    "domain": "zenithpicks.com",
    "drop_utc": "2026-04-08T18:45:00+00:00",
    "drop_pst": "2026-04-08T10:45:00-08:00",
    "confidence": "exact",
    "technique": "nodriver / Chrome (Dynadot)",
    "raw": "2026/04/08 10:45 PST",
    "error": null
  }
]
```

---

## TLD drop windows (built-in)

| TLD | PendingDelete days | Typical drop (UTC) |
|---|---|---|
| `.com` / `.net` | 5 | ~15:30 |
| `.org` / `.info` / `.biz` | 5 | ~16:00 |
| `.io` / `.co` / `.me` | 5 | ~16:00–18:00 |

---

## Requirements

- Python 3.11+ (uses `zoneinfo`)
- Google Chrome (for Technique 1 only)
- `nodriver` and/or `curl-cffi` (optional, for exact results)
