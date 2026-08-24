import functools
import json
from typing import Optional

import asyncpg
from langchain_core.tools import tool
from langgraph.types import interrupt

from app.db import account_tools
from app.graph.llm import get_llm
from app.graph.state import GraphState, AgentOutput, ApprovalRequest

def build_account_tools(pool: asyncpg.Pool, customer_id: int):
    """customer_id bound via closure, never an LLM arg — see account_tools.py
    module docstring, same rule as billing.py."""

    @tool
    async def list_accounts() -> str:
        """List all accounts belonging to the current customer (a customer
        can hold more than one). Use this first if the query doesn't
        specify which account."""
        result = await account_tools.list_accounts(pool, customer_id)
        return json.dumps(result, default=str)
    
    @tool
    async def get_account_details(account_id: int) -> str:
        """Get full details for one account — status, active subscriptions,
        and payment methods. ALWAYS call this before proposing a closure or
        payment method removal, so your proposal can account for (and
        mention) any active subscriptions or other payment methods."""
        result = await account_tools.get_account_details(pool, customer_id, account_id)
        return json.dumps(result, default=str) if result else "Account not found"

    @tool
    def propose_account_change(
        action_type: str,
        target_id: int,
        reason: str,
        risk_level: str,
        details: str = "",
    ) -> str:
        """
         Propose an account change for human approval. THIS DOES NOT EXECUTE
        THE CHANGE — it only records a proposal for the approval gate.
 
        action_type must be exactly one of: "account_closure",
        "payment_method_change".
        - For account_closure: target_id is the account_id to close.
        - For payment_method_change: target_id is the payment_method_id to
          remove.
 
        risk_level must be one of: low, medium, high. Note: for
        account_closure specifically, the system will always treat this as
        high risk regardless of what you set here — closure is irreversible.
        Still provide your honest assessment; do not just always say "high"
        to game this.
        """
        if action_type not in ("account_closure", "payment_method_change"):
            return json.dumps({
                "error": f"Invalid action_type {action_type!r}. Must be"
                f"'account_closure' or 'payment_method_closure'"
            })
        return json.dumps({
            "action_type": action_type,
            "target_id": target_id,
            "reason": reason,
            "risk_level": risk_level,
            "details": details
        })

    return [list_accounts, get_account_details, propose_account_change]

SYSTEM_PROMPT = """You are the Account specialist agent in a customer support system.
 
You handle account-level questions and changes using your tools (list_accounts,
get_account_details, propose_account_change). You can only two kinds of changes:
closing an account, or removing a payment method. You cannot change a customer's
email or add a new payment method — if asked, explain this is outside what you can
do and that a human would need to handle it.
 
ALWAYS call get_account_details before proposing a closure, so you can see (and
mention in your reasoning) any active subscriptions the account still has — closing
an account with active subscriptions still on it is worth flagging explicitly, not
silently proposing anyway.
 
If a change is warranted, call propose_account_change exactly once. This does not
execute the change — a human reviews it. Do not propose changes you're not confident
are warranted; explain your findings in your final answer instead.
 
When you are done, respond with a final plain-text summary of what you found and did,
addressed to the orchestrator, not the customer directly.
"""

async def account_agent_node(state: GraphState, pool: asyncpg.Pool) -> dict:
    tools = build_account_tools(pool, state.user_id)
    llm = get_llm("account").bind_tools(tools)
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

            if call["name"] == "propose_account_change":
                parsed = json.loads(result)
                if "error" not in parsed:
                    proposed_action = parsed

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            })

    if proposed_action is None:
        return {
            "agent_output": {
                "account": AgentOutput(
                    agent_name="account",
                    summary=final_summary or "Investigated account query; no action needed",
                    structured_data={},
                    requires_approval=False,
                    confidence=0.9,
                )
            },
            "agents_completed": ["account"],
        }

    effective_risk_level = proposed_action["risk_level"]
    if proposed_action["action_type"] == "account_closure":
        effective_risk_level = "high"

    approval_request = ApprovalRequest(
        action_type=proposed_action["action_type"],
        agent = "account",
        details=proposed_action,
        risk_level=effective_risk_level,
        status="pending",
    )

    decision = interrupt(approval_request.model_dump())

    if decision.get("status") == "approved":
        if proposed_action["action_type"] == "account_closure":
            change_result = await account_tools.close_account(
                pool, state.user_id, proposed_action["target_id"], proposed_action["reason"]
            )

        else:
            change_result = await account_tools.update_payment_method_status(
                pool, state.user_id, proposed_action["target_id"], "removed"
            )
        summary = f"Change approved and executed: {change_result}"
    else:
        change_result = None
        summary = f"Change was proposed but rejected by reviewer. Original finding: {final_summary}"

    return {
        "agent_outputs": {
            "account": AgentOutput(
                agent_name="account",
                summary=summary,
                structured_data={"change_result": change_result} if change_result else {},
                requires_approval=True,
                proposed_action=proposed_action,
                confidence=0.9,
            )
        },
        "agents_completed": ["account"],
        "pending_approvals": {
            "account": ApprovalRequest(
                action_type=proposed_action["action_type"],
                agent="account",
                details=proposed_action,
                risk_level=effective_risk_level,
                status=decision.get("status", "rejected"),
            )
        },
    }

def make_account_node(pool: asyncpg.Pool):
    return functools.partial(account_agent_node, pool=pool)