"""What a conversation's subagents did, over the HTTP API (#286) — the TUI's Subagents view and the Android app read this.
(ccc.api_subagents is about subagent *definitions*; the runs come from Claude Code's transcripts: ccc.subagent_runs.)

GET /api/v1/conversations/{id}/subagents                a summary and every run of the conversation, oldest first, as a list
                                                        with `parent_id` (the tree) but without prompts and steps
GET /api/v1/conversations/{id}/subagents/{agent_id}     one run with its prompt, steps (tool, argument, offset and duration in
                                                        ms, error) and result; a step with `agent_id` started that nested run

A run: id, parent_id ("" = started by the conversation), kind, description, status (running | done | error | stopped),
started, ended, duration_ms, tokens, tool_count, model, depth, background. Poll for live: a running one grows.
"""
from __future__ import annotations

from http import HTTPStatus

from ccc import subagent_runs
from ccc.api_common import ApiError, route
from ccc.i18n import _


def summary_of(runs: list[subagent_runs.Run]) -> dict:
    """Totals for the header. `span_ms` is the wall-clock time from the first start to the last end (runs overlap)."""
    starts = [r.started for r in runs if r.started]
    ends = [r.ended for r in runs if r.ended]
    span = int((max(ends) - min(starts)).total_seconds() * 1000) if starts and ends else 0
    return {
        "total": len(runs),
        "running": sum(r.status == subagent_runs.RUNNING for r in runs),
        "errors": sum(r.status in (subagent_runs.ERROR, subagent_runs.STOPPED) for r in runs),
        "span_ms": max(span, 0),
        "tokens": sum(r.tokens for r in runs if not r.parent_id),  # a nested run's tokens are in its parent's
    }


class SubagentRunEndpoints:
    storage: object

    def _subagent_run_routes(self) -> list:
        ok = route
        return [
            ok(r"/conversations/(\d+)/subagents", lambda ids, q, b: self.subagent_runs(*ids)),
            ok(r"/conversations/(\d+)/subagents/([A-Za-z0-9]+)", lambda ids, q, b: self.subagent_run(*ids)),
        ]

    def subagent_runs(self, queue_id: int) -> dict:
        runs = subagent_runs.read_runs(self._queue(queue_id).session_id)
        return {"summary": summary_of(runs), "runs": [r.as_dict() for r in runs]}

    def subagent_run(self, queue_id: int, agent_id: str) -> dict:
        run = subagent_runs.find_run(self._queue(queue_id).session_id, agent_id)
        if run is None:
            raise ApiError(HTTPStatus.NOT_FOUND, _("No such subagent run."))
        return run.as_dict(detail=True)
