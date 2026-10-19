"""Estimate what an AI agent would spend to do ferryGL's job, for the white paper.

No model is called. The script runs ferryGL against the in-memory GitHub and Linear of
world.py, through stand-ins that record every call: what an agent with equivalent tools
would have to read (each tool result) and send (each tool call's arguments). A cost model
then turns those sizes into tokens and dollars for an agent loop with prompt caching, at
the published prices of three Claude models, under two sets of assumptions: a lean agent,
for which every assumption favors the agent, and a plausible one.

A third configuration prices an agent that delegates the job to ferryGL instead: it runs
``ferrygl sync``, reads the plan, runs ``ferrygl sync --apply`` and reports, so it reads only
what ferryGL prints.

It measures two workloads: the random histories of simulate.py, and single routes of
growing size in a steady state, where each run finds a twentieth of the items changed.

    uv run python docs/paper/evaluation/agent_cost.py
"""

from __future__ import annotations

import argparse
import csv
import functools
import io
import json
import math
import random
import statistics
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simulate import UserState, github_event, linear_event, random_config
from world import (
    ME,
    FakeGitHub,
    GitHubWorld,
    Issue,
    MemoryLinear,
    PullRequest,
)

import ferrygl.sync as sync_module
from ferrygl.config import Config, Route, SyncOptions
from ferrygl.model import Attachment, LinearIssue, Link

ROOT = Path(__file__).resolve().parents[3]
DATA = Path(__file__).resolve().parent.parent / "data"

# The agent's fixed context. The tool use system prompt is from Anthropic's pricing page;
# the instructions are ferryGL's own rules, which the agent needs to do the same job.
TOOL_SYSTEM_TOKENS = 286
BASH_TOOL_TOKENS = 325  # the bash tool's definition, on Claude 4.7 and later models
CALL_OVERHEAD_TOKENS = 20  # around each tool call and each tool result: name, ID, framing
USER_MESSAGE_TOKENS = 40  # "Sync my GitHub work into Linear now."
RUNS_PER_MONTH = {"twice a workday": 44, "hourly, 8 hours a workday": 176}


# Prices in dollars per million tokens, from https://platform.claude.com/docs/en/about-claude/pricing
# (accessed 2026-10-19): base input, 5-minute cache write, cache hit, output.
@dataclass(frozen=True)
class Price:
    model: str
    input: float
    write: float
    read: float
    output: float
    long_prompt: Price | None = None  # prices for long prompts, if different
    long_threshold: int = 100_000  # prompt tokens above which long_prompt applies
    min_cache: int = 0  # shortest prompt the provider caches, in tokens


PRICES = (
    Price("Claude Haiku 5.5", 0.10, 0.125, 0.01, 0.50, Price("", 0.50, 0.625, 0.05, 2.50)),
    Price("Claude Sonnet 5.5", 2.00, 2.50, 0.10, 10.00),
    Price("Claude Opus 5.5", 4.00, 5.00, 0.20, 20.00),
)


@dataclass(frozen=True)
class Agent:
    """Assumptions about how an agent works through one run."""

    name: str
    assumptions: str  # "lean" or "plausible": which tokenization to use when unknown
    body_chars: int  # average length of a GitHub issue or pull request description
    parallel: int  # tool calls per turn; 0 means every call a step allows, in one turn
    thinking: int  # reasoning tokens per turn, billed as output
    final: int = 150  # tokens of the closing summary
    delegates: bool = False  # runs ferryGL through a bash tool instead of doing the job


AGENTS = (
    Agent("lean", "lean", body_chars=0, parallel=0, thinking=100),
    Agent("plausible", "plausible", body_chars=500, parallel=5, thinking=500),
    # The same two sets of assumptions, for an agent that delegates the job to ferryGL.
    Agent("lean_delegating", "lean", body_chars=0, parallel=1, thinking=100, delegates=True),
    Agent("plausible_delegating", "plausible", 0, parallel=1, thinking=500, delegates=True),
)


@dataclass(frozen=True)
class Tokenization:
    """Characters per token for each kind of text an agent reads or writes."""

    name: str
    payload: float  # JSON tool results and calls
    text: float  # instructions, tool definitions and descriptions
    printed: float  # what ferryGL prints


TOKENIZER_TEXTS = {  # the parts of count_tokens.py's corpus, by kind of text
    "tool results and calls": "payload",
    "instructions and tools": "text",
    "delegation: instructions and ferryGL's output": "printed",
}


@functools.cache
def measured_tokenizations() -> dict[str, Tokenization]:
    """Ratios measured by count_tokens.py with public tokenizers, from data/tokenizers.csv."""
    ratios: dict[str, dict[str, float]] = {}
    with (DATA / "tokenizers.csv").open() as f:
        for row in csv.DictReader(f):
            kind = TOKENIZER_TEXTS[row["text"]]
            ratios.setdefault(row["tokenizer"], {})[kind] = float(row["characters_per_token"])
    return {name: Tokenization(name, **kinds) for name, kinds in ratios.items()}


@functools.cache
def assumed_tokenization(assumptions: str) -> Tokenization:
    """For a model whose tokenizer isn't public: the measured ratios most favorable to the
    agent (the most characters per token) for lean assumptions, the median for plausible."""
    # Models of one family share a tokenizer: count each distinct tokenizer once.
    measured = list(
        {(t.payload, t.text, t.printed): t for t in measured_tokenizations().values()}.values()
    )
    pick = max if assumptions == "lean" else statistics.median
    return Tokenization(
        f"{assumptions} (measured {'maximum' if assumptions == 'lean' else 'median'})",
        payload=pick(t.payload for t in measured),
        text=pick(t.text for t in measured),
        printed=pick(t.printed for t in measured),
    )


DELEGATE_INSTRUCTIONS = (
    "Keep the user's Linear workspace in step with their GitHub work by running ferryGL. "
    "Run `ferrygl sync` to preview the changes, then `ferrygl sync --apply` to make them, "
    "and report what changed. If a line says skip, tell the user what ferryGL needs. If the "
    "exit status is 2, report the error and stop. Never change GitHub or Linear yourself."
)
DELEGATE_COMMANDS = ("ferrygl sync", "ferrygl sync --apply")


# The tools an agent would need, kept as small as the job allows.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "github_search",
        "description": "Search GitHub issues and pull requests with GitHub's search syntax. "
        "Returns each result's number, title, URL, state, close reason, draft flag, "
        "description, repository, author, the issues a pull request closes and, if asked, "
        "the authors of its commits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Such as 'is:pr author:alice'"},
                "commit_authors": {"type": "boolean"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "linear_viewer",
        "description": "The Linear user who owns the API key.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "linear_team",
        "description": "A Linear team by key, with its workflow states.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "linear_links",
        "description": "For each URL, the Linear links (attachments) to it, with their "
        "metadata and the issue each is on: identifier, title, status, team, assignee.",
        "input_schema": {
            "type": "object",
            "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
            "required": ["urls"],
        },
    },
    {
        "name": "linear_my_issues",
        "description": "The issues in a team assigned to a user, open or closed, with their "
        "description and link URLs.",
        "input_schema": {
            "type": "object",
            "properties": {"team_id": {"type": "string"}, "assignee_id": {"type": "string"}},
            "required": ["team_id", "assignee_id"],
        },
    },
    {
        "name": "linear_create_issue",
        "description": "Create a Linear issue.",
        "input_schema": {
            "type": "object",
            "properties": {
                name: {"type": "string"}
                for name in ("team_id", "title", "description", "state_id", "assignee_id")
            },
            "required": ["team_id", "title", "state_id", "assignee_id"],
        },
    },
    {
        "name": "linear_set_status",
        "description": "Move a Linear issue to a workflow state.",
        "input_schema": {
            "type": "object",
            "properties": {"issue_id": {"type": "string"}, "state_id": {"type": "string"}},
            "required": ["issue_id", "state_id"],
        },
    },
    {
        "name": "linear_link",
        "description": "Add a link to a Linear issue, or update the link with that URL. "
        "Metadata values are strings or numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "url": {"type": "string"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["issue_id", "url", "title"],
        },
    },
    {
        "name": "linear_comment",
        "description": "Comment on a Linear issue.",
        "input_schema": {
            "type": "object",
            "properties": {"issue_id": {"type": "string"}, "body": {"type": "string"}},
            "required": ["issue_id", "body"],
        },
    },
]


def instructions() -> str:
    """The agent's system prompt: ferryGL's sync rules and its link metadata convention."""
    rules = (ROOT / "docs" / "sync-rules.md").read_text("utf-8")
    privacy = (ROOT / "docs" / "data-and-privacy.md").read_text("utf-8")
    metadata = privacy[privacy.index("### Link metadata") : privacy.index("## What it never")]
    task = (
        "You keep the user's Linear workspace in step with their GitHub work. Route: the "
        "repositories acme/api and acme/web go to the Linear team ENG. Follow these rules "
        "exactly, and record state on links as described.\n\n"
    )
    return task + rules + "\n\n" + metadata


# Recording what an agent would read and send


@dataclass(frozen=True)
class Call:
    step: int  # 0: setup and GitHub, 1: Linear lookups, 2: writes
    args: int  # characters of the call's arguments
    result: int  # characters of the result, without GitHub descriptions
    bodies: int = 0  # GitHub descriptions in the result
    requests: int = 1  # HTTP requests ferryGL makes for this call, with paging


@dataclass
class Shape:
    """Everything one run reads and sends."""

    calls: list[Call] = field(default_factory=list)
    items: int = 0
    changes: int = 0
    printed: int = 0  # characters ferryGL printed, which a delegating agent reads
    texts: list[str] | None = None  # the calls' text, when kept for counting tokens
    printed_text: str = ""  # what ferryGL printed, when kept

    def add(self, step: int, args: Any, result: Any, bodies: int = 0, requests: int = 1) -> None:
        self.calls.append(Call(step, _size(args), _size(result), bodies, requests))
        if self.texts is not None:
            self.texts += [_text(args), _text(result)]


def _text(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def _size(value: Any) -> int:
    return len(_text(value))


def uid(value: str | None) -> str | None:
    """A UUID-sized stand-in for an internal ID, as Linear's IDs are UUIDs."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value)) if value else None


class MeteredGitHub(FakeGitHub):
    def __init__(self, world: GitHubWorld, shape: Shape) -> None:
        super().__init__(world)
        self.shape = shape

    def viewer_login(self) -> str:
        self.shape.add(0, {}, {"login": self.login})
        return self.login

    def search(self, query: str, with_commits: bool = False) -> list[dict[str, Any]]:
        nodes = super().search(query, with_commits)
        bare = [{**node, "body": ""} for node in nodes]
        size = 25 if with_commits else 50
        pages = max(1, math.ceil(len(nodes) / size))
        args = {"query": query, "commit_authors": with_commits}
        self.shape.add(0, args, bare, len(nodes), requests=pages)
        return nodes


class MeteredLinear(MemoryLinear):
    shape: Shape

    def viewer(self) -> tuple[str, str]:
        result = super().viewer()
        self.shape.add(0, {}, {"id": uid(result[0]), "name": result[1]})
        return result

    def find_team(self, ref: str):
        team = super().find_team(ref)
        self.shape.add(0, {"key": ref}, {"id": uid(team.id), "key": team.key, "name": team.name})
        return team

    def team_states(self, team_id: str):
        states = super().team_states(team_id)
        result = [{"id": uid(s.id), "name": s.name, "type": s.type} for s in states.values()]
        self.shape.add(0, {"team_id": uid(team_id)}, result)
        return states

    def attachments_for_urls(self, urls):
        found = super().attachments_for_urls(urls)
        result = {url: [_attachment(a) for a in hits] for url, hits in found.items()}
        requests = max(1, math.ceil(len(found) / 25))  # ferryGL sends 25 URLs per request
        self.shape.add(1, {"urls": list(found)}, result, requests=requests)
        return found

    def assigned_issues(self, team_id: str, assignee_id: str) -> list[LinearIssue]:
        issues = super().assigned_issues(team_id, assignee_id)
        args = {"team_id": uid(team_id), "assignee_id": uid(assignee_id)}
        pages = max(1, math.ceil(len(issues) / 50))
        self.shape.add(1, args, [_issue(i, full=True) for i in issues], requests=pages)
        return issues

    def create_issue(self, **fields: Any) -> LinearIssue:
        issue = super().create_issue(**fields)
        args = {k: uid(v) if k.endswith("_id") else v for k, v in fields.items() if v}
        self.shape.add(2, args, {"id": uid(issue.id), "identifier": issue.identifier})
        return issue

    def create_attachment(self, issue_id: str, link: Link) -> None:
        super().create_attachment(issue_id, link)
        self.shape.add(2, {"issue_id": uid(issue_id), **_link(link)}, {"success": True})

    def update_attachment(self, link: Link) -> None:
        super().update_attachment(link)
        self.shape.add(2, {"link_id": uid(link.attachment_id), **_link(link)}, {"success": True})

    def update_issue_state(self, issue_id: str, state_id: str) -> None:
        super().update_issue_state(issue_id, state_id)
        self.shape.add(2, {"issue_id": uid(issue_id), "state_id": uid(state_id)}, {"success": True})

    def create_comment(self, issue_id: str, body: str) -> None:
        super().create_comment(issue_id, body)
        self.shape.add(2, {"issue_id": uid(issue_id), "body": body}, {"success": True})


def _issue(issue: LinearIssue, full: bool = False) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": uid(issue.id),
        "identifier": issue.identifier,
        "title": issue.title,
        "state": {"name": issue.state_name, "type": issue.state_type},
        "team": {"id": uid(issue.team_id)},
        "assignee": {"id": uid(issue.assignee_id)} if issue.assignee_id else None,
    }
    if full:
        node["description"] = issue.description
        node["attachments"] = [{"url": url} for url in issue.attachment_urls]
    return node


def _attachment(a: Attachment) -> dict[str, Any]:
    return {
        "id": uid(a.id),
        "url": a.url,
        "title": a.title,
        "subtitle": a.subtitle,
        "metadata": a.metadata,
        "issue": _issue(a.issue),
    }


def _link(link: Link) -> dict[str, Any]:
    return {"url": link.url, "title": link.title, "subtitle": link.subtitle, **link.metadata}


def measure(
    cfg: Config, lin: MeteredLinear, gh: MeteredGitHub, keep_text: bool = False
) -> tuple[Shape, int]:
    """Run ``sync --apply`` once and return what it read and sent, and its exit status."""
    shape = Shape(texts=[] if keep_text else None)
    lin.shape = gh.shape = shape
    recorded: list[Any] = []

    def plan(inp):
        changes = real_plan(inp)
        shape.items += len(inp.items)
        shape.changes += sum(c.action.value != "skip" for c in changes)
        recorded.extend(changes)
        return changes

    sync_module.plan = plan
    out, err = io.StringIO(), io.StringIO()
    try:
        status = sync_module.run_sync(
            cfg,
            apply=True,
            linear=lin,
            github_for=lambda user: gh,
            out=out,
            err=err,
            now="2026-10-19T09:00:00+00:00",
        )
    finally:
        sync_module.plan = real_plan
    shape.printed = len(out.getvalue()) + len(err.getvalue())
    if keep_text:
        shape.printed_text = out.getvalue() + err.getvalue()
    return shape, status


real_plan = sync_module.plan


# The cost model


@dataclass
class Usage:
    write: float = 0.0  # tokens written to the prompt cache
    read: float = 0.0  # tokens read from it
    uncached: float = 0.0  # input tokens of prompts too short to cache
    output: float = 0.0
    dollars: float = 0.0
    turns: int = 0
    peak_prompt: float = 0.0
    model_seconds: float = 0.0  # time spent waiting on the model
    api_seconds: float = 0.0  # time spent on GitHub and Linear requests

    @property
    def input(self) -> float:
        return self.write + self.read + self.uncached


def fixed_context(agent: Agent, tok: Tokenization) -> float:
    """Tokens every run starts with: the tool use system prompt, tools and instructions."""
    if agent.delegates:
        text = len(DELEGATE_INSTRUCTIONS) / tok.text
        return TOOL_SYSTEM_TOKENS + BASH_TOOL_TOKENS + text + USER_MESSAGE_TOKENS
    chars = len(json.dumps(TOOLS)) + len(instructions())
    return TOOL_SYSTEM_TOKENS + chars / tok.text + USER_MESSAGE_TOKENS


API_LATENCY = 0.5  # seconds per GitHub or Linear request: an assumption, the same for all
FERRYGL_STARTUP = 0.1  # seconds for ferryGL to start, measured with `ferrygl --version`


def ferrygl_seconds(shape: Shape, writes: bool = True) -> float:
    """ferryGL's own time for a run: its requests one after another, and its startup.
    Without ``writes``, the time of a preview, which reads but doesn't write."""
    requests = sum(c.requests for c in shape.calls if writes or c.step != 2)
    return FERRYGL_STARTUP + API_LATENCY * requests


def cost(
    shape: Shape,
    agent: Agent,
    price: Price,
    tok: Tokenization | None = None,
    speed: float | None = None,
    first_token: float | None = None,
) -> Usage:
    """Tokens and dollars for one run of an agent loop with prompt caching.

    Each step's calls depend on the previous step's results (GitHub results name the URLs
    to look up in Linear; both decide the writes), so steps take separate turns, and a step
    takes ceil(calls / parallel) turns. Runs are hours apart, so each run starts with a cold
    cache. Each turn reads the previous prompt from the cache and writes what is new: the
    previous turn's output and the tool results it asked for.

    Time, when the model's output speed and time to first answer token are given, adds each
    turn's time to first answer token, which includes the model's reasoning, and its visible
    output at that speed, to the time of the turn's requests, which run in parallel within a
    turn.
    """
    tok = tok or assumed_tokenization(agent.assumptions)
    turns: list[tuple[float, float]] = []  # (output tokens, result tokens) per turn
    usage = Usage()
    if agent.delegates:
        # The preview prints the same plan as the apply, so both read what the run printed.
        for command in DELEGATE_COMMANDS:
            out = agent.thinking + len(command) / tok.text + CALL_OVERHEAD_TOKENS
            turns.append((out, shape.printed / tok.printed + CALL_OVERHEAD_TOKENS))
            usage.api_seconds += ferrygl_seconds(shape, writes="--apply" in command)
    for step in range(3 if not agent.delegates else 0):
        calls = [c for c in shape.calls if c.step == step]
        size = agent.parallel or len(calls) or 1
        for start in range(0, len(calls), size):
            chunk = calls[start : start + size]
            out = agent.thinking + sum(c.args / tok.payload + CALL_OVERHEAD_TOKENS for c in chunk)
            results = sum(
                c.result / tok.payload + c.bodies * agent.body_chars / tok.text for c in chunk
            ) + CALL_OVERHEAD_TOKENS * len(chunk)
            turns.append((out, results))
            usage.api_seconds += API_LATENCY * max(c.requests for c in chunk)
    turns.append((agent.thinking + agent.final, 0.0))

    usage.turns = len(turns)
    prompt, new = 0.0, fixed_context(agent, tok)
    cached = 0.0  # tokens of the prompt that the provider has cached
    for out, results in turns:
        total = prompt + new
        long = price.long_prompt is not None and total > price.long_threshold
        rates = price.long_prompt if long else price
        if total >= price.min_cache:
            # The cached prefix is read; the rest is written to the cache, which costs the
            # plain input price where the provider charges nothing extra for writes.
            usage.read += cached
            usage.write += total - cached
            usage.dollars += (cached * rates.read + (total - cached) * rates.write) / 1e6
            cached = total
        else:
            usage.uncached += total
            usage.dollars += total * rates.input / 1e6
        usage.output += out
        usage.dollars += out * rates.output / 1e6
        usage.peak_prompt = max(usage.peak_prompt, total)
        if speed and first_token is not None:
            usage.model_seconds += first_token + (out - agent.thinking) / speed
        prompt, new = total, out + results
    return usage


# Workloads


def histories(scenarios: int, steps: int, seed: int) -> list[tuple[Shape, Shape]]:
    """For each step of each random history: the run that applies the step's changes, and
    the rerun right after it, which finds nothing new. Histories are generated like
    simulate.py's, without its injected failures, so they aren't the same histories."""
    pairs = []
    for n in range(scenarios):
        rng = random.Random(seed + n)
        repos = ("acme/api",) if rng.random() < 0.5 else ("acme/api", "acme/web")
        world = GitHubWorld(repos)
        lin = MeteredLinear()
        gh = MeteredGitHub(world, Shape())
        cfg = random_config(rng, repos)
        user = UserState()
        for _ in range(rng.randint(3, 10)):
            github_event(rng, world)
        for step in range(steps):
            # The same mix of events as simulate.py: three steps in ten change only Linear.
            linear_only = step > 0 and rng.random() < 0.3
            if step > 0 and not linear_only:
                for _ in range(rng.randint(1, 4)):
                    github_event(rng, world)
            if step > 0:
                for _ in range(rng.randint(1 if linear_only else 0, 2)):
                    linear_event(rng, world, lin, user, [])
            changed, _ = measure(cfg, lin, gh)
            quiet, _ = measure(cfg, lin, gh)
            pairs.append((changed, quiet))
    return pairs


def route(size: int, rng: random.Random) -> GitHubWorld:
    """A route with ``size`` items in scope: issues, the user's pull requests, most of
    them referring to an issue, and other people's pull requests awaiting the user's review."""
    w = GitHubWorld(("acme/api",))
    issues = []
    for n in range(size):
        roll = rng.random()
        number = w.number("acme/api")
        title = f"Improve the {rng.choice(('cache', 'parser', 'export', 'search'))} widget"
        if roll < 0.4 or not issues:
            issue = Issue("acme/api", number, title, rng.choice((ME, "bob")), {ME}, body="Details")
            w.issues.append(issue)
            issues.append(issue)
        elif roll < 0.7:
            body = f"Fixes #{rng.choice(issues).number}" if rng.random() < 0.67 else "Cleanup"
            w.prs.append(PullRequest("acme/api", number, title, ME, body=body, draft=n % 3 == 0))
        else:
            pr = PullRequest("acme/api", number, title, "bob", body="Please review")
            pr.requested.add(ME)
            w.prs.append(pr)
    return w


def evolve(world: GitHubWorld, share: float, rng: random.Random) -> None:
    """Move ``share`` of the items one step forward on GitHub."""
    items: list[Any] = [*world.issues, *world.prs]
    for item in rng.sample(items, max(1, round(share * len(items)))):
        if isinstance(item, Issue) and item.state == "OPEN":
            item.state, item.reason = "CLOSED", "COMPLETED"
        elif isinstance(item, PullRequest) and item.state == "OPEN":
            if item.author != ME and ME in item.requested:
                item.requested.discard(ME)
                item.reviewed_by.add(ME)
            elif item.draft:
                item.draft = False
            else:
                item.state = "MERGED"


def scaling(sizes: list[int], seed: int, keep_text: bool = False) -> dict[int, dict[str, Shape]]:
    """For each route size: the first run, a run that finds a twentieth of the items
    changed, and a run that finds nothing new. The commit scan is off, which lowers the
    agent's cost."""
    cfg = Config(routes=(Route(("acme/api",), "ENG"),), sync=SyncOptions(prs_with_commits=False))
    shapes = {}
    for size in sizes:
        rng = random.Random(seed + size)
        world = route(size, rng)
        lin = MeteredLinear()
        gh = MeteredGitHub(world, Shape())
        first, _ = measure(cfg, lin, gh, keep_text)
        evolve(world, 0.05, rng)
        changed, _ = measure(cfg, lin, gh, keep_text)
        quiet, _ = measure(cfg, lin, gh, keep_text)
        shapes[size] = {"first": first, "changed": changed, "quiet": quiet}
    return shapes


# Reporting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenarios", type=int, default=2000)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--out", type=Path, default=DATA)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    sizes = [10, 25, 50, 100, 200, 400, 800]
    routes = scaling(sizes, args.seed)
    rows = []
    for size, runs in routes.items():
        for kind, shape in runs.items():
            for agent in AGENTS:
                for price in PRICES:
                    u = cost(shape, agent, price)
                    rows.append(
                        {
                            "items": size,
                            "run": kind,
                            "changes": shape.changes,
                            "tool_calls": len(shape.calls),
                            "agent": agent.name,
                            "model": price.model,
                            "turns": u.turns,
                            "input_tokens": round(u.input),
                            "output_tokens": round(u.output),
                            "peak_prompt_tokens": round(u.peak_prompt),
                            "dollars": round(u.dollars, 5),
                        }
                    )
    with (args.out / "agent-scaling.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    wide_rows = []
    for size in sizes:
        row: dict[str, Any] = {"items": size}
        for r in rows:
            if r["items"] == size and r["run"] == "changed":
                key = f"{r['agent']}_{r['model'].split()[-2].lower()}"
                row[f"tokens_{r['agent']}"] = r["input_tokens"] + r["output_tokens"]
                row[f"month_{key}"] = round(r["dollars"] * RUNS_PER_MONTH["twice a workday"], 4)
        wide_rows.append(row)
    with (args.out / "agent-scaling-wide.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(wide_rows[0]))
        writer.writeheader()
        writer.writerows(wide_rows)

    pairs = histories(args.scenarios, args.steps, args.seed)
    summary: dict[str, Any] = {
        "scenarios": args.scenarios,
        "steps": args.steps,
        "runs": 2 * len(pairs),
        "fixed_context_tokens": {
            a.name: round(fixed_context(a, assumed_tokenization(a.assumptions))) for a in AGENTS
        },
        "tokenization": {kind: vars(assumed_tokenization(kind)) for kind in ("lean", "plausible")},
        "instructions_chars": len(instructions()),
        "tools_chars": len(json.dumps(TOOLS)),
        "agents": {},
    }
    for agent in AGENTS:
        for price in PRICES:
            changed = [cost(c, agent, price) for c, _ in pairs]
            quiet = [cost(q, agent, price) for _, q in pairs]
            every = changed + quiet
            summary["agents"][f"{agent.name} / {price.model}"] = {
                "tokens_per_run_median": statistics.median(u.input + u.output for u in every),
                "dollars_per_run_median": statistics.median(u.dollars for u in every),
                "dollars_total": sum(u.dollars for u in every),
                "quiet_share_of_dollars": sum(u.dollars for u in quiet)
                / sum(u.dollars for u in every),
                "turns_median": statistics.median(u.turns for u in every),
            }
    summary["items_per_run_median"] = statistics.median(c.items for c, _ in pairs)
    summary["tool_calls_per_run_median"] = statistics.median(len(c.calls) for c, _ in pairs)
    (args.out / "agent-cost.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.out / "agent-cost.tex").write_text(latex(summary, rows))

    for row in rows:
        if row["run"] == "changed" and row["items"] in (25, 100, 400):
            print(row)
    print(json.dumps(summary, indent=2)[:3000])
    return 0


def camel(name: str) -> str:
    """``lean_delegating`` as ``LeanDelegating``, for macro names."""
    return "".join(part.capitalize() for part in name.split("_"))


def latex(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """The results as LaTeX macros for the paper, such as ``\\MonthPlausibleOpusHundred``."""
    lines = ["% Generated by evaluation/agent_cost.py."]

    def define(name: str, value: str) -> None:
        lines.append(f"\\newcommand{{\\{name}}}{{{value}}}")

    define("AgentRuns", f"\\num{{{summary['runs']}}}")
    for kind, tok in summary["tokenization"].items():
        for part in ("payload", "text", "printed"):
            define(f"Tok{kind.capitalize()}{part.capitalize()}", f"{tok[part]:.2f}")
    define("AgentItemsMedian", f"\\num{{{summary['items_per_run_median']:g}}}")
    define("AgentCallsMedian", f"\\num{{{summary['tool_calls_per_run_median']:g}}}")
    for agent in AGENTS:
        define(
            f"AgentFixed{camel(agent.name)}",
            f"\\num{{{summary['fixed_context_tokens'][agent.name]}}}",
        )
    for key, stats in summary["agents"].items():
        agent, model = key.split(" / ")
        name = camel(agent) + model.split()[-2]
        define(f"Hist{name}Tokens", f"\\num{{{stats['tokens_per_run_median']:.0f}}}")
        define(f"Hist{name}Dollars", _dollars(stats["dollars_per_run_median"]))
        define(f"Hist{name}Quiet", f"{stats['quiet_share_of_dollars'] * 100:.0f}\\,\\%")
    ratios: dict[str, list[float]] = {}
    for size, word in ((10, "Ten"), (100, "Hundred"), (800, "EightHundred")):
        runs = {
            r["agent"]: r
            for r in rows
            if r["items"] == size and r["run"] == "changed" and "Opus" in r["model"]
        }
        for agent in ("lean", "plausible"):
            mine, delegated = runs[agent], runs[f"{agent}_delegating"]
            ratio = (mine["input_tokens"] + mine["output_tokens"]) / (
                delegated["input_tokens"] + delegated["output_tokens"]
            )
            define(f"Delegation{camel(agent)}Ratio{word}", f"{ratio:.0f}")
            ratios.setdefault(f"DelegationRatio{word}", []).append(ratio)
            saves = mine["dollars"] / delegated["dollars"]
            define(f"Delegation{camel(agent)}Saves{word}", f"{saves:.0f}")
            ratios.setdefault(f"DelegationSaves{word}", []).append(saves)
    for name, values in ratios.items():  # "27 to 38": the lower bound first, whichever it is
        define(f"{name}Low", f"{min(values):.0f}")
        define(f"{name}High", f"{max(values):.0f}")
    for row in rows:
        if row["run"] in ("first", "quiet") and row["items"] == 100:
            name = f"{camel(row['agent'])}{row['model'].split()[-2]}"
            define(f"{row['run'].capitalize()}Run{name}Hundred", _dollars(row["dollars"]))
            tokens = row["input_tokens"] + row["output_tokens"]
            define(f"{row['run'].capitalize()}Tok{name}Hundred", f"\\num{{{tokens}}}")
            continue
        if row["run"] != "changed" or row["items"] not in (25, 100, 400, 800):
            continue
        name = f"{camel(row['agent'])}{row['model'].split()[-2]}{_words(row['items'])}"
        define(f"Run{name}", _dollars(row["dollars"]))
        define(f"Tok{name}", f"\\num{{{row['input_tokens'] + row['output_tokens']}}}")
        for runs in RUNS_PER_MONTH.values():
            suffix = "Month" if runs == 44 else "Hourly"
            define(f"{suffix}{name}", _dollars(row["dollars"] * runs))
    return "\n".join(lines) + "\n"


def _dollars(value: float) -> str:
    if value >= 100:
        return f"\\${value:,.0f}"
    if value >= 1:
        return f"\\${value:,.2f}"
    digits = max(2, 1 - math.floor(math.log10(value))) if value > 0 else 2
    return f"\\${value:.{digits}f}"


def _words(number: int) -> str:
    return {25: "TwentyFive", 100: "Hundred", 400: "FourHundred", 800: "EightHundred"}[number]


if __name__ == "__main__":
    sys.exit(main())
