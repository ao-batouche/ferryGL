"""Compare ferryGL with AI agents on many models: tokens, time and dollars, for the white paper.

One user's workload is measured once, by running ferryGL over a route of 100 items in a steady
state, where each run finds a twentieth of the items changed (agent_cost.py). The same
workload is then priced and timed for every model in models.py, for three strategies:

- ferryGL alone, run on a schedule: no model at all;
- an agent doing the job itself, with tools that return what ferryGL's queries return;
- an agent delegating the job to ferryGL: it runs ``ferrygl sync``, then ``--apply``.

Each agent strategy is priced under the lean and the plausible assumptions of agent_cost.py,
with the model's own tokenizer when it is public (data/tokenizers.csv). Results are per run,
per user and per team of 10, 50 and 100 users, per month and per year, for two schedules.

    uv run python docs/paper/evaluation/compare_models.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_cost import (
    AGENTS,
    DATA,
    Agent,
    Tokenization,
    assumed_tokenization,
    cost,
    ferrygl_seconds,
    measured_tokenizations,
    scaling,
)
from models import ACTIONS_FREE_MINUTES, ACTIONS_MINUTE_PRICE, MODELS, Model

ROUTE_ITEMS = 100
SCHEDULES = {  # runs per month: 22 workdays
    "twice a workday": 2 * 22,
    "hourly, 8 hours a workday": 8 * 22,
}
TEAMS = (1, 10, 50, 100)


@dataclass(frozen=True)
class Result:
    model: Model
    strategy: str  # "itself" or "delegating"
    assumptions: str  # "lean" or "plausible"
    tokenization: str
    tokens: float  # input and output tokens per run
    dollars: float  # per run
    seconds: float | None  # per run, model and requests; None without a speed measurement


def tokenization_for(model: Model, agent: Agent) -> Tokenization:
    """The model's own tokenizer when it is public; else the agent's assumption."""
    measured = measured_tokenizations()
    if model.tokenizer and model.tokenizer in measured:
        return measured[model.tokenizer]
    return assumed_tokenization(agent.assumptions)


def evaluate(shape) -> list[Result]:
    results = []
    for model in MODELS:
        for agent in AGENTS:
            tok = tokenization_for(model, agent)
            u = cost(shape, agent, model.price, tok, model.speed, model.first_token)
            results.append(
                Result(
                    model=model,
                    strategy="delegating" if agent.delegates else "itself",
                    assumptions=agent.assumptions,
                    tokenization=tok.name,
                    tokens=u.input + u.output,
                    dollars=u.dollars,
                    seconds=u.model_seconds + u.api_seconds if model.speed else None,
                )
            )
    return results


def rows(results: list[Result], ferrygl_run_seconds: float) -> list[dict[str, Any]]:
    """One row per model, strategy and assumptions, with per-run, monthly and yearly figures."""
    out = []
    for r in results:
        row: dict[str, Any] = {
            "provider": r.model.provider,
            "model": r.model.name,
            "model_id": r.model.model_id,
            "strategy": r.strategy,
            "assumptions": r.assumptions,
            "tokenization": r.tokenization,
            "tokens_per_run": round(r.tokens),
            "dollars_per_run": round(r.dollars, 6),
            "seconds_per_run": round(r.seconds, 1) if r.seconds is not None else "",
        }
        for runs in SCHEDULES.values():
            key = "2x" if runs == 44 else "hourly"
            for users in TEAMS:
                row[f"dollars_month_{key}_{users}u"] = round(r.dollars * runs * users, 2)
                row[f"dollars_year_{key}_{users}u"] = round(r.dollars * runs * 12 * users, 2)
            row[f"tokens_year_{key}_100u"] = round(r.tokens * runs * 12 * 100)
            hours = r.seconds * runs * 12 / 3600 if r.seconds is not None else None
            row[f"hours_year_{key}_1u"] = round(hours, 2) if hours is not None else ""
        out.append(row)
    # ferryGL alone spends no tokens. On a user's machine it costs nothing; on GitHub Actions,
    # each run is a job billed as one minute, free within the plan's free minutes.
    for where, per_run in (
        ("own machine", 0.0),
        ("GitHub Actions, paid minutes", ACTIONS_MINUTE_PRICE),
    ):
        alone: dict[str, Any] = {
            "provider": "",
            "model": f"ferryGL alone ({where})",
            "model_id": "",
            "strategy": "alone",
            "assumptions": "",
            "tokenization": "",
            "tokens_per_run": 0,
            "dollars_per_run": per_run,
            "seconds_per_run": round(ferrygl_run_seconds, 1),
        }
        for runs in SCHEDULES.values():
            key = "2x" if runs == 44 else "hourly"
            for users in TEAMS:
                alone[f"dollars_month_{key}_{users}u"] = round(per_run * runs * users, 2)
                alone[f"dollars_year_{key}_{users}u"] = round(per_run * runs * 12 * users, 2)
            alone[f"tokens_year_{key}_100u"] = 0
            alone[f"hours_year_{key}_1u"] = round(ferrygl_run_seconds * runs * 12 / 3600, 2)
        out.append(alone)
    return out


NOTES = {  # footnote marks in the paper's tables, by model note
    "price from 2026-12-01; half until then": "a",
    "price from 2027-01-01; half until then": "a",
    "peak price; off-peak hours cost half": "b",
    "public preview; regular price": "c",
    "released 2026-10-19": "d",
}


def _money(value: float) -> str:
    # Whole dollars from $10 up: cents would be false precision in an estimate.
    if value >= 10:
        return f"\\${value:,.0f}"
    if value >= 1:
        return f"\\${value:,.2f}"
    if value >= 0.1:
        return f"\\${value:.2f}"
    if value >= 0.01:
        return f"\\${value:.3f}"
    return f"\\${value:.4f}"


def _name(model: Model) -> str:
    mark = NOTES.get(model.note)
    return model.name + (f"\\textsuperscript{{{mark}}}" if mark else "")


def latex(results: list[Result], ferrygl_run: float, shape) -> dict[str, str]:
    """The paper's table bodies and macros, written from the results."""
    by = {(r.model.name, r.strategy, r.assumptions): r for r in results}
    runs = SCHEDULES["twice a workday"]
    team_year = runs * 12 * 100  # runs a year for a team of 100, the paper's headline case
    run_rows = []
    provider = None
    for model in MODELS:
        if provider is not None and model.provider != provider:
            run_rows.append("\\midrule")
        first = model.provider != provider
        provider = model.provider
        label = (model.provider if first else "") + " & " + _name(model)
        mine = by[(model.name, "itself", "plausible")]
        dele = by[(model.name, "delegating", "plausible")]
        lean = by[(model.name, "itself", "lean")]
        dele_lean = by[(model.name, "delegating", "lean")]

        def secs(r: Result) -> str:
            return f"{r.seconds:.0f}" if r.seconds is not None else "---"

        def span(low: Result, high: Result) -> str:
            return f"{_money(low.dollars * team_year)}--{_money(high.dollars * team_year)}"

        # One row per model: the exact values behind the paper's figure of yearly team costs.
        run_rows.append(
            f"{label} & \\num{{{mine.tokens:.0f}}} & {span(lean, mine)} & {span(dele_lean, dele)}"
            f" & {secs(mine)} & {secs(dele)} \\\\"
        )
    actions_team = _money(ACTIONS_MINUTE_PRICE * team_year)
    run_rows += [
        "\\midrule",
        # Note e: nothing on users' own machines, the upper figure on paid Actions minutes.
        f"\\multicolumn{{2}}{{@{{}}l}}{{\\ferry{{}} alone}} & 0 & "
        f"\\$0--{actions_team}\\textsuperscript{{e}} & & {ferrygl_run:.0f} & \\\\",
    ]

    def yearly(strategy: str, assumptions: str) -> list[float]:
        return [by[(m.name, strategy, assumptions)].dollars * runs * 12 * 100 for m in MODELS]

    timed = [r for r in results if r.seconds is not None and r.assumptions == "plausible"]
    mine_times = [r.seconds for r in timed if r.strategy == "itself"]
    dele_times = [r.seconds for r in timed if r.strategy == "delegating"]

    def saves(assumptions: str) -> list[float]:
        return [
            by[(m.name, "itself", assumptions)].dollars
            / by[(m.name, "delegating", assumptions)].dollars
            for m in MODELS
        ]

    ratios = saves("lean") + saves("plausible")
    providers: dict[str, list[float]] = {}
    for m in MODELS:
        providers.setdefault(m.provider, []).append(by[(m.name, "itself", "plausible")].dollars)
    within = [max(costs) / min(costs) for costs in providers.values()]
    actions_year = ACTIONS_MINUTE_PRICE * runs * 12 * 100
    itself_year = yearly("itself", "plausible")
    lean_share = [
        lean / plausible
        for lean, plausible in zip(yearly("itself", "lean"), itself_year, strict=True)
    ]
    tokens_year_lean = [by[(m.name, "itself", "lean")].tokens * runs * 12 * 100 for m in MODELS]
    tokens_year = [by[(m.name, "itself", "plausible")].tokens * runs * 12 * 100 for m in MODELS]
    macros = {
        "CmpModels": str(len(MODELS)),
        "CmpProviders": str(len({m.provider for m in MODELS})),
        "CmpItselfHundredMin": _money(min(yearly("itself", "plausible"))),
        "CmpItselfHundredMax": _money(max(yearly("itself", "plausible"))),
        "CmpItselfLeanHundredMin": _money(min(yearly("itself", "lean"))),
        "CmpItselfLeanHundredMax": _money(max(yearly("itself", "lean"))),
        "CmpDelegatingHundredMin": _money(min(yearly("delegating", "plausible"))),
        "CmpDelegatingHundredMax": _money(max(yearly("delegating", "plausible"))),
        "CmpDelegatingLeanHundredMin": _money(min(yearly("delegating", "lean"))),
        "CmpDelegatingLeanHundredMax": _money(max(yearly("delegating", "lean"))),
        "CmpSavesMin": f"{min(ratios):.0f}",  # over both sets of assumptions
        "CmpSavesMax": f"{max(ratios):.0f}",
        "CmpSavesPlausibleMin": f"{min(saves('plausible')):.0f}",
        "CmpSavesPlausibleMax": f"{max(saves('plausible')):.0f}",
        "CmpTokensYearMin": f"{min(tokens_year) / 1e9:.1f}",
        "CmpTokensYearMax": f"{max(tokens_year) / 1e9:.1f}",
        "CmpItselfSecondsMin": f"{min(mine_times):.0f}",
        "CmpItselfSecondsMax": f"{max(mine_times):.0f}",
        "CmpDelegatingSecondsMin": f"{min(dele_times):.0f}",
        "CmpDelegatingSecondsMax": f"{max(dele_times):.0f}",
        "CmpFerryglSeconds": f"{ferrygl_run:.0f}",
        "CmpActionsHundredYear": _money(ACTIONS_MINUTE_PRICE * runs * 12 * 100),
        "CmpActionsMinutesMonth": str(runs),
        "CmpActionsFreeMinutes": f"\\num{{{ACTIONS_FREE_MINUTES}}}",
        "CmpUntimed": str(sum(1 for m in MODELS if m.speed is None)),
        "CmpSpread": f"{max(itself_year) / min(itself_year):.0f}",
        "CmpWithinMin": f"{min(within):.1f}",
        "CmpWithinMax": f"{max(within):.0f}",
        "CmpBelowActions": str(
            sum(1 for d in yearly("delegating", "plausible") if d < actions_year)
        ),
        "CmpItselfAboveActions": str(all(i > actions_year for i in yearly("itself", "lean"))),
        "CmpActionsHundredYearValue": f"{actions_year:.1f}",
        "CmpChanges": str(shape.changes),
        "CmpRequests": str(sum(c.requests for c in shape.calls)),
        "CmpLeanShareLow": f"{min(lean_share) * 100:.0f}",
        "CmpLeanShareHigh": f"{max(lean_share) * 100:.0f}",
        "CmpTokensYearLeanMin": f"{min(tokens_year_lean) / 1e9:.1f}",
        "CmpTokensYearLeanMax": f"{max(tokens_year_lean) / 1e9:.1f}",
    }
    groups: dict[tuple[float, float, float], list[str]] = {}
    for t in measured_tokenizations().values():
        groups.setdefault((t.payload, t.text, t.printed), []).append(t.name)
    token_rows = []
    for (payload, text, printed), names in sorted(groups.items(), key=lambda g: g[0][0]):
        label = ", ".join(names).replace("_", r"\_")
        token_rows.append(f"{label} & {payload:.2f} & {text:.2f} & {printed:.2f} \\\\")
    return {
        # Table rows as macros: \\input inside a tabular breaks the rule that follows it.
        "models-rows.tex": "".join(
            f"\\newcommand{{\\{name}}}{{%\n" + "\n".join(rows) + "\n}\n"
            for name, rows in (
                ("CmpTokenizerRows", token_rows),
                ("CmpRunRows", run_rows),
            )
        ),
        "models-macros.tex": "".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in macros.items()),
    }


def figure_rows(results: list[Result]) -> list[dict[str, Any]]:
    """Yearly cost for 100 users, twice a workday, by model, cheapest first."""
    by = {(r.model.name, r.strategy, r.assumptions): r for r in results}
    runs = SCHEDULES["twice a workday"] * 12 * 100
    out = [
        {
            "label": m.name,
            "itself": round(by[(m.name, "itself", "plausible")].dollars * runs, 2),
            "itself_lean": round(by[(m.name, "itself", "lean")].dollars * runs, 2),
            "delegating": round(by[(m.name, "delegating", "plausible")].dollars * runs, 2),
            "delegating_lean": round(by[(m.name, "delegating", "lean")].dollars * runs, 2),
        }
        for m in MODELS
    ]
    return sorted(out, key=lambda row: row["itself"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DATA)
    args = parser.parse_args(argv)

    shape = scaling([ROUTE_ITEMS], seed=20261019)[ROUTE_ITEMS]["changed"]
    results = evaluate(shape)
    table = rows(results, ferrygl_seconds(shape))
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "models.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    summary = {
        "route_items": ROUTE_ITEMS,
        "changes_per_run": shape.changes,
        "requests_per_run": sum(c.requests for c in shape.calls),
        "ferrygl_seconds_per_run": round(ferrygl_seconds(shape), 1),
        "actions_minute_price": ACTIONS_MINUTE_PRICE,
        "actions_free_minutes": ACTIONS_FREE_MINUTES,
        "models": len(MODELS),
        "providers": len({m.provider for m in MODELS}),
    }
    (args.out / "models.json").write_text(json.dumps(summary, indent=2) + "\n")
    for name, text in latex(results, ferrygl_seconds(shape), shape).items():
        (args.out / name).write_text(text)
    figure = figure_rows(results)
    with (args.out / "models-figure.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(figure[0]))
        writer.writeheader()
        writer.writerows(figure)
    for row in table:
        seconds = row["seconds_per_run"]
        print(
            f"{row['model']:<42} {row['strategy']:<10} {row['assumptions']:<9} "
            f"{row['tokens_per_run']:>9,} tok  ${row['dollars_per_run']:>8.4f}/run "
            f"{seconds if seconds != '' else '-':>6} s  "
            f"${row['dollars_year_2x_100u']:>10,.0f}/yr 100 users"
        )
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
