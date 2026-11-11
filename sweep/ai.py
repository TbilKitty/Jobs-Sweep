"""AI calls (OpenAI or Anthropic) over plain HTTPS — no SDK needed.

search()  — uses the provider's web search to find state/local/nonprofit jobs, returns JSON.
review()  — no web; decides keep/drop for every candidate and writes the Fit line.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import requests

SECTIONS = [
    "Maryland",
    "Washington DC",
    "Virginia",
    "Federal (MD/DC/NoVA duty stations)",
    "Nonprofits, Associations & Universities (DMV)",
]

JOB_SCHEMA = """Return ONLY a JSON array (no prose, no code fences). Each element:
{"section": one of %s,
 "title": exact job title as posted,
 "employer": employer name,
 "url": the SPECIFIC posting URL on the employer's own system (never an aggregator, never a generic careers page),
 "location": "City, ST",
 "salary": salary exactly as listed,
 "salary_max": top of range as a number per year,
 "closes": closing date exactly as listed (MM/DD/YYYY) or "" if open until filled,
 "posted": posted date MM/DD/YYYY if known else "",
 "open_until_filled": true/false,
 "summary": 2-3 sentences on the duties from the posting}""" % json.dumps(SECTIONS)


def _search_prompt(cfg: dict, already_sent: list[str]) -> str:
    today = dt.date.today().strftime("%B %d, %Y")
    c = cfg["candidate"]
    return f"""Today is {today}. Find CURRENTLY OPEN job postings for this candidate.

CANDIDATE
{c['profile']}

FIT RULE
{c['fit_rule']}

WHERE (no federal/USAJOBS jobs — those are handled separately):
1. Government first (spend most of your effort here): Maryland state agencies and quasi-publics (MDOT/MTA, Stadium
   Authority, Port, Lottery, TEDCO, Office of Tourism, MPT, DNR), University System of Maryland schools, Baltimore City,
   Baltimore/Montgomery/Prince George's/Anne Arundel/Howard/Frederick/Harford counties, housing authorities, school-system
   communications offices; DC government agencies, WMATA, DC Water, Events DC, UDC; Arlington, Fairfax, Alexandria,
   Loudoun, Prince William, Virginia state agencies (DWR, VTC), GMU, NOVA, MWAA, MWCOG, NVRPA.
2. Then established DC/Baltimore/Northern Virginia nonprofits, trade/professional associations and private universities.
   No staffing agencies, recruiters, "confidential" employers or start-ups.

RULES
- Top of salary range >= ${cfg['min_salary']:,}/yr; private/nonprofit postings must show a salary.
- Open now: closing date today or later, or open-until-filled AND posted within {cfg['max_posting_age_days']} days.
- The url must be the employer's own posting page for that specific job. Open it to confirm title, salary and dates.
- Skip evergreen / talent-pool / pipeline postings and anything already sent: {json.dumps(already_sent[:200])}
- Never invent a field. If you can't confirm it, leave the job out.
- Aim for up to 15 strong matches.

{JOB_SCHEMA}"""


def _review_prompt(cfg: dict, jobs: list[dict]) -> str:
    c = cfg["candidate"]
    slim = [{"i": i, "title": j["title"], "employer": j["employer"], "salary": j["salary"],
             "summary": j.get("summary", "")} for i, j in enumerate(jobs)]
    return f"""Judge each job for this candidate.

CANDIDATE
{c['profile']}

FIT RULE
{c['fit_rule']}

JOBS
{json.dumps(slim, ensure_ascii=False)}

Return ONLY a JSON array, one element per job you KEEP (drop poor fits):
{{"i": <index>, "stretch": true/false, "fit": "<= 18 words: concrete reason tied to his resume, or the gap if stretch>"}}"""


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    return json.loads(text[start:end + 1])


# ── OpenAI (Responses API) ─────────────────────────────────────
def _openai(prompt: str, model: str, web: bool) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is empty — check the secret's exact name in GitHub")
    print(f"Using OpenAI key ending in ...{key[-4:]}")
    body = {"model": model, "input": prompt}
    if web:
        body["tools"] = [{"type": "web_search"}]
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body, timeout=900,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:400]}")
    data = r.json()
    out = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    out.append(part.get("text", ""))
    return "\n".join(out)


# ── Anthropic (Messages API) ───────────────────────────────────
def _anthropic(prompt: str, model: str, web: bool) -> str:
    headers = {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {"model": model, "max_tokens": 16000, "messages": [{"role": "user", "content": prompt}]}
    if web:
        headers["anthropic-beta"] = "web-fetch-2025-09-10"
        body["tools"] = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 30},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 40},
        ]
    for _ in range(6):  # continue through pause_turn
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=900)
        r.raise_for_status()
        data = r.json()
        if data.get("stop_reason") == "pause_turn":
            body["messages"] = body["messages"][:1] + [{"role": "assistant", "content": data["content"]}]
            continue
        return "\n".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return ""


def _call(cfg: dict, prompt: str, web: bool) -> str:
    if cfg["ai_provider"] == "anthropic":
        return _anthropic(prompt, cfg["anthropic_model"], web)
    return _openai(prompt, cfg["openai_model"], web)


def search(cfg: dict, already_sent: list[str]) -> list[dict]:
    jobs = _extract_json(_call(cfg, _search_prompt(cfg, already_sent), web=True))
    for j in jobs:
        j["source"] = "ai"
        if j.get("section") not in SECTIONS:
            j["section"] = SECTIONS[-1]
    return jobs


def review(cfg: dict, jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    verdicts = _extract_json(_call(cfg, _review_prompt(cfg, jobs), web=False))
    kept = []
    for v in verdicts:
        try:
            j = dict(jobs[int(v["i"])])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        j["stretch"] = bool(v.get("stretch"))
        j["fit"] = (v.get("fit") or "").strip()
        kept.append(j)
    return kept
