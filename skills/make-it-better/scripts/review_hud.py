#!/usr/bin/env python3
"""Serve a local review HUD that switches between every scored version of a site.

Usage: review_hud.py --config <run_dir>/config.json [--port 4400]

The HUD lives outside the site. Each version is exported from git and served on its own port,
because many sites use absolute paths that break inside a subfolder. Everything binds to
127.0.0.1. The list refreshes from the ledger, so new passes appear during a run.
"""
import argparse
import functools
import http.server
import json
import pathlib
import re
import subprocess
import threading

SKILL = pathlib.Path(__file__).resolve().parent.parent


def as_text(value):
    return "\n\n".join(str(x) for x in value) if isinstance(value, list) else str(value)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # Forms must not act like they worked, and nothing is sent anywhere.
        self.send_error(501, "Forms do not run in the review HUD")


class Hud:
    def __init__(self, cfg, port):
        self.cfg, self.port = cfg, port
        self.run_dir = pathlib.Path(cfg["run_dir"])
        self.servers = {}
        self.lock = threading.Lock()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.cfg["workdir"], capture_output=True, text=True).stdout

    def export(self, n, sha):
        """Write one version's files to run_dir/review/v<n>, once."""
        dest = self.run_dir / "review" / f"v{n}"
        serve = dest / self.cfg.get("serve_dir", ".")
        if not dest.exists():
            dest.mkdir(parents=True)
            tar = subprocess.run(["git", "archive", sha], cwd=self.cfg["workdir"], capture_output=True)
            subprocess.run(["tar", "-x", "-C", str(dest)], input=tar.stdout, check=True)
        if not serve.exists():
            raise RuntimeError(f"{serve} is not in commit {sha[:7]}: set serve_dir to a tracked build folder")
        return serve

    def serve(self, n, sha):
        if n not in self.servers:
            handler = functools.partial(Quiet, directory=str(self.export(n, sha)))
            s = http.server.ThreadingHTTPServer(("127.0.0.1", self.port + 1 + n), handler)
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.servers[n] = s
        return self.port + 1 + n

    def changes(self, row, prev):
        if prev is None:
            return {"summary": "The site as it was before the loop started.", "commit": "", "notes": "",
                    "files": [], "stat": "", "reason": "Starting point. Later versions are compared against it."}
        maker = {}
        path = self.run_dir / "notes" / f"iteration-{row.get('iteration', row['pass'])}.json"
        if path.exists():
            maker = json.loads(path.read_text())
        skip = self.cfg.get("serve_dir", ".").rstrip("/") + "/"
        names = [f for f in self.git("diff", "--name-only", prev["sha"], row["sha"]).split("\n")
                 if f and (skip == "./" or not f.startswith(skip))]
        low = min(prev["dims"], key=lambda k: prev["dims"][k]["score"] / prev["dims"][k]["max"])
        d = prev["dims"][low]
        return {"summary": maker.get("summary", ""), "notes": as_text(maker.get("notes", "")), "why": maker.get("why", ""),
                "commit": re.sub(r"(?im)^co-authored-by:.*$", "", self.git("log", "-1", "--format=%B", row["sha"])).strip(),
                "files": names[:25], "stat": f"{len(names)} source files changed",
                "reason": maker.get("why") or f"Weakest area before this version: {low} ({d['score']}/{d['max']}). "
                          f"Judge's biggest gap: {prev.get('biggest_gap') or 'none recorded'}"}

    def versions(self):
        ledger = self.run_dir / "ledger.jsonl"
        rows = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
        best = max(rows, key=lambda r: r["total"])["pass"] if rows else None
        out = []
        with self.lock:
            for i, r in enumerate(rows):
                prev = rows[i - 1] if i else None
                out.append({"n": r["pass"], "sha": r["sha"][:7], "total": r["total"], "dims": r["dims"],
                            "delta": round(r["total"] - prev["total"], 1) if prev else None,
                            "best": r["pass"] == best, "port": self.serve(r["pass"], r["sha"]),
                            "overflow": r.get("overflow", []), **self.changes(r, prev)})
        decision = self.run_dir / "decision.json"
        stop = json.loads(decision.read_text()) if decision.exists() else {}
        return {"pages": self.cfg["pages"], "purpose": self.cfg.get("purpose", ""), "target": self.cfg.get("target", 80) if self.cfg.get("auto_stop") else None,
                "reason": stop.get("reason", ""), "detail": stop.get("detail", ""), "versions": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--port", type=int, default=4400)
    a = ap.parse_args()
    hud = Hud(json.loads(pathlib.Path(a.config).read_text()), a.port)
    page = (SKILL / "assets" / "hud.html").read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *x):
            pass

        def do_GET(self):
            if self.path.startswith("/api/versions"):
                body, kind = json.dumps(hud.versions()).encode(), "application/json"
            else:
                body, kind = page, "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    print(f"Review HUD: http://127.0.0.1:{a.port}/", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
