import functools
import json
from typing import Optional

import asyncpg
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langgraph.types import interrupt

from app.db import billing_tools
from app.graph.state import GraphState, AgentOutput, ApprovalRequest

def build_billing_tools(pool: asyncpg.Pool, customer_id: int):
    """
    Returns the list of tools bound to this specific request's customer_id.
    Built fresh per-invocation (not module-level) so customer_id can never
    leak across requests and can never be supplied by the model itself —
    it's baked into the closure before the LLM ever sees the tool.
    """

    @tool
    async def get_invoice(invoice_id: int) -> str:
        """Look up a single invoice by ID for the cuncurrent customer."""
        result = await billing_tools.get_invoice(pool, customer_id, invoice_id)
        return json.dumps(result, default=str) if result else "Invoice not found"

    @tool
    async def get_payment_history(account_id: Optional[int] = None, limit: int = 10) -> str:
        """Get recent invoices for the current customer, optionally for one account."""
        result = await billing_tools.get_payment_history(pool, customer_id, account_id, limit)
        return json.dumps(result, default=str)

    @tool
    async def check_duplicate(invoice_id: int) -> str:
        """Check whether an invoice is a genuine duplicate charge (ground truth, not inferred from amount/date matching)."""
        result = await billing_tools.check_duplicate(pool, customer_id, invoice_id)
        return json.dumps(result, default=str)

    @tool
    async def propose_refund(invoice_id: int, amount_cents: int, reason: str, risk_level: str) -> str:
        """
        Propose a refund for human approval. THIS DOES NOT ISSUE THE REFUND —
        it only records a proposal for the approval gate. risk_level must be
        one of: low, medium, high.
        """
        return json.dumps({
            "invoice_id": invoice_id,
            "amount_cents": amount_cents,
            "reason": reason,
            "risk_level": risk_level
        })

    return [get_invoice, get_payment_history, check_duplicate, propose_refund]

SYSTEM_PROMPT = """You are the Billing specialist agent in a customer support system.
 
You investigate billing questions using your tools (get_invoice, get_payment_history,
check_duplicate). You never trust a customer's claim about duplication or overcharging
at face value — always verify with check_duplicate, which reflects ground truth, not
the customer's framing.
 
If a refund is warranted, call propose_refund exactly once with your reasoning. This
does not issue the refund — a human reviews it. Do not call propose_refund for refunds
you are not confident are warranted; explain your findings in your final answer instead.
 
When you are done investigating (whether or not you proposed a refund), respond with
a final plain-text summary of what you found and what you did, addressed to the
orchestrator, not the customer directly.
"""

async def billing_agent_node(state: GraphState, pool: asyncpg.Pool) -> dict:
    """
    LangGraph node signature note: `pool` here is extra context, not part of
    GraphState — in the real graph assembly this gets bound via
    functools.partial(billing_agent_node, pool=pool) when the node is
    registered, since a DB pool doesn't belong in checkpointed graph state.
    """
    tools = build_billing_tools(pool, state.user_id)
    llm = ChatGoogleGenerativeAI(model = "gemini-3.5-flash-lite").bind_tools(tools)
    tools_by_name = {t.name: t for t in tools}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": state.user_query},
    ]

    proposed_action: Optional[dict] = None
    final_summary = ""

    for _ in range(6):
        response = await llm.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            final_summary = response.content if isinstance(response.content, str) else str(response.content)
            break

        for call in response.tool_calls:
            tool_fn = tools_by_name[call["name"]]
            result = await tool_fn.ainvoke(call["args"])

            if call["name"] == "propose_refund":
                proposed_action = json.loads(result)

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })

    # --- no refund proposed: read-only path, no approval needed ---
    if proposed_action is None:
        return {
            "agent_outputs": {
                "billing": AgentOutput(
                    agent_name="billing",
                    summary=final_summary or "Investigated billing query; no action needed.",
                    structured_data={},
                    requires_approval=False,
                    confidence=0.9,
                )
            },
            "agents_completed": ["billing"],
        }

    # --- refund proposed: everything below is the approval gate ---
    approval_request = ApprovalRequest(
        action_type="refund",
        agent="billing",
        details=proposed_action,
        risk_level=proposed_action.get("risk_level", "medium"),
        status="pending"
    )

    # Everything above this line is read-only (LLM calls + read-only tools),
    # so re-execution on resume is safe. See spike + module docstring.
    decision = interrupt(approval_request.model_dump())

    if decision.get("status") == "approved":
        # Executed directly by trusted app code, NOT delegated back to the LLM.
        refund_result = await billing_tools.issue_refund(
            pool,
            state.user_id,
            proposed_action["invoice_id"],
            proposed_action["amount_cents"],
            proposed_action["reason"]
        )
        summary = f"Refund approved and issued: {refund_result}"
    else:
        refund_result = None
        summary = f"Refund was proposed but rejected by reviewer. Original finding: {final_summary}"

    return {
        "agent_outputs": {
            "billing": AgentOutput(
                agent_name="billing",
                summary=summary,
                structured_data={"refund_result": refund_result} if refund_result else {},
                requires_approval=True,
                proposed_action=proposed_action,
                confidence=0.9
            )
        },
        "agents_completed": ["billing"],
        "pending_approvals": {
            "billing": ApprovalRequest(
                action_type="refund",
                agent = "billing",
                details=proposed_action,
                risk_level=proposed_action.get("risk_level", "medium"),
                status=decision.get("status", "rejected")
            )
        },
    }

def make_billing_node(pool: asyncpg.Pool):
    """Bind the DB pool at graph-assembly time so the node fits LangGraph's single-argument (state) -> dict node signature."""
    return functools.partial(billing_agent_node, pool = pool)