"""Federal jobs from the official USAJOBS Search API (https://developer.usajobs.gov)."""
from __future__ import annotations

import os
import re
import time

import requests

API = "https://data.usajobs.gov/api/search"

NOVA_CITIES = {
    "arlington", "alexandria", "fairfax", "reston", "springfield", "quantico", "falls church",
    "mclean", "vienna", "herndon", "chantilly", "dulles", "fort belvoir", "manassas", "sterling",
    "tysons", "pentagon", "leesburg", "woodbridge", "ashburn", "annandale", "lorton",
}


def _headers() -> dict:
    return {
        "Host": "data.usajobs.gov",
        "User-Agent": os.environ["USAJOBS_EMAIL"],
        "Authorization-Key": os.environ["USAJOBS_API_KEY"],
    }


def _in_region(loc: dict) -> bool:
    state = (loc.get("CountrySubDivisionCode") or "").lower()
    city = (loc.get("CityName") or "").lower()
    if "district of columbia" in state or "washington, district" in city:
        return True
    if state == "maryland":
        return True
    if state == "virginia":
        return any(c in city for c in NOVA_CITIES)
    return False


def _grade_ok(d: dict, lo: int, hi: int) -> bool:
    try:
        low = int(d["UserArea"]["Details"]["LowGrade"])
        high = int(d["UserArea"]["Details"]["HighGrade"])
    except (KeyError, ValueError, TypeError):
        return True  # non-GS pay plans; salary filter still applies
    return high >= lo and low <= hi


def _public(d: dict) -> bool:
    who = d.get("UserArea", {}).get("Details", {}).get("WhoMayApply", {}) or {}
    text = (who.get("Name", "") + " " + who.get("Code", "")).lower()
    hiring = " ".join(d.get("UserArea", {}).get("Details", {}).get("HiringPath", []) or []).lower()
    return "public" in text or "public" in hiring


def _salary(d: dict) -> tuple[str, float]:
    rem = (d.get("PositionRemuneration") or [{}])[0]
    lo, hi = rem.get("MinimumRange"), rem.get("MaximumRange")
    per = rem.get("Description") or rem.get("RateIntervalCode") or ""
    try:
        lo_f, hi_f = float(lo), float(hi)
    except (TypeError, ValueError):
        return "", 0.0
    if "hour" in per.lower() or per == "PH":
        hi_f *= 2087
    return f"${float(lo):,.0f} – ${float(hi):,.0f} {'per year' if 'year' in per.lower() or per == 'PA' else per}".strip(), hi_f


def _fmt_date(s: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else s


def fetch(cfg: dict, min_salary: int) -> list[dict]:
    uc = cfg["usajobs"]
    seen, jobs = set(), []
    for kw in uc["keywords"]:
        for loc in uc["locations"]:
            params = {
                "Keyword": kw,
                "LocationName": loc,
                "JobCategoryCode": ";".join(uc["series"]),
                "DatePosted": uc["days_posted"],
                "ResultsPerPage": 100,
                "WhoMayApply": "public",
            }
            r = requests.get(API, headers=_headers(), params=params, timeout=40)
            r.raise_for_status()
            items = r.json().get("SearchResult", {}).get("SearchResultItems", [])
            for it in items:
                d = it["MatchedObjectDescriptor"]
                pid = d.get("PositionID") or d.get("PositionURI")
                if pid in seen:
                    continue
                seen.add(pid)
                locs = [l for l in d.get("PositionLocation", []) if _in_region(l)]
                if not locs:
                    continue
                if not _grade_ok(d, uc["min_grade"], uc["max_grade"]) or not _public(d):
                    continue
                sal_text, sal_max = _salary(d)
                if sal_max < min_salary:
                    continue
                grade = ""
                try:
                    det = d["UserArea"]["Details"]
                    grade = f"GS-{det['LowGrade']}" + (f"/{det['HighGrade']}" if det["HighGrade"] != det["LowGrade"] else "")
                except KeyError:
                    pass
                first = locs[0]
                city = first.get("CityName", "").split(",")[0]
                st = {"District of Columbia": "DC", "Maryland": "MD", "Virginia": "VA"}.get(
                    first.get("CountrySubDivisionCode", ""), first.get("CountrySubDivisionCode", ""))
                jobs.append({
                    "section": "Federal (MD/DC/NoVA duty stations)",
                    "title": d.get("PositionTitle", "").strip() + (f", {grade}" if grade else ""),
                    "employer": d.get("OrganizationName") or d.get("DepartmentName", ""),
                    "url": d.get("PositionURI", ""),
                    "location": f"{city}, {st}" + (f" (+{len(locs) - 1} more)" if len(locs) > 1 else ""),
                    "salary": sal_text,
                    "salary_max": sal_max,
                    "closes": _fmt_date(d.get("ApplicationCloseDate", "")),
                    "posted": _fmt_date(d.get("PublicationStartDate", "")),
                    "open_until_filled": False,
                    "summary": (d.get("UserArea", {}).get("Details", {}).get("JobSummary") or "")[:1200],
                    "source": "usajobs",
                })
            time.sleep(0.3)
    return jobs
