#!/usr/bin/env python3
"""Decide whether a make-it-better run should stop. Pure logic.

A row is one scored version:
  {"pass": 0, "total": 62.0, "dims": {"clarity": {"score": 10, "max": 15}}, "sha": "abc"}

By default a run uses every iteration. With auto_stop on, it can also stop at the target,
on a regression, on diminishing returns, or when the maker changes nothing.
"""


def floor_failures(row, floor_pct):
    return [k for k, d in row["dims"].items() if d["score"] < d["max"] * floor_pct / 100]


def auto_decision(rows, target=80, min_gain=2.0, window=2, floor_pct=50, regress_drop=5.0):
    """Return (reason, detail) for the auto-stop checks. reason 'continue' means keep going."""
    if not rows:
        return "continue", "no scored version yet"
    latest = rows[-1]
    best = max(rows, key=lambda r: r["total"])
    low = floor_failures(latest, floor_pct)
    if latest["total"] >= target and not low:
        return "target_met", f"{latest['total']:.1f} >= {target}, no area under {floor_pct}%"
    if len(rows) >= 3 and all(r["total"] <= best["total"] - regress_drop for r in rows[-2:]):
        return "regressed", f"last 2 versions are {regress_drop}+ below the best, {best['total']:.1f}"
    # Needs window + 1 rows, so a single quiet pass never ends a run.
    if len(rows) > window:
        before = max(r["total"] for r in rows[:-window])
        recent = max(r["total"] for r in rows[-window:])
        if recent - before < min_gain:
            return "diminishing_returns", f"best of the last {window} versions gained {recent - before:.1f}, under {min_gain}"
    return "continue", f"total {latest['total']:.1f}"


def decide(rows, iteration, cfg, no_change=False):
    """Return (reason, detail). The iteration cap always applies, whatever auto_stop says."""
    limit = int(cfg.get("iterations", 5))
    if cfg.get("auto_stop", False):
        if no_change:
            return "no_change", "the maker found nothing that would clearly help"
        reason, detail = auto_decision(rows, cfg.get("target", 80), cfg.get("min_gain", 2.0), cfg.get("window", 2))
        if reason != "continue":
            return reason, detail
    if iteration >= limit:
        return "iterations_reached", f"{iteration} of {limit} iterations done"
    return "continue", f"{iteration} of {limit} iterations done"
