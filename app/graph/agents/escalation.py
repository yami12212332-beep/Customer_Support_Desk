import functools
import logging
from typing import Literal, Optional

import asyncpg
from pydantic import BaseModel, Field

from app.db import escalation_tools
from app.graph.llm import get_llm
from app.graph.state import GraphState, AgentOutput

logger = logging.getLogger(__name__)

class EscalationDraft(BaseModel):
    """Structured output only — no tools, no loop. The model's entire job
    is to fill this in once, from context it's handed directly."""
    short_description: str = Field(..., description="One-line summary for the incident title, under ~100 chars")
    description: str = Field(..., description="Fuller explanation for the human reviewer: what happened, what the customer wants")
    priority: Literal["low", "medium", "high"] = Field(..., description="Suggested priority for the human queue")

SYSTEM_PROMPT = """You are drafting a support escalation for a human reviewer.

You are NOT deciding whether to escalate — that decision has already been made.
Your only job is to write a clear, factual summary of the situation so the human
reviewer can act on it quickly, and to suggest a priority.

Do not soften or omit the customer's frustration if sentiment is angry — the
reviewer needs an accurate picture, not a smoothed-over one. Do not speculate
about facts you weren't given; if something is unclear, say so in the
description rather than guessing.
"""


def _build_context_message(state: GraphState, ticket_history: list[dict]) -> str:
    """Plain-text context block for the single drafting call. No tool
    results to interleave here since there's no tool loop — just render
    what we already have."""
    history_lines = "\n".join(
        f"- ticket {t['ticket_id']}: status={t['status']}, sentiment={t['sentiment']}, "
        f"summary={t.get('summary') or '(none)'}"
        for t in ticket_history
    ) or "(no prior tickets)"

    return (
        f"Customer query: {state.user_query}\n"
        f"Detected sentiment: {state.sentiment}\n"
        f"Escalation reason: {state.escalation_reason}\n"
        f"Routing confidence: {state.routing_confidence}\n"
        f"Prior ticket history:\n{history_lines}"
    )

async def escalation_agent_node(state: GraphState, pool:asyncpg.Pool) -> dict:
    """
    LangGraph node signature note: `pool` is bound via functools.partial
    at graph-assembly time, same pattern as billing/account — see
    make_escalation_node below.
    """
    ticket_history = await escalation_tools.get_ticket_history(pool, state.user_id)

    llm = get_llm("escalation").with_structured_output(EscalationDraft)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_context_message(state, ticket_history)},
    ]
    draft: EscalationDraft = await llm.ainvoke(messages)
    ticket = await escalation_tools.create_ticket(
        pool,
        customer_id=state.user_id,
        account_id=None,
        summary=draft.description,
        sentiment=state.sentiment,
        escalation_reason=state.escalation_reason or "unspecified",
    )

    external_ref: Optional[str] = None
    servicenow_error: Optional[str] = None
    try:
        incident = await escalation_tools.create_servicenow_incident(
            short_description=draft.short_description,
            description=draft.description,
            priority=draft.priority,
            correlation_id=str(ticket["ticket_id"]),
        )
        external_ref = incident["number"]
        await escalation_tools.update_ticket_external_ref(
            pool, state.user_id, ticket["ticket_id"], external_ref
        )
    except Exception as exc:
        servicenow_error = str(exc)
        logger.error(
            "ServiceNow incident creation failed for ticket_id=%s: %s",
            ticket["ticket_id"], exc,
        )

    summary = (
        f"Escalated to human review (reason: {state.escalation_reason}). "
        f"Local ticket #{ticket['ticket_id']} created."
    )
    if external_ref:
        summary += f"ServiceNow incident {external_ref} created."
    else:
        summary += (
            "ServiceNow incident creation FAILED — local ticket exists "
            "but is not yet synced externally; needs manual reconciliation."
        )

    return {
        "agent_outputs": {
            "escalation": AgentOutput(
                agent_name="escalation",
                summary=summary,
                structured_data={
                    "ticket_id": ticket["ticket_id"],
                    "external_ticket_ref": external_ref,
                    "servicenow_error": servicenow_error,
                },
                requires_approval=False,
                confidence=1.0,
            )
        },
        "agents_completed": ["escalation"],
    }

def make_excalation_node(pool: asyncpg.Pool):
    """Bind the DB pool at graph-assembly time, same pattern as
    make_billing_node / make_account_node."""
    return functools.partial(escalation_agent_node, pool=pool)