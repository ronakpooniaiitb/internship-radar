"""Internship Radar — collects internships from company job boards and remote job sites,
keeps the ones that fit profile.yml, scores them, and writes docs/jobs.json for the webpage.

Run:  python scanner/scan.py
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import yaml

sys.path.insert(0, os.path.dirname(__file__))
import sources  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timezone(timedelta(hours=5, minutes=30))

INTERN_RE = re.compile(r"\b(intern|interns|internship|internships|summer analyst|summer associate|trainee|apprentice|apprenticeship|co-?op|stipend)\b", re.I)
FULLTIME_TITLE_RE = re.compile(r"\b(senior|sr\.?|lead|principal|director|head of|manager ii|staff|vp|vice president)\b", re.I)
REMOTE_RE = re.compile(r"\b(remote|anywhere|worldwide|work from home|wfh|distributed|global)\b", re.I)
GLOBAL_OK_RE = re.compile(r"\b(anywhere|worldwide|global|international|apac|asia|asia[- ]pacific)\b", re.I)
RESTRICTED_RE = re.compile(
    r"\b(us|u\.s\.|usa|united states|americas|north america|latam|latin america|canada|uk|u\.k\.|united kingdom|"
    r"england|ireland|europe|european|eu|emea|germany|france|spain|netherlands|poland|portugal|italy|sweden|"
    r"australia|new zealand|brazil|mexico|argentina|colombia|nigeria|kenya|south africa|philippines|"
    r"pakistan|israel|japan|singapore|cet|est|pst|cst)\b", re.I)
GRAD_YEAR_RE = re.compile(
    r"(?:graduat\w*|class of|batch(?: of)?|passouts?|pass-?outs?|grads?)\D{0,25}(20\d\d)|(20\d\d)\s*(?:graduat\w*|grads?|batch|passouts?|pass-?outs?)",
    re.I)
SUMMER_RE = re.compile(r"summer\s*(?:of\s*)?(20\d\d)|(20\d\d)\s*summer|may\s*[-–to]+\s*(?:july|aug)\w*\s*(20\d\d)", re.I)


def load_yaml(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_company_urls():
    urls = []
    with open(os.path.join(ROOT, "companies.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line.startswith("http"):
                urls.append(line)
    return list(dict.fromkeys(urls))


def kw_re(words):
    words = [w for w in words if w]
    if not words:
        return None
    return re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w.lower()) for w in sorted(words, key=len, reverse=True)) + r")", re.I)


class Matcher:
    def __init__(self, p):
        self.p = p
        self.roles = {k.lower(): v for k, v in (p.get("role_keywords") or {}).items()}
        self.role_res = [(re.compile(r"(?<![a-z0-9])" + re.escape(k), re.I), k, v) for k, v in self.roles.items()]
        self.exclude = kw_re(p.get("exclude_title_keywords") or [])
        self.profile = [re.compile(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z])", re.I) for k in p.get("profile_keywords") or []]
        self.profile_words = [k for k in p.get("profile_keywords") or []]
        self.flags = [(re.compile(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z])", re.I), k) for k in p.get("red_flags") or []]
        self.india = kw_re(p.get("india_locations") or [])
        self.india = re.compile(self.india.pattern + r"(?![a-z])", re.I)
        self.home = kw_re(p.get("home_city_bonus") or [])
        self.grad = int(p.get("graduation_year") or 0)

    # → None if the posting should be dropped, else an enriched dict
    def evaluate(self, j):
        title = j["title"]
        if not title or not j.get("url"):
            return None
        text_emp = f"{title} {j.get('employment', '')}"
        if not INTERN_RE.search(text_emp):
            return None
        if FULLTIME_TITLE_RE.search(title):
            return None
        if self.exclude and self.exclude.search(title):
            return None

        hits = [(k, v) for r, k, v in self.role_res if r.search(title)]
        if not hits:
            return None
        role_score = max(v for _, v in hits) + (1 if len(hits) > 1 else 0)

        loc = " | ".join(j["locations"])
        desc = j.get("description") or ""
        head = desc[:1500]
        remote = j.get("remote")
        if remote is None:
            remote = bool(REMOTE_RE.search(loc)) or bool(re.search(r"\bremote\b", title, re.I))
        elif not remote and REMOTE_RE.search(loc) and not self.india.search(loc):
            remote = True

        india_hit = bool(self.india.search(loc))
        notes = []
        if remote:
            if india_hit or GLOBAL_OK_RE.search(loc):
                mode, loc_score = "Remote", 2
            elif RESTRICTED_RE.search(loc) or re.search(r"\b(us|usa|uk|eu)[- ]only\b|must (?:be )?(?:based|located|reside) in the (?:us|united states|uk|eu)", head, re.I):
                return None  # remote, but only for another country
            else:
                mode, loc_score = "Remote", 0
                notes.append("Remote, but region not stated — check India is allowed")
        else:
            if not india_hit:
                return None  # on-site outside India
            mode, loc_score = "On-site (India)", 2
            if self.home and self.home.search(loc):
                loc_score += 1

        full = f"{title} {desc}"
        prof_hits = sorted({w for r, w in zip(self.profile, self.profile_words) if r.search(full)})
        prof_score = min(2.0, 0.4 * len(prof_hits))

        bonus = 0
        years = set()
        for m in GRAD_YEAR_RE.finditer(full):
            y = m.group(1) or m.group(2)
            if y:
                years.add(int(y))
        if self.grad and years:
            if self.grad in years:
                bonus += 1
                notes.append(f"Mentions {self.grad} graduates ✓")
            elif all(y < self.grad for y in years):
                bonus -= 3
                notes.append("Seems to be for " + "/".join(str(y) for y in sorted(years)) + " graduates")
        summers = set()
        for m in SUMMER_RE.finditer(full):
            for g in m.groups():
                if g:
                    summers.add(int(g))
        if summers:
            if self.grad - 1 in summers:
                bonus += 1
                notes.append(f"Summer {self.grad - 1} ✓")

        flags = sorted({k for r, k in self.flags if r.search(full)})
        if flags:
            bonus -= min(4, 2 * len(flags))
            notes.append("Check: mentions " + ", ".join(flags))

        score = role_score + loc_score + prof_score + bonus
        score = int(round(max(1, min(10, score))))

        return {
            "title": title,
            "company": prettify_company(j["company"]),
            "url": j["url"],
            "location": loc or ("Remote" if remote else ""),
            "mode": mode,
            "posted": j.get("posted", ""),
            "source": j.get("source", ""),
            "fit": score,
            "matched": [k for k, _ in hits][:3] + prof_hits[:5],
            "notes": notes,
        }


def prettify_company(c):
    c = (c or "").strip()
    if c and c == c.lower() and " " not in c:
        c = re.sub(r"[-_]+", " ", c).title()
    return c


def key_of(j):
    return re.sub(r"\W+", "", (j["company"] + "|" + j["title"]).lower())


def main():
    profile = load_yaml("profile.yml")
    matcher = Matcher(profile)
    urls = load_company_urls()
    started = time.time()

    raw, health = [], []

    def run(name, fn, *args):
        t = time.time()
        try:
            res = fn(*args)
            return name, res, None, time.time() - t
        except Exception as e:
            return name, [], f"{type(e).__name__}: {str(e)[:120]}", time.time() - t

    tasks = [(u, sources.fetch_company, u) for u in urls] + [(n, f) for n, f in sources.BOARDS.items()]
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(run, t[0], *t[1:]) for t in tasks]
        for fut in as_completed(futs):
            name, res, err, dt = fut.result()
            raw.extend(res)
            health.append({"source": name, "postings": len(res), "error": err, "seconds": round(dt, 1)})

    kept = {}
    for j in raw:
        try:
            e = matcher.evaluate(j)
        except Exception as ex:  # a single bad posting never stops the run
            print("skip posting:", ex)
            continue
        if not e:
            continue
        k = key_of(e)
        if k not in kept or e["fit"] > kept[k]["fit"]:
            kept[k] = e

    # remember when each posting was first seen
    seen_path = os.path.join(ROOT, "data", "seen.json")
    try:
        with open(seen_path, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    today = datetime.now(IST).strftime("%Y-%m-%d")
    for k, e in kept.items():
        seen.setdefault(k, today)
        e["first_seen"] = seen[k]
    cutoff = (datetime.now(IST) - timedelta(days=180)).strftime("%Y-%m-%d")
    seen = {k: v for k, v in seen.items() if k in kept or v >= cutoff}

    jobs = sorted(kept.values(), key=lambda e: (e["fit"], e["first_seen"], e["posted"]), reverse=True)

    health.sort(key=lambda h: (h["error"] is None, h["postings"]))
    out = {
        "updated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "count": len(jobs),
        "scanned_postings": len(raw),
        "sources_ok": sum(1 for h in health if not h["error"]),
        "sources_total": len(health),
        "jobs": jobs,
        "health": health,
    }
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "docs", "jobs.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=0, sort_keys=True)

    bad = [h for h in health if h["error"]]
    print(f"Scanned {len(raw)} postings from {len(health)} sources ({len(bad)} failed) in {time.time() - started:.0f}s")
    print(f"Kept {len(jobs)} matching internships")
    for h in bad:
        print("  ✗", h["source"], "—", h["error"])


if __name__ == "__main__":
    main()
