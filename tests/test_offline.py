"""Offline tests: no network, no keys. Run: python -m tests.test_offline"""
import datetime as dt
import json
import tempfile
from pathlib import Path
from unittest import mock

import yaml

from sweep import ai, main, usajobs
from sweep.checks import SentLog, rule_problems

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
TODAY = dt.date(2026, 11, 11)


def fake_usajobs_item(pid, title, state, city, lo, hi, grade=("13", "13"), public=True):
    return {"MatchedObjectDescriptor": {
        "PositionID": pid, "PositionTitle": title, "PositionURI": f"https://www.usajobs.gov/job/{pid}",
        "OrganizationName": "National Park Service",
        "PositionLocation": [{"CityName": f"{city}, {state}", "CountrySubDivisionCode": state}],
        "PositionRemuneration": [{"MinimumRange": lo, "MaximumRange": hi, "RateIntervalCode": "PA",
                                  "Description": "Per Year"}],
        "ApplicationCloseDate": "2026-11-25T23:59:59", "PublicationStartDate": "2026-11-10",
        "UserArea": {"Details": {"LowGrade": grade[0], "HighGrade": grade[1], "JobSummary": "Leads outreach.",
                                 "WhoMayApply": {"Name": "United States Citizens" if not public else "Open to the public",
                                                 "Code": ""},
                                 "HiringPath": ["public"] if public else ["fed-competitive"]}}}}


def test_usajobs_filters():
    items = [
        fake_usajobs_item("1", "Public Affairs Specialist", "District of Columbia", "Washington", "121785", "158322"),
        fake_usajobs_item("2", "Outreach Specialist", "Virginia", "Richmond", "121785", "158322"),      # not NoVA
        fake_usajobs_item("3", "Marketing Specialist", "Maryland", "Baltimore", "70000", "90000"),     # low pay
        fake_usajobs_item("4", "Partnership Specialist", "Maryland", "Silver Spring", "121785", "158322", public=False),
        fake_usajobs_item("5", "Comms Specialist", "Virginia", "Arlington", "143913", "187093", grade=("14", "14")),
    ]
    resp = mock.Mock(status_code=200, json=lambda: {"SearchResult": {"SearchResultItems": items}})
    resp.raise_for_status = lambda: None
    cfg = dict(CFG, usajobs=dict(CFG["usajobs"], keywords=["x"], locations=["y"]))
    with mock.patch.dict("os.environ", {"USAJOBS_EMAIL": "a@b.c", "USAJOBS_API_KEY": "k"}), \
         mock.patch("sweep.usajobs.requests.get", return_value=resp), mock.patch("sweep.usajobs.time.sleep"):
        jobs = usajobs.fetch(cfg, 95000)
    titles = sorted(j["title"] for j in jobs)
    assert titles == ["Comms Specialist, GS-14", "Public Affairs Specialist, GS-13"], titles
    assert jobs[0]["closes"] == "11/25/2026"


def test_rules():
    good = {"title": "Communications Director", "employer": "Howard County", "url": "https://x.wd1.myworkdayjobs.com/job/123",
            "location": "Ellicott City, MD", "salary": "$100,000 - $140,000", "salary_max": 140000,
            "closes": "11/30/2026", "posted": "", "open_until_filled": False}
    assert rule_problems(good, CFG, TODAY) == []
    assert "aggregator link" in rule_problems(dict(good, url="https://www.indeed.com/viewjob?jk=1"), CFG, TODAY)
    assert "generic careers page" in rule_problems(dict(good, url="https://www.aasld.org/careers"), CFG, TODAY)
    assert "salary below minimum" in rule_problems(dict(good, salary_max=90000), CFG, TODAY)
    assert "closed" in rule_problems(dict(good, closes="11/01/2026"), CFG, TODAY)
    old = dict(good, closes="", open_until_filled=True, posted="09/01/2026")
    assert "open-until-filled but not recent" in rule_problems(old, CFG, TODAY)
    assert rule_problems(dict(old, posted="11/05/2026"), CFG, TODAY) == []


def test_repeats():
    log = SentLog(ROOT / "data" / "sent_jobs.json")
    assert log.is_repeat({"title": "Public Affairs Specialist, GS-14", "employer": "Federal Trade Commission",
                          "url": "https://www.usajobs.gov/job/999"})
    assert log.is_repeat({"title": "x", "employer": "y", "url": "https://www.usajobs.gov/job/891648900/"})
    assert not log.is_repeat({"title": "Director of Communications", "employer": "Maryland DNR", "url": "https://z"})


def test_json_extract():
    assert ai._extract_json('Here you go:\n```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert ai._extract_json("no jobs today") == []


def test_render():
    cfg = dict(CFG)
    jobs = [
        {"section": "Maryland", "title": "Director of Communications", "employer": "Maryland Department of Natural Resources",
         "url": "https://www.jobapscloud.com/MD/sup/bulpreview.asp?R1=26&R2=1&R3=1", "location": "Annapolis, MD",
         "salary": "$104,519 – $162,800/yr", "closes": "11/24/2026", "posted": "", "fit": "Ran comms at a boating and fishing foundation; outdoor-recreation match.", "stretch": False},
        {"section": "Federal (MD/DC/NoVA duty stations)", "title": "Public Affairs Specialist, GS-13", "employer": "U.S. Fish and Wildlife Service",
         "url": "https://www.usajobs.gov/job/1", "location": "Falls Church, VA", "salary": "$121,785 – $158,322 per year",
         "closes": "11/20/2026", "posted": "", "fit": "Press releases, media inquiries, and outdoor-recreation public education.", "stretch": False},
        {"section": "Nonprofits, Associations & Universities (DMV)", "title": "Senior Director, Marketing", "employer": "League of American Bicyclists",
         "url": "https://bikeleague.org/jobs/senior-director-marketing", "location": "Washington, DC",
         "salary": "$120,000 – $140,000", "closes": "", "posted": "11/06/2026", "fit": "Asks for 15+ years leading a national membership brand.", "stretch": True},
    ]
    subject, html, text = main.render(cfg, jobs, dt.datetime(2026, 11, 12, 8, 45))
    assert subject == "[Review] Job Sweep — Thursday, November 12, 2026 — 3 new roles", subject
    assert 'href="https://www.usajobs.gov/job/1"' in html and "Stretch" in html and "Washington DC, Virginia" in html
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "sample_email.html").write_text(html)
    s2, h2, _ = main.render(cfg, [], dt.datetime(2026, 11, 12, 8, 45))
    assert "0 new roles" in s2 and "No new qualifying postings" in h2


def test_dst_guard():
    with mock.patch("sweep.main.dt") as mdt:
        mdt.datetime.now.return_value = dt.datetime(2026, 11, 12, 8, 45, tzinfo=main.ET)  # EST
        assert main.should_run_now("45 13 * * 2,4") is True
        assert main.should_run_now("45 12 * * 2,4") is False
        mdt.datetime.now.return_value = dt.datetime(2026, 6, 4, 8, 45, tzinfo=main.ET)    # EDT
        assert main.should_run_now("45 12 * * 2,4") is True
        assert main.should_run_now("45 13 * * 2,4") is False
    assert main.should_run_now("") is True


def test_end_to_end_dry_run():
    fed = [{"section": "Federal (MD/DC/NoVA duty stations)", "title": "Public Affairs Specialist, GS-13", "employer": "NOAA",
            "url": "https://www.usajobs.gov/job/2", "location": "Silver Spring, MD", "salary": "$121,785 – $158,322 per year",
            "salary_max": 158322, "closes": "12/01/2099", "posted": "", "open_until_filled": False, "summary": "", "source": "usajobs"}]
    found = [{"section": "Maryland", "title": "Communications Manager", "employer": "Frederick County",
              "url": "https://www.indeed.com/x", "location": "Frederick, MD", "salary": "$90,000 - $120,000",
              "salary_max": 120000, "closes": "12/01/2099", "posted": "", "open_until_filled": False, "source": "ai"}]
    with mock.patch("sweep.main.usajobs.fetch", return_value=fed), \
         mock.patch("sweep.main.ai.search", return_value=found), \
         mock.patch("sweep.main.ai.review", side_effect=lambda c, js: [dict(j, fit="Good match.", stretch=False) for j in js]), \
         mock.patch("sys.argv", ["x", "--dry-run"]):
        assert main.main() == 0
    text = (ROOT / "out" / "email.txt").read_text()
    assert "NOAA" in text and "Frederick County" not in text  # aggregator link dropped


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
