#!/usr/bin/env python3
"""
rca_criteria_check.py — compare permissive vs strict RCA scoring.

Permissive: the injected service appears anywhere in `affected_services`.
Strict:     the injected service is named in the `root_cause` narrative.

The paper's Table 5 uses the permissive criterion, scored on the first
ANOMALY_CONFIRMED cycle per run. This script reports both criteria, on
both the first-cycle sample and all scored cycles, so the gap between
them is visible.

Usage:
    python analysis/rca_criteria_check.py data
"""

import json
import re
import sys
import collections
from pathlib import Path

ALL_SERVICES = ["frontend", "cartservice", "currencyservice",
                "paymentservice", "productcatalogservice", "redis-cart"]


def named(text, service):
    if not text or not service or service == "none":
        return False
    return re.search(rf"(?<![\w-]){re.escape(service)}(?![\w-])",
                     text, re.I) is not None


def main(data_dir):
    print()
    print("PART 1 — how many services does the agent list per cycle?")
    print("(a permissive criterion is weak when this number is large)\n")
    print(f"{'Scenario':<24} {'cycles':>7} {'mean listed':>12} {'distribution'}")
    print("-" * 72)

    per_scenario = {}
    for path in sorted(Path(data_dir).glob("S*.jsonl")):
        recs = []
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("llm_verdict"):
                recs.append(r)
        if not recs:
            continue
        per_scenario[path.stem.upper()] = recs
        counts = collections.Counter(
            len(r["llm_verdict"].get("affected_services") or []) for r in recs)
        mean = sum(k * v for k, v in counts.items()) / len(recs)
        dist = " ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
        print(f"{path.stem.upper():<24} {len(recs):>7} {mean:>12.2f}   {dist}")

    print()
    print("PART 2 — permissive vs strict, first confirmed cycle per run")
    print("(this is the sample Table 5 reports)\n")
    print(f"{'Scenario':<24} {'runs':>5} {'permissive':>12} {'strict':>9}")
    print("-" * 56)

    for scen, recs in per_scenario.items():
        by_run = collections.defaultdict(list)
        for r in recs:
            by_run[r.get("run_id")].append(r)
        exp = recs[0].get("expected_root_cause_service")
        if exp == "none":
            print(f"{scen:<24} {'n/a':>5} {'n/a':>12} {'n/a':>9}")
            continue
        perm = strict = 0
        for run in sorted(by_run):
            first = by_run[run][0]["llm_verdict"]
            if exp in (first.get("affected_services") or []):
                perm += 1
            if named(first.get("root_cause", ""), exp):
                strict += 1
        n = len(by_run)
        print(f"{scen:<24} {n:>5} {f'{perm}/{n}':>12} {f'{strict}/{n}':>9}")

    print()
    print("PART 3 — permissive vs strict, ALL scored cycles")
    print("(the fuller picture; Table 5 does not report this)\n")
    print(f"{'Scenario':<24} {'n':>5} {'permissive':>12} {'strict':>9}")
    print("-" * 56)

    tp = ts = tn = 0
    for scen, recs in per_scenario.items():
        exp = recs[0].get("expected_root_cause_service")
        if exp == "none":
            print(f"{scen:<24} {'n/a':>5} {'n/a':>12} {'n/a':>9}")
            continue
        perm = sum(1 for r in recs
                   if exp in (r["llm_verdict"].get("affected_services") or []))
        strict = sum(1 for r in recs
                     if named(r["llm_verdict"].get("root_cause", ""), exp))
        n = len(recs)
        tp += perm; ts += strict; tn += n
        print(f"{scen:<24} {n:>5} "
              f"{f'{perm}/{n} ({100*perm/n:.0f}%)':>12} "
              f"{f'{strict}/{n} ({100*strict/n:.0f}%)':>9}")

    if tn:
        print("-" * 56)
        print(f"{'POOLED':<24} {tn:>5} "
              f"{f'{tp}/{tn} ({100*tp/tn:.0f}%)':>12} "
              f"{f'{ts}/{tn} ({100*ts/tn:.0f}%)':>9}")
    print()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
