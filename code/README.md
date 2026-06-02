# Code

## `fetch_wfh_data.py` — work-from-home shares for the website

Builds the real numbers behind the site's "Remote Work Reality Check" calculator.

It downloads the U.S. Census Bureau's American Community Survey (ACS) 1-year
Public Use Microdata Sample (PUMS) CSV files, joins the person and housing
records on `SERIALNO`, and computes the weighted share of workers who worked
from home, broken down by sex, presence of a young child in the household, and
education.

To avoid conflating "works from home" with "has a job that can be done from
home," the universe is restricted to remote-capable (teleworkable) occupations.
The grouping follows Dingel & Neiman (2020): the script keeps the `OCCP` major
groups that are predominantly teleworkable (management, business and financial,
computer and math, architecture and engineering, science, community and social
service, legal, education, arts and media, and office and administrative
support) and drops the rest. This strips out most of the white-collar versus
blue-collar confound, so the resulting share reflects who *uses* remote work
among those who *could*.

The output is written to [`../website/data/wfh.js`](../website/data/wfh.js) as a
`window.WFH_DATA` global that the static site reads with a plain `<script>` tag.
Only the small computed summary ships to the browser; the raw microdata is never
stored in the repo.

### Requirements

- Python 3 (standard library only; no `pip install` needed).
- An internet connection (downloads roughly 850 MB of CSV files, processed in a
  stream and then discarded).

### Refresh the data

```bash
python3 code/fetch_wfh_data.py
```

The script automatically uses the latest available ACS 1-year year (currently
2023, with fallback to older years). It prints a summary table and overwrites
`website/data/wfh.js`. Re-run it whenever a new ACS year is released.

### Notes

- The Census API now requires a key, so this script uses the key-free bulk PUMS
  CSV downloads instead.
- Definitions: "worked from home" is the ACS commute-mode response (`JWTRNS`);
  "young child" is a household with an own child under 6 (`HUPAC` 1 or 2);
  "bachelor's or higher" is `SCHL` 21+; "remote-capable" is an `OCCP` in a
  predominantly teleworkable major group (Dingel & Neiman 2020); the universe is
  people employed and at work in the reference week (`ESR` = 1) with a reported
  commute mode in a remote-capable occupation.

## `fetch_cps_retention.py` — 12-month job-retention panel

Builds the real numbers behind the site's **Finding** section: do mothers of
young children who work remotely *stay employed* longer than comparable mothers
who do not?

The ACS calculator is a single cross-section, so it can only show who *uses*
remote work, not what it does over time. Answering the retention question
requires following the same people. The Current Population Survey (CPS) makes
this possible without an API key. CPS uses a 4-8-4 rotation: a household is
interviewed for 4 months, rests for 8, then returns for 4 more. A person in
month-in-sample (MIS) 1–4 of month *m* therefore reappears in MIS 5–8 exactly 12
months later. The script downloads the public-use basic monthly CSVs, matches
month *m* to month *m+12* on the household and person identifiers (`HRHHID`,
`HRHHID2`, `PULINENO`), and validates each match on sex and age.

- **Treatment**: teleworked at home in the reference week at baseline
  (`PTTLWK = 1`), a permanent CPS item that began **June 2024** — hence baselines
  start there. On-site comparison is employed but not teleworking (`PTTLWK = 2`).
- **Outcome**: still employed (`PEMLR` 1–2), and separately still in the labor
  force (`PEMLR` 1–4), 12 months later.
- **Groups**: sex × young child (own child under 6, `PRCHLD`) × telework status;
  ages 18–50; weighted by the baseline final person weight `PWSSWGT`.
- **Window**: 10 baseline months, June 2024–April 2025 (October 2024 skipped
  because the October 2025 follow-up file is not published), matched to
  June 2025–April 2026.

Output is written to [`../website/data/retention.js`](../website/data/retention.js)
as a `window.RETENTION_DATA` global. Only the computed summary ships; raw
microdata is streamed and discarded.

```bash
python3 code/fetch_cps_retention.py
```

It prints per-pair match rates, a retention table, and the headline remote-vs-
on-site gaps, then overwrites `website/data/retention.js`. Match rates run
~56–63%, typical for matched CPS panels.

**Important caveat (stated on the site too):** this is a descriptive
association, not a causal estimate. Teleworkers differ from on-site workers in
occupation, schedule, and unobserved ways, so part of the retention gap reflects
selection rather than the effect of remote work itself.
