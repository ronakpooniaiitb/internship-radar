# Internship Radar

A personal tool that finds **internships** in product, AI product management, founder's office, strategy, growth and operations:
**remote from anywhere** (as long as India isn't excluded) or **on-site in India**. It runs by itself on GitHub twice a day. No AI, no paid services.

**Your webpage:** see the link in this repository's "About" box (top right), or Settings → Pages.

## How it works
1. Every day at ~06:47 and ~18:47 IST, GitHub runs `scanner/scan.py`.
2. It reads job listings from the companies in `companies.txt` (their Greenhouse / Lever / Ashby / Workable / SmartRecruiters job boards) and from remote job sites (Remotive, RemoteOK, Himalayas, Jobicy, Arbeitnow, We Work Remotely).
3. It keeps only internships whose title matches your roles, drops full-time, engineering and off-region roles, and gives each a fit score (1–10) using `profile.yml`.
4. The results go to `docs/jobs.json`, which the webpage shows with **Apply** links.

## Changing it (no coding)
- **Roles/keywords/eligibility:** edit `profile.yml` → pencil icon → change → "Commit changes". A new scan starts automatically.
- **Add a company:** edit `companies.txt` and paste its careers link (instructions at the top of the file).
- **Run a scan now:** Actions tab → "Scan for internships" → "Run workflow".
- **Broken sources:** shown under "Source health" at the bottom of the webpage.

## Limits
LinkedIn, Internshala, Wellfound and Naukri don't allow automated collection, so they're not scanned. Use their own job alerts alongside this.
