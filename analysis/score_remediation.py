#!/usr/bin/env python3
"""
score_remediation.py — apply the pre-registered remediation rubric.

Implements REMEDIATION_RUBRIC.md exactly. Run AFTER the rubric is
committed; the rubric is the specification, this file only executes it.

Usage:
    python analysis/score_remediation.py data/
"""

import json
import re
import sys
import collections
from pathlib import Path

# --------------------------------------------------------------------------
# Action taxonomy (rubric section "Action taxonomy")
# --------------------------------------------------------------------------

def classify_action(action: str) -> str:
    """Map a kubectl command string to one action class."""
    a = " ".join(action.lower().split())

    if re.search(r"\b(autoscale|hpa)\b", a) or re.search(r"--replicas", a):
        return "SCALE_HORIZONTAL"
    if re.search(r"\bscale\b", a):
        return "SCALE_HORIZONTAL"
    if re.search(r"\bset\s+resources\b", a) or re.search(r"--limits|--requests", a):
        return "SCALE_VERTICAL"
    if re.search(r"\brollout\s+undo\b", a):
        return "ROLLBACK"
    if re.search(r"\brollout\s+restart\b", a) or re.search(r"\bdelete\s+pod", a):
        return "RESTART"
    if re.search(r"\b(patch|edit|apply|create)\b", a):
        return "CONFIG_PATCH"
    if re.search(r"\b(get|describe|logs|top|explain|events)\b", a):
        return "DIAGNOSTIC"
    return "OTHER"


# --------------------------------------------------------------------------
# Per-scenario tier mapping (rubric section "Per-scenario mapping")
# tier: 2 = Appropriate, 1 = Partial, 0 = Inappropriate
# --------------------------------------------------------------------------

TIERS = {
    "S1_CPU_STRESS": {
        "SCALE_VERTICAL": 2, "SCALE_HORIZONTAL": 2,
        "RESTART": 1, "DIAGNOSTIC": 1,
        "ROLLBACK": 0, "CONFIG_PATCH": 0, "OTHER": 0,
    },
    "S2_POD_CRASH": {
        "DIAGNOSTIC": 2,
        "RESTART": 1,
        "SCALE_HORIZONTAL": 0, "SCALE_VERTICAL": 0,
        "ROLLBACK": 0, "CONFIG_PATCH": 0, "OTHER": 0,
    },
    "S3_MEMORY_PRESSURE": {
        "RESTART": 2, "SCALE_VERTICAL": 2,
        "SCALE_HORIZONTAL": 1, "DIAGNOSTIC": 1,
        "ROLLBACK": 0, "CONFIG_PATCH": 0, "OTHER": 0,
    },
    "S4_NETWORK_LATENCY": {
        "RESTART": 2,
        "DIAGNOSTIC": 1,
        "SCALE_HORIZONTAL": 0, "SCALE_VERTICAL": 0,
        "ROLLBACK": 0, "CONFIG_PATCH": 0, "OTHER": 0,
    },
    # S5: tier depends on BOTH target and action (see rubric).
    # Restoration of redis-cart = 2; inspection of redis-cart = 1;
    # anything aimed elsewhere = 0. Handled in score_cycle().
    "S5_CASCADING_FAILURE": None,
    "S6_NOISY_BASELINE": {
        "DIAGNOSTIC": 2,
        "SCALE_HORIZONTAL": 0, "SCALE_VERTICAL": 0, "RESTART": 0,
        "ROLLBACK": 0, "CONFIG_PATCH": 0, "OTHER": 0,
    },
}

# Scenarios where a LOW-risk step is expected (rubric: "Handling plans
# with no LOW-risk step")
STEP_EXPECTED = {
    "S1_CPU_STRESS": True,
    "S2_POD_CRASH": False,
    "S3_MEMORY_PRESSURE": True,
    "S4_NETWORK_LATENCY": True,
    "S5_CASCADING_FAILURE": True,
    "S6_NOISY_BASELINE": False,
}

RESTORATIVE = {"RESTART", "SCALE_HORIZONTAL", "SCALE_VERTICAL", "CONFIG_PATCH"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def first_low_step(verdict):
    """First step tagged LOW risk. Risk tags are free-text in places, so
    normalise before matching."""
    for s in (verdict.get("remediation_steps") or []):
        risk = str(s.get("risk", "")).strip().upper()
        if risk.startswith("LOW"):
            return s
    return None


def targets_service(action: str, service: str) -> bool:
    """Does the command reference the expected service by name?"""
    if not service or service == "none":
        return False
    return re.search(rf"(?<![\w-]){re.escape(service)}(?![\w-])", action, re.I) is not None


def has_placeholder(action: str) -> bool:
    """Unresolved angle-bracket placeholder, e.g. <namespace>."""
    return re.search(r"<[^>]{1,40}>", action) is not None


def score_cycle(scenario, expected_service, verdict):
    """Return dict or None if the cycle is not scoreable."""
    if not verdict:
        return None
    if verdict.get("verdict") not in ("ANOMALY_CONFIRMED", "FALSE_POSITIVE"):
        return None

    step = first_low_step(verdict)
    expected = STEP_EXPECTED[scenario]

    if step is None:
        # Rubric: no LOW step is restraint where unexpected, miss where expected
        return {
            "action_class": "NONE",
            "target_correct": None if not expected else False,
            "tier": 0 if expected else 2,
            "placeholder": False,
            "no_step": True,
        }

    action = step.get("action", "")
    cls = classify_action(action)
    tgt = targets_service(action, expected_service)

    if scenario == "S5_CASCADING_FAILURE":
        if not tgt:
            tier = 0
        elif cls in RESTORATIVE:
            tier = 2
        else:
            tier = 1
    else:
        tier = TIERS[scenario].get(cls, 0)

    return {
        "action_class": cls,
        "target_correct": None if expected_service == "none" else tgt,
        "tier": tier,
        "placeholder": has_placeholder(action),
        "no_step": False,
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(data_dir):
    rows = []
    for path in sorted(Path(data_dir).glob("S*.jsonl")):
        scenario = path.stem.upper()
        if scenario not in TIERS:
            print(f"  ! skipping unrecognised file: {path.name}", file=sys.stderr)
            continue
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            sc = score_cycle(scenario,
                             rec.get("expected_root_cause_service"),
                             rec.get("llm_verdict"))
            if sc:
                sc["scenario"] = scenario
                sc["run_id"] = rec.get("run_id")
                rows.append(sc)

    if not rows:
        print("No scoreable plans found. Check the data directory path.")
        return

    print(f"\nScored plans: {len(rows)}\n")
    hdr = (f"{'Scenario':<22} {'n':>4} {'Target':>8} {'Approp.':>9} "
           f"{'Mean tier':>10} {'Full':>7} {'Placehldr':>10}")
    print(hdr)
    print("-" * len(hdr))

    by = collections.defaultdict(list)
    for r in rows:
        by[r["scenario"]].append(r)

    def pct(x, n):
        return f"{100.0*x/n:5.1f}%" if n else "    n/a"

    for scen in sorted(by):
        rs = by[scen]
        n = len(rs)
        tgt_apply = [r for r in rs if r["target_correct"] is not None]
        tgt_ok = sum(1 for r in tgt_apply if r["target_correct"])
        approp = sum(1 for r in rs if r["tier"] == 2)
        mean_tier = sum(r["tier"] for r in rs) / n
        full = sum(1 for r in rs
                   if r["tier"] == 2 and r["target_correct"] is not False)
        ph = sum(1 for r in rs if r["placeholder"])
        print(f"{scen:<22} {n:>4} "
              f"{pct(tgt_ok, len(tgt_apply)):>8} "
              f"{pct(approp, n):>9} "
              f"{mean_tier:>10.2f} "
              f"{pct(full, n):>7} "
              f"{pct(ph, n):>10}")

    n = len(rows)
    tgt_apply = [r for r in rows if r["target_correct"] is not None]
    tgt_ok = sum(1 for r in tgt_apply if r["target_correct"])
    approp = sum(1 for r in rows if r["tier"] == 2)
    full = sum(1 for r in rows
               if r["tier"] == 2 and r["target_correct"] is not False)
    ph = sum(1 for r in rows if r["placeholder"])
    print("-" * len(hdr))
    print(f"{'POOLED':<22} {n:>4} "
          f"{pct(tgt_ok, len(tgt_apply)):>8} "
          f"{pct(approp, n):>9} "
          f"{sum(r['tier'] for r in rows)/n:>10.2f} "
          f"{pct(full, n):>7} "
          f"{pct(ph, n):>10}")

    print("\nAction-class distribution of the scored (first LOW-risk) step:")
    dist = collections.Counter(r["action_class"] for r in rows)
    for k, v in dist.most_common():
        print(f"   {k:<20} {v:>4}  ({100.0*v/n:.1f}%)")

    print("\nColumn notes:")
    print("  Target    — step names the injected fault's service (S6 excluded)")
    print("  Approp.   — action scored tier 2")
    print("  Full      — target correct AND action tier 2")
    print("  Placehldr — command contains an unresolved <placeholder>")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
