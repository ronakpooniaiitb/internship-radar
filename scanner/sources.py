"""Fetchers for each job source. Every fetcher returns a list of dicts with the same keys:

    title, company, url, locations (list[str]), remote (bool|None), posted (YYYY-MM-DD or ""),
    description (plain text), employment (str), source (str)
"""
import html
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (internship-radar; personal job scanner)"}
TIMEOUT = 25

_session = requests.Session()
_session.headers.update(UA)


def _get_json(url, params=None):
    r = _session.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _date(v):
    """Turn many date formats into YYYY-MM-DD."""
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):
        if v > 1e11:  # milliseconds
            v = v / 1000
        return time.strftime("%Y-%m-%d", time.gmtime(v))
    s = str(v)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


def job(**kw):
    base = dict(title="", company="", url="", locations=[], remote=None, posted="",
                description="", employment="", source="")
    base.update(kw)
    base["title"] = (base["title"] or "").strip()
    base["locations"] = [l.strip() for l in base["locations"] if l and str(l).strip()]
    return base


# ───────────────────────── company career boards (ATS) ─────────────────────────

def parse_company_url(url):
    """Work out which job-board system a careers URL uses, and the company's id on it."""
    u = urlparse(url.strip())
    host = u.netloc.lower()
    parts = [p for p in u.path.split("/") if p]
    if not parts:
        return None
    slug = parts[0]
    if "greenhouse.io" in host:
        return ("greenhouse-eu" if ".eu." in host else "greenhouse", slug)
    if "lever.co" in host:
        return ("lever-eu" if ".eu." in host else "lever", slug)
    if "ashbyhq.com" in host:
        return ("ashby", slug)
    if "workable.com" in host:
        return ("workable", slug)
    if "smartrecruiters.com" in host:
        return ("smartrecruiters", slug)
    return None


def fetch_greenhouse(slug, eu=False):
    bases = ["https://boards-api.eu.greenhouse.io", "https://boards-api.greenhouse.io"] if eu \
        else ["https://boards-api.greenhouse.io"]
    last = None
    for b in bases:
        try:
            data = _get_json(f"{b}/v1/boards/{slug}/jobs", params={"content": "true"})
            break
        except Exception as e:  # try next base
            last = e
    else:
        raise last
    company = slug
    out = []
    for j in data.get("jobs", []):
        company = j.get("company_name") or company
        out.append(job(
            title=j.get("title"), company=company, url=j.get("absolute_url"),
            locations=[(j.get("location") or {}).get("name", "")],
            posted=_date(j.get("first_published") or j.get("updated_at")),
            description=strip_html(j.get("content")), source="Greenhouse"))
    return out


def fetch_lever(slug, eu=False):
    base = "https://api.eu.lever.co" if eu else "https://api.lever.co"
    data = _get_json(f"{base}/v0/postings/{slug}", params={"mode": "json"})
    out = []
    for j in data:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        wt = (j.get("workplaceType") or "").lower()
        out.append(job(
            title=j.get("text"), company=slug, url=j.get("hostedUrl"), locations=locs,
            remote=True if wt == "remote" else (False if wt in ("onsite", "on-site", "hybrid") else None),
            posted=_date(j.get("createdAt")), description=j.get("descriptionPlain") or strip_html(j.get("description")),
            employment=cat.get("commitment", ""), source="Lever"))
    return out


def fetch_ashby(slug):
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    out = []
    for j in data.get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in (j.get("secondaryLocations") or [])]
        wt = (j.get("workplaceType") or "").lower()
        remote = bool(j.get("isRemote")) or wt == "remote"
        out.append(job(
            title=j.get("title"), company=slug, url=j.get("jobUrl") or j.get("applyUrl"), locations=locs,
            remote=remote if (j.get("isRemote") is not None or wt) else None,
            posted=_date(j.get("publishedAt")), description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
            employment=j.get("employmentType", ""), source="Ashby"))
    return out


def fetch_workable(slug):
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
    company = data.get("name") or slug
    out = []
    for j in data.get("jobs", []):
        locs = []
        for l in j.get("locations") or []:
            locs.append(", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x))
        if not locs:
            locs = [", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)]
        out.append(job(
            title=j.get("title"), company=company,
            url=j.get("url") or j.get("application_url") or f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/",
            locations=locs, remote=bool(j.get("telecommuting")),
            posted=_date(j.get("published_on") or j.get("created_at")), description=strip_html(j.get("description")),
            employment=j.get("employment_type", ""), source="Workable"))
    return out


def fetch_smartrecruiters(slug):
    data = _get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100})
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        out.append(job(
            title=j.get("name"), company=(j.get("company") or {}).get("name") or slug,
            url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
            locations=[", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)],
            remote=bool(loc.get("remote")), posted=_date(j.get("releasedDate")),
            employment=(j.get("typeOfEmployment") or {}).get("label", ""), source="SmartRecruiters"))
    return out


def fetch_company(url):
    parsed = parse_company_url(url)
    if not parsed:
        raise ValueError("unsupported careers URL")
    kind, slug = parsed
    if kind.startswith("greenhouse"):
        return fetch_greenhouse(slug, eu=kind.endswith("eu"))
    if kind.startswith("lever"):
        return fetch_lever(slug, eu=kind.endswith("eu"))
    if kind == "ashby":
        return fetch_ashby(slug)
    if kind == "workable":
        return fetch_workable(slug)
    if kind == "smartrecruiters":
        return fetch_smartrecruiters(slug)
    raise ValueError("unsupported careers URL")


# ───────────────────────── remote job boards ─────────────────────────

def fetch_remotive():
    out = []
    for q in ["intern", "internship"]:
        data = _get_json("https://remotive.com/api/remote-jobs", params={"search": q})
        for j in data.get("jobs", []):
            out.append(job(
                title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
                locations=[j.get("candidate_required_location", "")], remote=True,
                posted=_date(j.get("publication_date")), description=strip_html(j.get("description")),
                employment=j.get("job_type", ""), source="Remotive"))
    return out


def fetch_remoteok():
    data = _get_json("https://remoteok.com/api")
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue
        out.append(job(
            title=j.get("position"), company=j.get("company"), url=j.get("apply_url") or j.get("url"),
            locations=[j.get("location") or "Worldwide"], remote=True, posted=_date(j.get("date")),
            description=strip_html(j.get("description")) + " " + " ".join(j.get("tags") or []), source="RemoteOK"))
    return out


def fetch_himalayas():
    out = []
    for q in ["intern", "internship"]:
        data = _get_json("https://himalayas.app/jobs/api/search", params={"q": q, "limit": 100})
        for j in data.get("jobs", []):
            restr = j.get("locationRestrictions") or []
            locs = [r if isinstance(r, str) else (r.get("name") or "") for r in restr] or ["Worldwide"]
            out.append(job(
                title=j.get("title"), company=j.get("companyName"),
                url=j.get("applicationLink") or j.get("guid"), locations=locs, remote=True,
                posted=_date(j.get("pubDate")), description=strip_html(j.get("description") or j.get("excerpt")),
                employment=j.get("employmentType", ""), source="Himalayas"))
    return out


def fetch_jobicy():
    data = _get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 100, "tag": "intern"})
    out = []
    for j in data.get("jobs", []):
        out.append(job(
            title=j.get("jobTitle"), company=j.get("companyName"), url=j.get("url"),
            locations=[j.get("jobGeo") or "Anywhere"], remote=True, posted=_date(j.get("pubDate")),
            description=strip_html(j.get("jobDescription") or j.get("jobExcerpt")),
            employment=" ".join(j.get("jobType") or []) if isinstance(j.get("jobType"), list) else (j.get("jobType") or ""),
            source="Jobicy"))
    return out


def fetch_arbeitnow():
    out = []
    for page in (1, 2, 3):
        data = _get_json("https://www.arbeitnow.com/api/job-board-api", params={"page": page})
        for j in data.get("data", []):
            if not j.get("remote"):
                continue
            out.append(job(
                title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
                locations=[j.get("location") or "Remote"], remote=True, posted=_date(j.get("created_at")),
                description=strip_html(j.get("description")), employment=" ".join(j.get("job_types") or []),
                source="Arbeitnow"))
    return out


def fetch_wwr():
    r = _session.get("https://weworkremotely.com/remote-jobs.rss", timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "")
        company, _, title = t.partition(":")
        if not title:
            company, title = "", t
        out.append(job(
            title=title, company=company, url=it.findtext("link"),
            locations=[it.findtext("region") or "Anywhere"], remote=True, posted=_date(it.findtext("pubDate")),
            description=strip_html(it.findtext("description")), source="We Work Remotely"))
    return out


BOARDS = {
    "Remotive": fetch_remotive,
    "RemoteOK": fetch_remoteok,
    "Himalayas": fetch_himalayas,
    "Jobicy": fetch_jobicy,
    "Arbeitnow": fetch_arbeitnow,
    "We Work Remotely": fetch_wwr,
}
