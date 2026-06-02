#!/usr/bin/env python3
"""Build real work-from-home shares from Census ACS 1-year PUMS CSV files.

The Census API now requires a key, but the same PUMS microdata is published as
key-free CSV downloads. This script streams the per-state person and housing
files, joins them on SERIALNO, and computes the weighted share of workers who
worked from home, broken down by sex, presence of young children in the
household, and education.

To avoid conflating "works from home" with "holds a job that can be done from
home," the universe is restricted to remote-capable (teleworkable) occupations.
The teleworkability grouping follows Dingel & Neiman (2020), "How many jobs can
be done at home?", classifying the occupation major groups that are
predominantly teleworkable (management, business and financial, computer and
math, architecture and engineering, science, community and social service,
legal, education, arts and media, and office and administrative support).

Output: website/data/wfh.js (a `window.WFH_DATA` global) so the static site can
read it with a plain <script> tag, working both locally and on Vercel.

No third-party packages are required. To refresh:

    python3 code/fetch_wfh_data.py
"""

import csv
import io
import json
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import date

BASE = "https://www2.census.gov/programs-surveys/acs/data/pums/%d/1-Year/"
CANDIDATE_YEARS = [2023, 2022, 2021]
WORKED_FROM_HOME_CODE = 11  # JWTRNS: worked from home (2019+ coding)

# Remote-capable occupations, by ACS OCCP code range (which tracks the SOC
# major groups). Following Dingel & Neiman (2020), we keep the major groups that
# are predominantly teleworkable and drop those that mostly cannot be done from
# home (healthcare practitioners, all service occupations, sales, farming,
# construction, maintenance, production, transportation, and military).
TELEWORK_OCCP_RANGES = [
    (10, 440),     # Management (SOC 11)
    (500, 960),    # Business, financial operations (SOC 13)
    (1005, 1240),  # Computer and mathematical (SOC 15)
    (1305, 1560),  # Architecture and engineering (SOC 17)
    (1600, 1980),  # Life, physical, and social science (SOC 19)
    (2001, 2060),  # Community and social service (SOC 21)
    (2100, 2180),  # Legal (SOC 23)
    (2205, 2555),  # Educational instruction and library (SOC 25)
    (2600, 2920),  # Arts, design, entertainment, sports, media (SOC 27)
    (5000, 5940),  # Office and administrative support (SOC 43)
]


def is_teleworkable(occp):
    if occp is None:
        return False
    return any(lo <= occp <= hi for lo, hi in TELEWORK_OCCP_RANGES)

# 50 states + DC (postal abbreviations, lowercase) to match PUMS file names.
STATES = [
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "dc", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn",
    "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh",
    "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa",
    "wv", "wi", "wy",
]


def make_ssl_context():
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        pass
    for path in ("/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem",
                 "/opt/homebrew/etc/openssl@3/cert.pem"):
        if os.path.exists(path):
            try:
                return ssl.create_default_context(cafile=path)
            except Exception:  # noqa: BLE001
                continue
    print("  Warning: no CA bundle found; proceeding without TLS verification.")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


SSL_CONTEXT = make_ssl_context()


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "econ30-capstone"})
    with urllib.request.urlopen(req, timeout=300, context=SSL_CONTEXT) as resp:
        with open(dest, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)


def open_csv_from_zip(zip_path):
    zf = zipfile.ZipFile(zip_path)
    name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
    stream = io.TextIOWrapper(zf.open(name, "r"), encoding="latin-1", newline="")
    return zf, csv.reader(stream)


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def url_ok(url):
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "econ30-capstone"})
        with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def pick_year():
    for year in CANDIDATE_YEARS:
        if url_ok((BASE % year) + "csv_hwy.zip"):
            return year
    raise RuntimeError("No ACS 1-year PUMS CSV year available.")


def cell_key(sex, kids, ba):
    return "%s|%s|%s" % (
        "women" if sex == 2 else "men",
        "kids" if kids else "nokids",
        "ba" if ba else "noba",
    )


def process_state(year, abbr, cells, tmpdir):
    base = BASE % year

    # Housing file -> map SERIALNO to "has own child under 6" (HUPAC 1 or 2).
    h_zip = os.path.join(tmpdir, "h_%s.zip" % abbr)
    download(base + "csv_h%s.zip" % abbr, h_zip)
    zf_h, reader_h = open_csv_from_zip(h_zip)
    header_h = next(reader_h)
    hi = {n: i for i, n in enumerate(header_h)}
    young_kids = {}
    for row in reader_h:
        hupac = to_int(row[hi["HUPAC"]])
        young_kids[row[hi["SERIALNO"]]] = hupac in (1, 2)
    zf_h.close()
    os.remove(h_zip)

    # Person file -> accumulate weighted WFH shares.
    p_zip = os.path.join(tmpdir, "p_%s.zip" % abbr)
    download(base + "csv_p%s.zip" % abbr, p_zip)
    zf_p, reader_p = open_csv_from_zip(p_zip)
    header_p = next(reader_p)
    pi = {n: i for i, n in enumerate(header_p)}
    for row in reader_p:
        if to_int(row[pi["ESR"]]) != 1:
            continue  # employed, at work in the reference week
        jw = to_int(row[pi["JWTRNS"]])
        if jw is None or jw < 1 or jw > 12:
            continue  # no valid commute response
        if not is_teleworkable(to_int(row[pi["OCCP"]])):
            continue  # restrict to remote-capable occupations
        weight = to_int(row[pi["PWGTP"]]) or 0
        sex = to_int(row[pi["SEX"]])
        schl = to_int(row[pi["SCHL"]])
        ba = schl is not None and schl >= 21
        kids = young_kids.get(row[pi["SERIALNO"]], False)
        key = cell_key(sex, kids, ba)
        entry = cells.setdefault(key, {"num": 0, "den": 0, "n": 0})
        entry["den"] += weight
        entry["n"] += 1
        if jw == WORKED_FROM_HOME_CODE:
            entry["num"] += weight
    zf_p.close()
    os.remove(p_zip)


def main():
    print("Building work-from-home shares from Census ACS 1-year PUMS CSVs...")
    year = pick_year()
    print("Using ACS %d 1-year PUMS.\n" % year)

    cells = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, abbr in enumerate(STATES, start=1):
            process_state(year, abbr, cells, tmpdir)
            sys.stdout.write("\r  Processed %d/%d states (%s)        "
                             % (i, len(STATES), abbr.upper()))
            sys.stdout.flush()
    print("\n")

    out_cells = {}
    for key, entry in sorted(cells.items()):
        share = (100.0 * entry["num"] / entry["den"]) if entry["den"] else 0.0
        out_cells[key] = {"share": round(share, 1), "nUnweighted": entry["n"]}

    payload = {
        "year": year,
        "source": "U.S. Census Bureau, American Community Survey 1-year PUMS",
        "metric": "Share of workers in remote-capable jobs who worked from home (commute mode)",
        "definitions": {
            "kids": "Lives in a household with an own child under age 6 (HUPAC 1 or 2).",
            "ba": "Bachelor's degree or higher (SCHL 21 or above).",
            "universe": "Employed, at work in the reference week (ESR = 1), with a reported commute mode, in a remote-capable occupation.",
            "teleworkable": "Occupation in a predominantly teleworkable major group, following Dingel & Neiman (2020).",
        },
        "cells": out_cells,
        "generatedAt": date.today().isoformat(),
    }

    print("Weighted work-from-home shares (%d):" % year)
    for key in sorted(out_cells):
        c = out_cells[key]
        print("  %-18s %5.1f%%   (n=%d)" % (key, c["share"], c["nUnweighted"]))

    out_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "website", "data"))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "wfh.js")
    with open(out_path, "w") as fh:
        fh.write("// Auto-generated by code/fetch_wfh_data.py. Do not edit by hand.\n")
        fh.write("// Source: %s (%d).\n" % (payload["source"], year))
        fh.write("window.WFH_DATA = ")
        fh.write(json.dumps(payload, indent=2))
        fh.write(";\n")
    print("\nWrote %s" % out_path)


if __name__ == "__main__":
    main()
