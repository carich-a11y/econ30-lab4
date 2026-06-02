#!/usr/bin/env python3
"""Build a 12-month job-retention panel from CPS basic monthly microdata.

The question the calculator could not answer with a single cross-section is the
one the project actually cares about: do mothers of young children who work
remotely *stay employed* longer than comparable mothers who do not? Answering it
requires following the same people over time.

The Current Population Survey makes this possible without an API key. CPS uses a
4-8-4 rotation: a household is interviewed for 4 months, rests for 8, then
returns for 4 more. A person in month-in-sample (MIS) 1-4 of month *m* therefore
reappears in MIS 5-8 exactly 12 months later. By matching the public-use CSVs of
month *m* to month *m+12* on the household and person identifiers (and validating
on sex and age), we observe each worker's labor-force status a year apart.

Treatment is measured with PTTLWK ("teleworked at home"), a permanent CPS item
that began in June 2024, so baselines start there. The retention outcome is
whether a worker who was employed at baseline is still employed (or still in the
labor force) 12 months later.

Output: website/data/retention.js (a `window.RETENTION_DATA` global) so the
static site can read it with a plain <script> tag.

No third-party packages are required. To refresh:

    python3 code/fetch_cps_retention.py
"""

import csv
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date

BASE = "https://www2.census.gov/programs-surveys/cps/datasets/%d/basic/%s%02dpub.csv"
MONTH_ABBR = ["jan", "feb", "mar", "apr", "may", "jun",
              "jul", "aug", "sep", "oct", "nov", "dec"]

# Baseline -> outcome (12 months later) pairs. Baselines begin June 2024, the
# first month with the permanent telework item, and skip October 2024 because the
# matching October 2025 file is not published. Each baseline's +12-month outcome
# file is confirmed available.
PAIRS = [
    ((2024, 6), (2025, 6)),
    ((2024, 7), (2025, 7)),
    ((2024, 8), (2025, 8)),
    ((2024, 9), (2025, 9)),
    ((2024, 11), (2025, 11)),
    ((2024, 12), (2025, 12)),
    ((2025, 1), (2026, 1)),
    ((2025, 2), (2026, 2)),
    ((2025, 3), (2026, 3)),
    ((2025, 4), (2026, 4)),
]

# PRCHLD categories whose youngest own child is under 6 (codes that include a
# 0-2 or 3-5 age group). PRCHLD == 0 means no own children under 18.
YOUNG_CHILD_CODES = {1, 2, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15}

AGE_MIN, AGE_MAX = 18, 50


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


def url_for(year, month):
    return BASE % (year, MONTH_ABBR[month - 1], year % 100)


def download(url, dest, attempts=3):
    last = None
    for _ in range(attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "econ30-capstone"})
            with urllib.request.urlopen(req, timeout=300,
                                        context=SSL_CONTEXT) as resp:
                with open(dest, "wb") as fh:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
    raise RuntimeError("Download failed for %s: %s" % (url, last))


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def header_index(path):
    with open(path, "r", encoding="latin-1", newline="") as fh:
        header = next(csv.reader(fh))
    return {name.strip().upper(): i for i, name in enumerate(header)}


def person_key(row, idx):
    return "%s|%s|%s" % (
        row[idx["HRHHID"]].strip(),
        row[idx["HRHHID2"]].strip(),
        row[idx["PULINENO"]].strip(),
    )


def sex_label(pesex):
    return "women" if pesex == 2 else "men"


def child_label(prchld):
    if prchld in YOUNG_CHILD_CODES:
        return "youngch"
    if prchld == 0:
        return "nochild"
    return None  # older children only -> excluded from the clean contrast


def read_baseline(path):
    """Return matchable employed workers keyed by household/person id.

    Restricted to MIS 1-4 (the only rotation groups that return 12 months later)
    and to employed workers who answered the telework item.
    """
    idx = header_index(path)
    people = {}
    with open(path, "r", encoding="latin-1", newline="") as fh:
        reader = csv.reader(fh)
        next(reader)
        for row in reader:
            mis = to_int(row[idx["HRMIS"]])
            if mis is None or mis > 4:
                continue
            if to_int(row[idx["PEMLR"]]) != 1:  # employed, at work
                continue
            telework = to_int(row[idx["PTTLWK"]])
            if telework not in (1, 2):
                continue
            age = to_int(row[idx["PRTAGE"]])
            if age is None or age < AGE_MIN or age > AGE_MAX:
                continue
            sex = to_int(row[idx["PESEX"]])
            child = child_label(to_int(row[idx["PRCHLD"]]))
            if sex not in (1, 2) or child is None:
                continue
            people[person_key(row, idx)] = {
                "sex": sex,
                "age": age,
                "mis": mis,
                "child": child,
                "remote": telework == 1,
                "weight": to_float(row[idx["PWSSWGT"]]) or 0.0,
            }
    return people


def accumulate_outcomes(path, baseline, cells):
    """Stream the +12-month file, match people, and tally retention."""
    idx = header_index(path)
    matched = 0
    with open(path, "r", encoding="latin-1", newline="") as fh:
        reader = csv.reader(fh)
        next(reader)
        for row in reader:
            key = person_key(row, idx)
            base = baseline.get(key)
            if base is None:
                continue
            if to_int(row[idx["PESEX"]]) != base["sex"]:
                continue  # guard against id reuse / false match
            age = to_int(row[idx["PRTAGE"]])
            if age is None or age < base["age"] or age > base["age"] + 2:
                continue  # age should advance ~1 year, allow rounding
            mlr = to_int(row[idx["PEMLR"]])
            if mlr is None:
                continue
            matched += 1
            cell_key = "%s|%s|%s" % (
                sex_label(base["sex"]),
                base["child"],
                "remote" if base["remote"] else "onsite",
            )
            cell = cells.setdefault(
                cell_key, {"den": 0.0, "emp": 0.0, "lf": 0.0, "n": 0})
            w = base["weight"]
            cell["den"] += w
            cell["n"] += 1
            if mlr in (1, 2):  # still employed
                cell["emp"] += w
            if mlr in (1, 2, 3, 4):  # still in the labor force
                cell["lf"] += w
    return matched


def pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def main():
    print("Building 12-month CPS retention panel...\n")
    cells = {}
    total_baseline = 0
    total_matched = 0
    with tempfile.TemporaryDirectory() as tmp:
        for (by, bm), (oy, om) in PAIRS:
            b_path = os.path.join(tmp, "base.csv")
            o_path = os.path.join(tmp, "out.csv")
            download(url_for(by, bm), b_path)
            baseline = read_baseline(b_path)
            os.remove(b_path)
            download(url_for(oy, om), o_path)
            matched = accumulate_outcomes(o_path, baseline, cells)
            os.remove(o_path)
            total_baseline += len(baseline)
            total_matched += matched
            print("  %d-%02d -> %d-%02d : baseline %6d  matched %6d  (%.0f%%)"
                  % (by, bm, oy, om, len(baseline), matched,
                     100.0 * matched / len(baseline) if baseline else 0))

    out_cells = {}
    for key, c in sorted(cells.items()):
        out_cells[key] = {
            "retentionEmp": pct(c["emp"], c["den"]),
            "retentionLf": pct(c["lf"], c["den"]),
            "nMatched": c["n"],
        }

    def gap(group):
        r = out_cells.get("%s|remote" % group)
        o = out_cells.get("%s|onsite" % group)
        if not r or not o or r["retentionEmp"] is None or o["retentionEmp"] is None:
            return None
        return {
            "remote": r["retentionEmp"],
            "onsite": o["retentionEmp"],
            "gap": round(r["retentionEmp"] - o["retentionEmp"], 1),
            "nRemote": r["nMatched"],
            "nOnsite": o["nMatched"],
        }

    headline = {
        "mothers": gap("women|youngch"),
        "fathers": gap("men|youngch"),
        "womenNoChild": gap("women|nochild"),
    }

    payload = {
        "source": "U.S. Census Bureau, Current Population Survey basic monthly files",
        "design": "12-month matched panel (CPS 4-8-4 rotation); workers employed "
                  "at baseline matched to their record 12 months later.",
        "baselineWindow": "June 2024 - April 2025 baselines (telework item "
                          "available June 2024+), matched to June 2025 - April 2026.",
        "metric": "Share of baseline-employed workers still employed 12 months later.",
        "definitions": {
            "remote": "Teleworked at home in the reference week at baseline (PTTLWK = 1).",
            "onsite": "Employed but did not telework at baseline (PTTLWK = 2).",
            "youngch": "Has an own child under age 6 (PRCHLD youngest-child codes).",
            "nochild": "No own children under 18 (PRCHLD = 0).",
            "retentionEmp": "Still employed (PEMLR 1-2) 12 months later.",
            "retentionLf": "Still in the labor force (PEMLR 1-4) 12 months later.",
            "ageBand": "Ages %d-%d at baseline." % (AGE_MIN, AGE_MAX),
            "caveat": "Descriptive comparison, not a causal estimate: workers who "
                      "telework differ from those who do not (occupation, schedule, "
                      "selection). Weighted by the baseline final person weight.",
        },
        "cells": out_cells,
        "headline": headline,
        "totals": {"baseline": total_baseline, "matched": total_matched,
                   "matchRate": pct(total_matched, total_baseline)},
        "generatedAt": date.today().isoformat(),
    }

    print("\nRetention by group (still employed 12 months later):")
    for key in sorted(out_cells):
        c = out_cells[key]
        emp = "n/a" if c["retentionEmp"] is None else "%5.1f%%" % c["retentionEmp"]
        print("  %-22s %s   (n=%d)" % (key, emp, c["nMatched"]))

    print("\nHeadline remote vs onsite retention gaps (pp):")
    for name, g in headline.items():
        if g:
            print("  %-14s remote %.1f%%  onsite %.1f%%  gap %+.1f pp  "
                  "(n=%d/%d)" % (name, g["remote"], g["onsite"], g["gap"],
                                 g["nRemote"], g["nOnsite"]))
        else:
            print("  %-14s insufficient sample" % name)

    out_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "website", "data"))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "retention.js")
    import json
    with open(out_path, "w") as fh:
        fh.write("// Auto-generated by code/fetch_cps_retention.py. Do not edit by hand.\n")
        fh.write("// Source: %s.\n" % payload["source"])
        fh.write("window.RETENTION_DATA = ")
        fh.write(json.dumps(payload, indent=2))
        fh.write(";\n")
    print("\nWrote %s" % out_path)


if __name__ == "__main__":
    main()
