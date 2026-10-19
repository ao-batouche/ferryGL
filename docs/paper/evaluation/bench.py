"""Time ferryGL's planner on synthetic routes of growing size, for the white paper.

Each route has issues, the user's pull requests (most referring to an issue) and other
people's pull requests. Most items already have a Linear issue, some of whose recorded
GitHub states are out of date; the rest are new, so the planner also matches them against
the user's Linear issues in the team. The planner is pure, so it is timed alone.

    uv run python docs/paper/evaluation/bench.py
"""

from __future__ import annotations

import argparse
import csv
import platform
import random
import statistics
import sys
import time
from pathlib import Path

from world import STATE_TYPES, TEAM_ID, VIEWER

from ferrygl.config import Config, Route
from ferrygl.model import Attachment, GitHubItem, ItemRef, Kind, LinearIssue, Role
from ferrygl.plan import PlanInput, github_state, plan

REPO = "acme/api"
SIZES = (10, 30, 100, 300, 1000, 3000, 10000)


def route_input(size: int, rng: random.Random, known: float = 0.8) -> PlanInput:
    issues, prs, involved = [], [], []
    for number in range(1, size + 1):
        url = f"https://github.com/{REPO}/issues/{number}"
        roll = rng.random()
        if roll < 0.4:
            issues.append(GitHubItem(Kind.ISSUE, REPO, number, f"Issue {number}", url, "OPEN"))
        elif roll < 0.7:
            refs = (ItemRef(REPO, rng.choice(issues).number),) if issues and roll < 0.6 else ()
            url = url.replace("issues", "pull")
            prs.append(GitHubItem(Kind.PR, REPO, number, "PR", url, "OPEN", references=refs))
        else:
            url = url.replace("issues", "pull")
            roles = frozenset({Role.REQUESTED})
            involved.append(GitHubItem(Kind.INVOLVED, REPO, number, "PR", url, "OPEN", roles=roles))
    items = [*issues, *prs, *involved]
    attachments: dict[str, list[Attachment]] = {}
    candidates = []
    for n, item in enumerate(items):
        if rng.random() >= known:
            continue
        linear = LinearIssue(
            f"id-{n}", f"ENG-{n}", item.title, "Todo", STATE_TYPES["Todo"], TEAM_ID, VIEWER
        )
        state = github_state(item).key if rng.random() < 0.9 else "issue:open"
        meta = {"ferrygl_v": 1, "ferrygl_kind": item.kind.value, "ferrygl_state": state}
        attachments[item.url] = [Attachment(f"att-{n}", item.url, "#", REPO, meta, linear)]
        candidates.append(linear)
    if len(attachments) == len(items):
        candidates = []  # ferryGL loads candidates only when some item has no Linear issue
    config = Config(routes=(Route((REPO,), "ENG"),))
    return PlanInput(config.routes[0], config, VIEWER, items, attachments, candidates, "now")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "data")
    args = parser.parse_args(argv)
    rows = []
    for size in SIZES:
        row: dict[str, float] = {"items": size}
        for name, known in (("known", 1.0), ("new", 0.8)):
            inp = route_input(size, random.Random(size), known)
            timings = []
            for _ in range(args.repeats):
                start = time.perf_counter()
                changes = plan(inp)
                timings.append(time.perf_counter() - start)
            row[f"candidates_{name}"] = len(inp.candidates)
            row[f"changes_{name}"] = len(changes)
            row[f"median_ms_{name}"] = round(statistics.median(timings) * 1000, 3)
        rows.append(row)
        print(row)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "planner-time.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(platform.python_implementation(), platform.python_version(), platform.processor())
    return 0


if __name__ == "__main__":
    sys.exit(main())
