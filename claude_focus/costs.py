"""Scorer spend: one JSON line per scoring call in ~/.claude-focus/costs.jsonl.
The amounts are what Claude Code reports as the API-equivalent cost; on a
Claude subscription they count against the plan's usage limits."""
import collections
import datetime as dt
import json
import os
import time

from . import store

PATH = os.path.join(store.DIR, "costs.jsonl")


def record(name, model, cost):
    if cost is None:
        return
    with open(PATH, "a") as f:
        f.write(json.dumps({"t": int(time.time()), "name": name, "model": model,
                            "usd": round(cost, 6)}) + "\n")


def report(days=14):
    by_day = collections.defaultdict(lambda: [0, 0.0])
    try:
        with open(PATH) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                d = dt.date.fromtimestamp(e["t"]).isoformat()
                by_day[d][0] += 1
                by_day[d][1] += e.get("usd") or 0
    except OSError:
        pass
    if not by_day:
        return "No scoring calls recorded yet."
    keys = sorted(by_day)[-days:]
    lines = [f"{d}  {by_day[d][0]:>4} calls  ${by_day[d][1]:7.2f}" for d in keys]
    calls = sum(by_day[d][0] for d in keys)
    usd = sum(by_day[d][1] for d in keys)
    per_day = usd / len(keys)
    lines.append(f"average: {calls / len(keys):.0f} calls/day, ${per_day:.2f}/day, "
                 f"about ${per_day * 30:.0f} per 30 days (${usd / max(calls, 1):.4f} per call)")
    return "\n".join(lines)
