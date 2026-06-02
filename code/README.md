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
