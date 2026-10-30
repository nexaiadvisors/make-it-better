---
name: make-it-better
description: "Improves an existing web project over repeated iterations, scores every version out of 100 with an independent judge, and opens a review page to compare them all. Use for 'make it better', 'improve this site', 'run a make it better loop'."
argument-hint: "<project path> [--iterations 5] [--auto-stop] [--target 80] [--pages / /pricing] [--bar <reference site>]"
---

# Make It Better

## Overview

Runs repeated improvement iterations on one existing web project. Each iteration makes one focused
change on a branch. An independent judge and Lighthouse then score the result out of 100. At the end,
a local review page shows every version, including the original, with what changed and why.

The default is 5 iterations, and the run uses all of them. With `--auto-stop`, the run can also stop
early at the target score, on diminishing returns, on a regression, or when an iteration finds nothing
worth changing.

A bare "make it better" prompt repeated in a loop tends to add things until the project breaks. So this
skill allows "no change", counts removal as progress, requires outside references, and never lets the
agent that made a change score it.

## Hard rules

- **Existing projects only.** The project needs a page that runs locally.
- **Work on a branch.** Never push, deploy or merge. Going live is the user's separate decision.
- **Score locally.** Do not run this loop against a live production site.
- **Never invent facts.** Prices, results, customers and testimonials come from the project or the user.
- **The maker never scores itself.** Only `scripts/score.py` scores a version.

## Workflow

### 1. Fix the inputs

Collect these, reading the project first and asking only for what it cannot answer:

| Input | Default |
|---|---|
| Project path | required |
| Local URL and the command that starts it | from the project's scripts. For a plain static site, `python3 -m http.server <port>` |
| Pages to score | `/`, up to 5 pages |
| Iterations | 5, and any higher number the user sets |
| Auto-stop | off |
| Target score | 80, used only with auto-stop |
| Purpose | one sentence: who it is for and what it should get them to do |
| Reference site | none |
| Facts the maker may use | what is already in the project |
| What the maker must not touch | payment, auth and form handling, plus anything the user names |

Accept when: every input has a value, and `git status` in the project shows no uncommitted changes.

### 2. Score the baseline

Below, `<skill-dir>` is the folder holding this file. Create the run folder at the absolute path of
`~/.make-it-better/<project-folder>-<YYYY-MM-DD>/`, adding `-2` if it exists, and call it `$RUN`. Create
branch `make-it-better/<YYYY-MM-DD>` in the project. Write `$RUN/config.json` from
`references/config-example.json`, using absolute paths. `judge` is `auto`, `codex` or `claude`.
`judge_model` is optional, and `judges` is how many judge runs to take the median of. Then run:

```bash
python3 <skill-dir>/scripts/score.py --config "$RUN/config.json" --iteration 0
```

The script starts the project with `start_cmd` if needed, and stops it afterward. It needs `git`,
`npx` with network access for Lighthouse, Chrome, `agent-browser`, and `codex` or `claude` for the judge.

Open `$RUN/pass-0/` and look at `<page>-1280.png`, `<page>-375.png` and `judges.json`. With 2 or more
judges, a `judge_spread` over 12 points means the rubric is not holding on this project, so tell the
user before spending iterations.

Accept when: `ledger.jsonl` has one row, and the total looks believable against the screenshots.

### 3. Confirm the run

Show the baseline total, the weakest area, the iteration count, whether auto-stop is on, and the cost:
each iteration is one maker run plus `judges` judge runs. Wait for a yes, unless the user already told
you to run it without asking.

Accept when: the user approved this project and iteration count.

### 4. Run the iterations

For each iteration `i` from 1 to the iteration count:

1. Start a fresh subagent with `references/maker-brief.md`, filling in its values. Without subagents,
   do the iteration yourself, and still never score your own work.
2. When it finishes, run the project's own build or test command, if it has one.
3. Run `score.py --config "$RUN/config.json" --iteration <i>`.
4. Read the last output line. `CONTINUE` means go to the next iteration. `STOP <reason>` ends the loop.

A version with `capture_warnings` had files fail to load. If the project itself has that broken file,
the score is fair. Otherwise the judges saw a partly loaded page, so treat that score as unreliable.
Never change the target or settings during a run.

Accept when: `$RUN/decision.json` holds a stop reason other than `continue`.

### 5. Open the review page

Start this in the background, and give the user the link:

```bash
python3 <skill-dir>/scripts/review_hud.py --config "$RUN/config.json" --port 4400
```

It shows every version in a frame, with a page picker, desktop and phone views, and what changed and
why. Each version needs `serve_dir` in the config, a folder that each commit tracks. Use `.` for a
plain static site. If the build folder is not committed, ask the maker to commit it on this branch.
Forms do not run on this page, so nothing can be sent from it. Each version uses its own port, counting
up from the page's port plus 1.

Accept when: the original and the best version both load. Without a browser, confirm each version's port
returns 200, and say that the widths were not viewed.

### 6. Report

Create branch `make-it-better/<YYYY-MM-DD>-best` at `best_sha` from `decision.json`. Its screenshots are in
`$RUN/pass-<best_pass>/`. Do not reset the
working branch. Then report the start and best totals, the change in each area, the stop reason, what
changed, and any fact the user needs to supply. Say that a score is a guide, and that close versions
are within judge noise, so the user should choose by looking.

Accept when: the best commit is named, and you have looked at its screenshots.

## Stop reasons

| Reason | Meaning |
|---|---|
| `iterations_reached` | Every iteration ran. This is the normal end. |
| `target_met` | Auto-stop: the target was reached with no area under half marks. |
| `diminishing_returns` | Auto-stop: the last 2 versions gained under 2 points. |
| `regressed` | Auto-stop: the last 2 versions fell 5 or more below the best. |
| `no_change` | Auto-stop: the maker found nothing that would clearly help. |

## Tuning

Judge scores vary, so `min_gain` below about 2 chases noise. Three judges are the default for that
reason. `references/judge-rubric.md` is plain text: edit it to match what you care about.
