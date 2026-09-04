import asyncpg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
 
from app.db import approval_log
from app.notifications import email_client

async def on_interrupt(pool: asyncpg.Pool, thread_id: str, customer_id: int, interrupt_payload: dict) -> None:
    """
    interrupt_payload is result["__interrupt__"][0].value — the
    ApprovalRequest.model_dump() dict the agent node passed to interrupt().
    """
    await approval_log.create_pending_approval(
        pool,
        thread_id=thread_id,
        customer_id=customer_id,
        agent=interrupt_payload["agent"],
        action_type=interrupt_payload["action_type"],
        risk_level=interrupt_payload["risk_level"],
        details=interrupt_payload["details"],
    )
 
    message_id = await email_client.send_approval_request_email(interrupt_payload, thread_id)
    await approval_log.set_outbound_message_id(pool, thread_id, message_id)

async def dispatch_resume(
    pool: asyncpg.Pool,
    checkpointer: AsyncPostgresSaver,
    agent: str,
    thread_id: str,
    customer_id: int,
    decision_status: str,
) -> dict:
    """
    Called by the poller AFTER approval_log.resolve_pending_approval()
    has already returned a non-None row for this thread_id — i.e. this
    call is only ever made once per approval, by construction, not by
    trusting the caller to have checked.
    """
    # Imported here, not at module level, to avoid a circular import
    # (graph.py will import on_interrupt from this module).
    from app.graph.graph import resume_billing_turn, resume_account_turn
 
    if agent == "billing":
        return await resume_billing_turn(pool, checkpointer, thread_id, decision_status, user_id=customer_id)
    elif agent == "account":
        return await resume_account_turn(pool, checkpointer, thread_id, decision_status, user_id=customer_id)
    else:
        raise ValueError(f"Unknown agent {agent!r} in approval_log — cannot dispatch resume.")