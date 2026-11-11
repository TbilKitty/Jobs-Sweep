"""The Job Sweep Archive web page: data/archive.json → site/index.html."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "data" / "archive.json"
OUT = ROOT / "site" / "index.html"
AP_MONTHS = ["Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]


def ap_date(d: dt.date) -> str:
    return f"{AP_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _iso(mmddyyyy: str) -> str:
    try:
        return dt.datetime.strptime(mmddyyyy.strip(), "%m/%d/%Y").date().isoformat()
    except (ValueError, AttributeError):
        return ""


def load() -> dict:
    return json.loads(ARCHIVE.read_text()) if ARCHIVE.exists() else {"groups": []}


def known_jobs(archive: dict) -> list[dict]:
    """Every job already on the page, so the sweep never repeats one."""
    return [j for g in archive["groups"] for j in g["jobs"]]


def add_run(archive: dict, jobs: list[dict], day: dt.date, save: bool = True) -> str:
    """Add this run's jobs as a dated section (merging with a same-day section). Returns its anchor id."""
    gid = f"sweep-{day.isoformat()}"
    group = next((g for g in archive["groups"] if g["id"] == gid), None)
    if group is None:
        group = {"id": gid, "title": f"Job Sweep · {ap_date(day)}", "kind": "sweep", "date": day.isoformat(), "jobs": []}
        archive["groups"].append(group)
    have = {j["url"] for j in group["jobs"]}
    for j in jobs:
        if j["url"] in have:
            continue
        have.add(j["url"])
        closes = j.get("closes", "")
        group["jobs"].append({
            "title": j["title"], "url": j["url"], "employer": j["employer"], "location": j["location"],
            "salary": j["salary"],
            "status": f"Closes {closes}" if closes else f"Open until filled (posted {j.get('posted') or 'recently'})",
            "fit": j.get("fit", ""), "stretch": bool(j.get("stretch")), "note": "",
            "deadline": _iso(closes), "section": j.get("section", ""),
        })
    if save:
        ARCHIVE.write_text(json.dumps(archive, indent=2, ensure_ascii=False) + "\n")
    return gid


def render(archive: dict, now: dt.datetime, latest_id: str | None = None, out: Path = OUT) -> None:
    sweeps = sorted((g for g in archive["groups"] if g.get("kind") == "sweep"), key=lambda g: g["date"], reverse=True)
    curated = [g for g in archive["groups"] if g.get("kind") != "sweep"]
    groups = [g for g in sweeps + curated if g["jobs"]]
    newest = sweeps[0] if sweeps else None
    latest = {"id": newest["id"], "short": ap_date(dt.date.fromisoformat(newest["date"]))} if newest else None
    env = Environment(loader=FileSystemLoader(ROOT / "sweep" / "templates"), autoescape=select_autoescape(["html"]))
    html = env.get_template("archive.html").render(
        groups=groups, latest=latest, latest_id=latest_id or (newest["id"] if newest else ""),
        updated=f"{ap_date(now.date())}, {now.strftime('%-I:%M %p').lower().replace('am', 'a.m.').replace('pm', 'p.m.')} ET")
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
