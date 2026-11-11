"""Hard rules applied in code, so every email follows them no matter what the AI returns."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; JobSweep/1.0; personal job alert)"}
GENERIC_PATHS = re.compile(r"/(careers?|jobs|employment|opportunities|join-us|work-with-us)/?$", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _parse_date(s: str):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


# ── sent-jobs log ──────────────────────────────────────────────
class SentLog:
    def __init__(self, path: Path, also_known: list[dict] | None = None):
        self.path = path
        self.rows = json.loads(path.read_text()) if path.exists() else []
        known = self.rows + (also_known or [])
        self.urls = {r["url"].rstrip("/").lower() for r in known if r.get("url")}
        self.keys = {(_norm(r["title"]), _norm(r["employer"])) for r in known}

    def is_repeat(self, j: dict) -> bool:
        if j["url"].rstrip("/").lower() in self.urls:
            return True
        title = re.sub(r",\s*GS-\d+(/\d+)?$", "", j["title"])
        return (_norm(title), _norm(j["employer"])) in self.keys or (_norm(j["title"]), _norm(j["employer"])) in self.keys

    def labels(self) -> list[str]:
        return [f"{r['title']} — {r['employer']}" for r in self.rows]

    def add(self, jobs: list[dict], today: str):
        for j in jobs:
            self.rows.append({k: j.get(k, "") for k in ("title", "employer", "url", "location", "salary", "closes")}
                             | {"first_emailed": today})
        self.path.write_text(json.dumps(self.rows, indent=2, ensure_ascii=False) + "\n")


# ── rule filters ───────────────────────────────────────────────
def rule_problems(j: dict, cfg: dict, today: dt.date) -> list[str]:
    p = []
    for f in ("title", "employer", "url", "location", "salary"):
        if not str(j.get(f, "")).strip():
            p.append(f"missing {f}")
    host = urlparse(j.get("url", "")).netloc.lower()
    if any(host == d or host.endswith("." + d) for d in cfg["aggregator_domains"]):
        p.append("aggregator link")
    if GENERIC_PATHS.search(urlparse(j.get("url", "")).path or "/"):
        p.append("generic careers page")
    try:
        if float(j.get("salary_max") or 0) < cfg["min_salary"]:
            p.append("salary below minimum")
    except (TypeError, ValueError):
        p.append("salary unreadable")
    closes = _parse_date(j.get("closes", ""))
    if closes:
        if closes < today:
            p.append("closed")
    elif j.get("open_until_filled"):
        posted = _parse_date(j.get("posted", ""))
        if not posted or (today - posted).days > cfg["max_posting_age_days"]:
            p.append("open-until-filled but not recent")
    else:
        p.append("no closing date")
    return p


def link_ok(j: dict) -> bool:
    """The posting must load. USAJOBS links come straight from the API, so they're trusted."""
    if j.get("source") == "usajobs":
        return True
    try:
        r = requests.get(j["url"], headers=UA, timeout=25, allow_redirects=True)
    except requests.RequestException:
        return False
    if r.status_code in (404, 410):
        return False
    if r.status_code >= 400:
        # Many job systems block scripts (403/429) but load fine in a browser; keep those.
        return r.status_code in (401, 403, 405, 429, 503)
    final = r.url.lower()
    if GENERIC_PATHS.search(urlparse(final).path or "/"):
        return False
    body = r.text.lower()
    if any(s in body for s in ("no longer accepting", "position has been filled", "job is no longer available",
                               "this job has expired", "posting has closed")):
        return False
    return True
