"""Job Sweep: find jobs → apply rules → verify links → skip repeats → email → log.

Usage:
  python -m sweep.main             # normal run (sends email)
  python -m sweep.main --dry-run   # build the email into out/email.html, send nothing, log nothing
"""
from __future__ import annotations

import argparse
import json
import datetime as dt
import os
import smtplib
import sys
from collections import OrderedDict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import ai, site, usajobs
from .checks import SentLog, _parse_date, link_ok, rule_problems

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")


# GitHub backup schedules (UTC) → whether that line is meant for daylight-saving time.
BACKUP_CRONS = {"45 14 * * 2,4": False, "45 13 * * 2,4": True}


def should_run_now(schedule: str) -> bool:
    """GitHub cron is UTC. Two backup lines exist (EST and EDT); only the one matching today's offset runs."""
    if not schedule:
        return True  # manual or Cloudflare-started run
    in_dst = bool(dt.datetime.now(ET).dst())
    return BACKUP_CRONS.get(schedule, in_dst) == in_dst


def already_sent_today(today: dt.date) -> bool:
    f = ROOT / "data" / "last_run.txt"
    return f.exists() and f.read_text().startswith(today.isoformat())


def sort_key(j: dict):
    closes = _parse_date(j.get("closes", ""))
    posted = _parse_date(j.get("posted", "")) or dt.date(1970, 1, 1)
    return (0, closes.toordinal(), 0) if closes else (1, 0, -posted.toordinal())


def render(cfg: dict, jobs: list[dict], today: dt.datetime, tracker_link: str = "") -> tuple[str, str, str]:
    groups = OrderedDict((s, []) for s in ai.SECTIONS)
    for j in jobs:
        groups.setdefault(j["section"], []).append(j)
    for s in groups:
        groups[s].sort(key=sort_key)
    sections = [(s, js) for s, js in groups.items() if js]
    empty = [s.split(" (")[0] for s, js in groups.items() if not js]
    date_long = today.strftime("%A, %B %-d, %Y")
    env = Environment(loader=FileSystemLoader(ROOT / "sweep" / "templates"), autoescape=select_autoescape(["html"]))
    html = env.get_template("email.html").render(
        date_long=date_long, total=len(jobs), name=cfg["candidate"]["name"], min_k=cfg["min_salary"] // 1000,
        sections=sections, empty_sections=empty, review_mode=cfg["review_mode"], tracker_link=tracker_link)
    lines = [f"Job Sweep — {date_long} — {len(jobs)} new roles", ""]
    if tracker_link:
        lines += [f"All jobs + mark what you've applied to: {tracker_link}", ""]
    for s, js in sections:
        lines += [s.upper(), ""]
        for j in js:
            when = f"Closes {j['closes']}" if j.get("closes") else f"Open until filled (posted {j.get('posted')})"
            lines += [f"{j['title']} — {j['employer']}", f"  {j['location']} · {j['salary']} · {when}"]
            if j.get("fit"):
                lines.append(f"  {'Stretch' if j.get('stretch') else 'Fit'}: {j['fit']}")
            lines += [f"  {j['url']}", ""]
    if not jobs:
        lines.append("No new qualifying postings this run.")
    subject = f"Job Sweep — {date_long} — {len(jobs)} new role{'' if len(jobs) == 1 else 's'}"
    if cfg["review_mode"]:
        subject = "[Review] " + subject
    return subject, html, "\n".join(lines)


def send(cfg: dict, subject: str, html: str, text: str):
    sender = os.environ["GMAIL_ADDRESS"]
    if cfg["review_mode"]:
        to, cc = [cfg["email"]["reviewer"]], []
    else:
        to, cc = cfg["email"]["to"], cfg["email"]["cc"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["email"]["from_name"], sender))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(sender, os.environ["GMAIL_APP_PASSWORD"])
        s.sendmail(sender, to + cc, msg.as_string())
    print(f"Sent to {to} cc {cc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not should_run_now(os.environ.get("CRON_SCHEDULE", "")):
        print("Other daylight-saving schedule; skipping this trigger.")
        return 0

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    now = dt.datetime.now(ET)
    today = now.date()
    source = os.environ.get("RUN_SOURCE", "manual")
    print(f"Started by: {source}")
    if source in ("cloudflare", "github-schedule") and already_sent_today(today) and not args.dry_run:
        print("Today's sweep was already sent; skipping so nobody gets a second email.")
        return 0
    archive = site.load()
    log = SentLog(ROOT / "data" / "sent_jobs.json", site.known_jobs(archive))
    notes = []

    candidates: list[dict] = []
    try:
        fed = usajobs.fetch(cfg, cfg["min_salary"])
        print(f"USAJOBS: {len(fed)} candidates")
        candidates += fed
    except Exception as e:  # keep going; report in logs
        notes.append(f"USAJOBS failed: {e}")
    try:
        found = ai.search(cfg, log.labels())
        print(f"AI search: {len(found)} candidates")
        candidates += found
    except Exception as e:
        notes.append(f"AI search failed: {e}")

    # rules, repeats, duplicates within this run
    passed, seen = [], set()
    for j in candidates:
        probs = rule_problems(j, cfg, today)
        key = j.get("url", "").rstrip("/").lower()
        if probs or log.is_repeat(j) or key in seen:
            print(f"drop: {j.get('title')} — {j.get('employer')}: {probs or 'repeat/duplicate'}")
            continue
        seen.add(key)
        passed.append(j)

    # fit review, then live link check
    try:
        reviewed = ai.review(cfg, passed)
    except Exception as e:
        notes.append(f"Fit review failed, sending unreviewed: {e}")
        reviewed = passed
    final = [j for j in reviewed if link_ok(j)]
    print(f"Final: {len(final)} jobs")

    tracker = (cfg.get("tracker_url") or "").rstrip("/")
    anchor = f"#sweep-{today.isoformat()}" if final else ""
    tracker_link = f"{tracker}/{anchor}" if tracker else ""
    subject, html, text = render(cfg, final, now, tracker_link)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "email.html").write_text(html)
    (out / "email.txt").write_text(text + ("\n\nNOTES:\n" + "\n".join(notes) if notes else ""))
    for n in notes:
        print("NOTE:", n)

    if args.dry_run:
        gid = site.add_run(archive, final, today, save=False) if final else None
        site.render(archive, now, gid, out=out / "archive-preview.html")
        print("Dry run — nothing sent or saved. See out/email.html and out/archive-preview.html")
        return 0
    # Update the archive page first, so the email's link already has this run's jobs once it deploys.
    gid = site.add_run(archive, final, today) if final else None
    site.render(archive, now, gid)
    send(cfg, subject, html, text)
    log.add(final, today.isoformat())
    (ROOT / "data" / "last_run.txt").write_text(f"{now.isoformat()} sent {len(final)} jobs\n")
    # Fail the workflow (GitHub emails you) if both sources broke.
    return 1 if len(notes) >= 2 else 0


if __name__ == "__main__":
    sys.exit(main())
