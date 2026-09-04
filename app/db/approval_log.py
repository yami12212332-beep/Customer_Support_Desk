from typing import Optional
import asyncpg

async def create_pending_approval(
        pool: asyncpg.Pool,
        thread_id: str,
        customer_id: int,
        agent: str,
        action_type: str,
        risk_level: str,
        details: dict,
) -> int:
    """
    Called once, from the driver code (graph.py's run_*_turn), the moment
    an __interrupt__ is first observed — never from inside the agent node
    itself (same replay-safety reasoning as issue_refund: this must not
    execute twice, and driver-level "did I just get __interrupt__ back"
    only fires once, unlike node code which replays on resume).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO approval_log
                (thread_id, customer_id, agent, action_type, risk_level, details, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
            RETURNING approval_id
            """,
            thread_id, customer_id, agent, action_type, risk_level, details,
        )
    return row["approval_id"]

async def set_outbound_message_id(pool: asyncpg.Pool, thread_id: str, message_id: str) -> None:
    """Set after the approval-request email actually sends successfully —
    if the send fails, don't call this, so a retry can be told apart from
    a message that's already been dispatched."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_log SET outbound_message_id = $1 WHERE thread_id = $2",
            message_id, thread_id,
        )

async def find_pending_by_message_ids(pool: asyncpg.Pool, candidate_ids: list[str]) -> Optional[dict]:
    """
    Given the set of Message-IDs pulled from an inbound reply's
    In-Reply-To + References headers, find the pending approval (if any)
    whose outbound_message_id matches one of them.
 
    Only matches status='pending' rows — an already-resolved approval
    shouldn't match again even if a stale reply to it arrives late.
    """
    if not candidate_ids:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT approval_id, thread_id, customer_id, agent, action_type,
                   risk_level, details, status
            FROM approval_log
            WHERE outbound_message_id = ANY($1::varchar[]) AND status = 'pending'
            """,
            candidate_ids,
        )
    return dict(row) if row else None

async def resolve_pending_approval(pool: asyncpg.Pool, thread_id: str, new_status: str) -> Optional[dict]:
    """
    THE idempotency guard. Returns the updated row if this call actually
    transitioned pending -> new_status, or None if it was already resolved
    (by a prior call, a race, or a retry) — the caller MUST treat None as
    "do nothing further," not as an error to retry.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE approval_log
            SET status = $1
            WHERE thread_id = $2 AND status = 'pending'
            RETURNING approval_id, thread_id, customer_id, agent, action_type, risk_level, details, status
            """,
            new_status, thread_id,
        )
    return dict(row) if row else None