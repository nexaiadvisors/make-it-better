#!/usr/bin/env python3
"""Score the current commit of a web project once, record it, and print the next step.

Usage: score.py --config <run_dir>/config.json --iteration <n>
Use iteration 0 for the baseline. The last output line is CONTINUE or STOP <reason>.
Scoring the same commit twice reuses the score, so a retry does not spend judge runs.
"""
import argparse
import json
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from stop_rule import decide

SKILL = pathlib.Path(__file__).resolve().parent.parent
LIGHTHOUSE = "lighthouse@12.8.2"
VIEWPORTS = [(1280, 800), (375, 812)]
JUDGED = {"clarity": 15, "design": 20, "trust": 10, "conversion": 15, "mobile": 10}


def run(cmd, timeout=180, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def reachable(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status < 500
    except Exception:
        return False


def slug(page):
    return re.sub(r"[^a-z0-9]+", "-", page.lower()).strip("-") or "home"


def loaded(ab, url, page, width, warnings):
    """Judges must not score a half-loaded page, so wait, retry once, and record a failure."""
    check = ("performance.getEntriesByType('resource').filter(r=>r.name.startsWith(location.origin)"
             "&&(r.responseStatus===0||r.responseStatus>=400)).length"
             "+[...document.images].filter(i=>i.loading!=='lazy'&&i.complete&&!i.naturalWidth&&i.getClientRects().length).length")
    for attempt in range(2):
        ab("wait", "--load", "networkidle")
        ab("eval", "document.fonts.ready.then(()=>1)")
        time.sleep(1.5)
        if not json.loads(ab("eval", check)):
            return
        if attempt == 0:
            ab("open", url)
    warnings.append(f"{page} at {width}px loaded incompletely")


def capture(cfg, out):
    """Screenshot each page at each viewport, and save its visible text."""
    s = f"site-score-{out.name}"
    def ab(*args):
        r = run(["agent-browser", "--session", s, *args], timeout=60)
        if r.returncode:
            raise RuntimeError(f"agent-browser {args[0]}: {(r.stdout + r.stderr)[:200]}")
        return r.stdout
    shots, overflow, warnings = [], [], []
    try:
        for page in cfg["pages"]:
            url = cfg["base_url"].rstrip("/") + page
            for w, h in VIEWPORTS:
                ab("set", "viewport", str(w), str(h))
                ab("open", url)
                loaded(ab, url, page, w, warnings)
                path = out / f"{slug(page)}-{w}.png"
                ab("screenshot", str(path), "--full")
                shots.append(str(path))
                if json.loads(ab("eval", "document.documentElement.scrollWidth>innerWidth+1")):
                    overflow.append(f"{page} at {w}px")
            text = json.loads(ab("eval", "document.body.innerText"))
            (out / f"{slug(page)}.txt").write_text(text)
    finally:
        run(["agent-browser", "--session", s, "close"], timeout=30)
    return shots, overflow, warnings


def lighthouse(cfg, out):
    """Average Lighthouse categories across pages. Points: speed 10, accessibility 10, technical 10."""
    cats = {"performance": [], "accessibility": [], "best-practices": [], "seo": []}
    for page in cfg["pages"]:
        path = out / f"lh-{slug(page)}.json"
        r = run(["npx", "-y", LIGHTHOUSE, cfg["base_url"].rstrip("/") + page, "--quiet", "--output=json",
                 f"--output-path={path}", '--chrome-flags=--headless=new',
                 "--only-categories=performance,accessibility,best-practices,seo"], timeout=240)
        if r.returncode or not path.exists():
            raise RuntimeError(f"lighthouse failed on {page}: {r.stderr[:200]}")
        d = json.loads(path.read_text())["categories"]
        for k in cats:
            if d[k]["score"] is None:
                raise RuntimeError(f"lighthouse returned no {k} score for {page}")
            cats[k].append(d[k]["score"])
    m = {k: statistics.mean(v) for k, v in cats.items()}
    return {"speed": {"score": round(m["performance"] * 10, 1), "max": 10},
            "accessibility": {"score": round(m["accessibility"] * 10, 1), "max": 10},
            "technical": {"score": round((m["best-practices"] + m["seo"]) * 5, 1), "max": 10}}


def judge_once(cfg, shots, out):
    rubric = (SKILL / "references" / "judge-rubric.md").read_text()
    texts = "\n".join(str(p) for p in sorted(out.glob("*.txt")))
    prompt = (f"{rubric}\n\nImages:\n" + "\n".join(shots) + f"\n\nVisible text files:\n{texts}\n\n"
              f"Stated purpose of this site: {cfg.get('purpose', 'not stated')}\n"
              f"Named bar: {cfg.get('bar', 'none')}\n")
    judge_cli = cfg.get("judge", "auto")
    if judge_cli == "auto":
        judge_cli = "codex" if shutil.which("codex") else "claude"
    if judge_cli == "codex":
        cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only"]
        if cfg.get("judge_model"):
            cmd += ["-m", cfg["judge_model"]]
        for p in shots:
            cmd += ["-i", p]
        cmd += ["--", prompt]
    else:
        # --tools limits what exists. --allowedTools only pre-approves, so user allow rules would still apply.
        cmd = ["claude", "-p", prompt, "--tools", "Read", "--setting-sources", "", "--strict-mcp-config",
               "--no-session-persistence"]
        if cfg.get("judge_model"):
            cmd += ["--model", cfg["judge_model"]]
    r = run(cmd, timeout=420, stdin=subprocess.DEVNULL, cwd=str(out))
    found = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", r.stdout)
    for block in reversed(found):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        if all(isinstance(d.get(k), (int, float)) and 0 <= d[k] <= m for k, m in JUDGED.items()):
            return d
    raise RuntimeError(f"judge returned no valid score: {(r.stdout + r.stderr)[-300:]}")


def clean_gap(text):
    """Judge text can echo page text, so keep it short, one line, and out of stdout."""
    return re.sub(r"[^\x20-\x7e]+", " ", str(text)).strip()[:200]


def judge(cfg, shots, out):
    runs = [judge_once(cfg, shots, out) for _ in range(int(cfg.get("judges", 3)))]
    (out / "judges.json").write_text(json.dumps(runs, indent=1))
    dims = {k: {"score": statistics.median(r[k] for r in runs), "max": m} for k, m in JUDGED.items()}
    spread = max(sum(r[k] for k in JUDGED) for r in runs) - min(sum(r[k] for k in JUDGED) for r in runs)
    return dims, spread, clean_gap(runs[-1].get("biggest_gap", ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--iteration", type=int, required=True)
    a = ap.parse_args()
    cfg = json.loads(pathlib.Path(a.config).read_text())
    run_dir = pathlib.Path(cfg["run_dir"])
    ledger = run_dir / "ledger.jsonl"
    rows = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()] if ledger.exists() else []
    sha = run(["git", "rev-parse", "HEAD"], cwd=cfg["workdir"]).stdout.strip()
    server = None
    already_scored = any(r["sha"] == sha for r in rows)
    if not already_scored:
        out = run_dir / f"pass-{len(rows)}"
        out.mkdir(parents=True, exist_ok=True)
        if not reachable(cfg["base_url"]):
            if not cfg.get("start_cmd"):
                raise RuntimeError(f"{cfg['base_url']} is not reachable and config has no start_cmd")
            server = subprocess.Popen(cfg["start_cmd"], shell=True, cwd=cfg["workdir"], start_new_session=True,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(60):
                if reachable(cfg["base_url"]):
                    break
                time.sleep(2)
            else:
                raise RuntimeError("start_cmd did not bring the site up within 120 seconds")
        try:
            shots, overflow, warnings = capture(cfg, out)
            dims = lighthouse(cfg, out)
            judged, spread, gap = judge(cfg, shots, out)
        finally:
            if server:
                import os, signal
                os.killpg(server.pid, signal.SIGTERM)
        dims.update(judged)
        rows.append({"pass": len(rows), "iteration": a.iteration, "sha": sha, "total": round(sum(d["score"] for d in dims.values()), 1),
                     "dims": dims, "judge_spread": spread, "biggest_gap": gap, "overflow": overflow,
                     "capture_warnings": warnings})
        with ledger.open("a") as f:
            f.write(json.dumps(rows[-1]) + "\n")
    latest = rows[-1]
    reason, detail = decide(rows, a.iteration, cfg, no_change=already_scored and a.iteration > 0)
    weakest = min(latest["dims"], key=lambda k: latest["dims"][k]["score"] / latest["dims"][k]["max"])
    best = max(rows, key=lambda r: r["total"])
    (run_dir / "decision.json").write_text(json.dumps({"reason": reason, "detail": detail, "weakest": weakest,
                                                       "best_sha": best["sha"], "best_pass": best["pass"], "latest": latest}, indent=1))
    print(f"iteration={a.iteration} total={latest['total']} best={best['total']} weakest={weakest} "
          f"judge_spread={latest['judge_spread']} overflow={latest['overflow']} "
          f"capture_warnings={latest.get('capture_warnings', [])}")
    print(f"CONTINUE ({detail})" if reason == "continue" else f"STOP {reason} ({detail})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        print(f"score failed: {e}", file=sys.stderr)
        sys.exit(1)
