#!/usr/bin/env python3
"""Deterministic state/budget bookkeeping for the research agent pipeline.

This script does NOT do any research, translation, or LLM work itself -- it
only tracks state-machine transitions and API-spend budget so that a fresh
Claude session (e.g. one woken up by a cron schedule with no memory of prior
sessions) can reload exactly where things stood and cannot silently skip
states or blow through budget. All the actual "thinking" (searching papers,
generating hypotheses, analyzing results, writing reports) is done by the
Claude session itself using its normal tools, and stored as plain
JSON/Markdown files under research_agent/.

Usage:
    python3 research_agent/orchestrator.py status
    python3 research_agent/orchestrator.py advance <NEW_STATE> --note "..."
    python3 research_agent/orchestrator.py check-budget <amount_usd>
    python3 research_agent/orchestrator.py log-cost <amount_usd> --note "..."
    python3 research_agent/orchestrator.py approve              # list waiting
    python3 research_agent/orchestrator.py approve <id> [<id>]  # human sign-off
    python3 research_agent/orchestrator.py approve --all
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(__file__).parent / "state"
PIPELINE_STATE_PATH = STATE_DIR / "pipeline_state.json"
BUDGET_PATH = STATE_DIR / "budget.json"
HYPOTHESES_PATH = STATE_DIR / "hypotheses.json"
PENDING_APPROVAL_PATH = STATE_DIR / "pending_approval.json"

# The state machine graph. Keys are states; values are the states legally
# reachable from them. WAITING_APPROVAL is a holding pattern entered from
# HUMAN_APPROVAL when auto-approval thresholds are exceeded.
TRANSITIONS: dict[str, list[str]] = {
    "SEARCH_PAPERS": ["EXTRACT_PAPERS"],
    "EXTRACT_PAPERS": ["READ_PAPERS"],
    "READ_PAPERS": ["GENERATE_HYPOTHESES"],
    "GENERATE_HYPOTHESES": ["HUMAN_APPROVAL"],
    "HUMAN_APPROVAL": ["RUN_EXPERIMENTS", "WAITING_APPROVAL"],
    "WAITING_APPROVAL": ["RUN_EXPERIMENTS", "WAITING_APPROVAL"],
    "RUN_EXPERIMENTS": ["ANALYZE_RESULTS"],
    "ANALYZE_RESULTS": ["WRITE_REPORT"],
    "WRITE_REPORT": ["REFLECT"],
    "REFLECT": ["SEARCH_PAPERS", "GENERATE_HYPOTHESES"],
}

ALL_STATES = set(TRANSITIONS.keys())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"error: {path} does not exist (pipeline not initialized?)")
    return json.loads(path.read_text())


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def cmd_status(_args: argparse.Namespace) -> None:
    state = _load(PIPELINE_STATE_PATH)
    budget = _load(BUDGET_PATH)
    _roll_budget_if_new_day(budget)
    print(json.dumps({"pipeline": state, "budget": budget}, indent=2, ensure_ascii=False))


def cmd_advance(args: argparse.Namespace) -> None:
    state = _load(PIPELINE_STATE_PATH)
    current = state["current_state"]
    target = args.new_state
    if target not in ALL_STATES:
        raise SystemExit(f"error: unknown state {target!r}. Valid states: {sorted(ALL_STATES)}")
    if target not in TRANSITIONS.get(current, []):
        raise SystemExit(
            f"error: illegal transition {current} -> {target}. "
            f"Allowed from {current}: {TRANSITIONS.get(current, [])}"
        )
    state.setdefault("history", []).append(
        {"from": current, "to": target, "at": _now(), "note": args.note or ""}
    )
    state["current_state"] = target
    state["last_updated"] = _now()
    if target == "SEARCH_PAPERS" and current == "REFLECT":
        state["cycle"] = state.get("cycle", 1) + 1
    _save(PIPELINE_STATE_PATH, state)
    print(f"advanced: {current} -> {target}")


def _roll_budget_if_new_day(budget: dict) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if budget.get("date") != today:
        budget["date"] = today
        budget["spent_today_usd"] = 0.0


def cmd_check_budget(args: argparse.Namespace) -> None:
    budget = _load(BUDGET_PATH)
    _roll_budget_if_new_day(budget)
    amount = args.amount_usd
    remaining_daily = budget["daily_cap_usd"] - budget["spent_today_usd"]
    fits_daily = amount <= remaining_daily
    fits_batch = amount <= budget["per_batch_cap_usd"]
    decision = "AUTO_APPROVE" if (fits_daily and fits_batch) else "ESCALATE_TO_HUMAN"
    result = {
        "decision": decision,
        "requested_usd": amount,
        "remaining_daily_usd": round(remaining_daily, 4),
        "per_batch_cap_usd": budget["per_batch_cap_usd"],
        "fits_daily_cap": fits_daily,
        "fits_batch_cap": fits_batch,
    }
    _save(BUDGET_PATH, budget)  # persist any day-roll
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_log_cost(args: argparse.Namespace) -> None:
    budget = _load(BUDGET_PATH)
    _roll_budget_if_new_day(budget)
    budget["spent_today_usd"] = round(budget["spent_today_usd"] + args.amount_usd, 4)
    budget["total_spent_usd"] = round(budget.get("total_spent_usd", 0.0) + args.amount_usd, 4)
    budget.setdefault("cost_log", []).append(
        {"at": _now(), "amount_usd": args.amount_usd, "note": args.note or ""}
    )
    _save(BUDGET_PATH, budget)
    print(f"logged ${args.amount_usd:.4f}; spent_today=${budget['spent_today_usd']:.4f} "
          f"of ${budget['daily_cap_usd']:.2f} daily cap")


def _waiting_hypotheses(items: list[dict]) -> list[dict]:
    return [
        h
        for h in items
        if h.get("approval") in (None, "proposed", "needs_human")
        and h.get("status") not in ("done", "rejected", "tested")
    ]


def cmd_approve(args: argparse.Namespace) -> None:
    """Human sign-off. Run by a person; agents must not call this themselves.

    With no ids, only lists what is waiting. With ids or --all, marks them
    ``approved_by_human`` / ``queued`` (the outcome PLAYBOOK.md's
    WAITING_APPROVAL section expects) and clears pending_approval.json.
    """
    data = _load(HYPOTHESES_PATH)
    items = data["hypotheses"]
    waiting = _waiting_hypotheses(items)
    if not args.ids and not args.all:
        if not waiting:
            print("nothing is waiting for approval")
        for h in waiting:
            print(
                f"{h['id']}  [{h.get('approval')}]  ${h.get('estimated_cost_usd', '?')}"
                f"  existing_clips={h.get('uses_existing_clips')}\n    {h.get('title', '')}"
            )
        return
    chosen = waiting if args.all else [h for h in waiting if h["id"] in args.ids]
    unknown = set(args.ids) - {h["id"] for h in chosen}
    if unknown:
        raise SystemExit(f"error: not waiting for approval: {', '.join(sorted(unknown))}")
    for h in chosen:
        h["approval"] = "approved_by_human"
        h["status"] = "queued"
        h["approved_at"] = _now()
    _save(HYPOTHESES_PATH, data)
    if PENDING_APPROVAL_PATH.exists():
        done = {h["id"] for h in chosen}
        pending = json.loads(PENDING_APPROVAL_PATH.read_text())
        pending = [e for e in pending if e.get("hypothesis_id") not in done]
        PENDING_APPROVAL_PATH.write_text(json.dumps(pending, indent=2, ensure_ascii=False) + "\n")
    print(f"approved {len(chosen)}: {', '.join(h['id'] for h in chosen)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Print current pipeline state and budget").set_defaults(func=cmd_status)

    p_advance = sub.add_parser("advance", help="Transition to a new pipeline state")
    p_advance.add_argument("new_state")
    p_advance.add_argument("--note", default="")
    p_advance.set_defaults(func=cmd_advance)

    p_check = sub.add_parser("check-budget", help="Check whether a spend is auto-approvable")
    p_check.add_argument("amount_usd", type=float)
    p_check.set_defaults(func=cmd_check_budget)

    p_log = sub.add_parser("log-cost", help="Record actual spend against the budget")
    p_log.add_argument("amount_usd", type=float)
    p_log.add_argument("--note", default="")
    p_log.set_defaults(func=cmd_log_cost)

    p_appr = sub.add_parser("approve", help="Human sign-off for waiting hypotheses")
    p_appr.add_argument("ids", nargs="*")
    p_appr.add_argument("--all", action="store_true")
    p_appr.set_defaults(func=cmd_approve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
