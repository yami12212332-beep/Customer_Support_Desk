import os
from typing import Optional

import asyncpg
import httpx

async def get_ticket_history(
        pool: asyncpg.Pool, customer_id: int, limit: int = 10
) -> list[dict]:
    """
    Prior support_tickets for this customer, most recent first. Used as
    context for the escalation summary — e.g. "this is their third contact
    this month" changes how a reviewer should prioritize it, even though
    it doesn't change WHETHER escalation happens (that's already decided).
    """
    query = """
        SELECT ticket_id, account_id, status, sentiment, summary,
               external_ticket_ref, created_at
        FROM support_tickets
        WHERE customer_id = $1
        ORDER BY created_at DESC
        LIMIT $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, customer_id, limit)
    return [dict[r] for r in rows]

async def create_ticket(
        pool: asyncpg.Pool,
        customer_id: int,
        account_id: Optional[int],
        summary: str,
        sentiment: str,
        escalation_reason: str,
        status: str = "escalated",
) -> dict:
    """
    Creates the permanent local support_tickets record. WRITE / side-
    effecting, but unlike issue_refund/close_account there is no "already
    done" state to guard against — a customer can have multiple tickets,
    so this doesn't need the FOR UPDATE + idempotency-guard pattern those
    two use.
 
    Deliberately does NOT call ServiceNow itself. Kept as two separate
    calls (this, then create_servicenow_incident) rather than one combined
    function, so the local row exists and has a ticket_id BEFORE the
    outbound HTTP call is attempted — that ticket_id becomes ServiceNow's
    correlation_id, giving traceability in both directions even if the
    external call subsequently fails.
 
    account_id may be None — some escalations (e.g. a general angry-
    sentiment complaint with no specific account in play) aren't tied to
    one account; support_tickets.account_id is nullable for this reason.
    """
    full_summary = f"[{escalation_reason}] {summary}"
    query = """
        INSERT INTO support_tickets (customer_id, account_id, status, sentiment, summary)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING ticket_id, customer_id, account_id, status, sentiment, summary, created_at
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            query, customer_id, account_id, status, sentiment, full_summary
        )
    return dict(row)

async def update_ticket_external_ref(
        pool: asyncpg.Pool, customer_id: int, ticket_id: int, external_ticket_ref: str
) -> dict:
    """
    Writes ServiceNow's returned incident number back onto the local row,
    once create_servicenow_incident has succeeded. Scoped by customer_id
    like every other write here, even though ticket_id alone would
    probably be enough — consistent enforcement beats "safe in this one
    case, trust me."
 
    Raises if the ticket doesn't exist for this customer, same "don't
    silently no-op on bad input" rule as issue_refund/close_account.
    """
    query = """
        UPDATE support_tickets
        SET external_ticket_ref = $1
        WHERE customer_id = $2 AND ticket_id = $3
        RETURNING ticket_id, external_ticket_ref
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, external_ticket_ref, customer_id, ticket_id)
    if row is None:
        raise ValueError(f"Ticket {ticket_id} not found for this customer.")
    return dict(row)

_PRIORITY_TO_SERVICENOW_URGENCY = {
    "high": "1",
    "medium": "2",
    "low": "3",
}

async def create_servicenow_incident(
        short_description: str,
        description: str,
        priority: str,
        correlation_id: Optional[str] = None,
) -> dict:
    """
    Creates a real incident in ServiceNow via the Table API
    (POST /api/now/table/incident). WRITE / side-effecting against a
    system this app doesn't own — treat failures as loud, not quiet.
 
    priority must be one of: low, medium, high (same vocabulary used
    elsewhere in this system, e.g. risk_level) — translated here to
    ServiceNow's own urgency/impact codes ("1"/"2"/"3" for High/Medium/Low)
    so callers never need to know ServiceNow's numbering convention.
 
    correlation_id should be the LOCAL ticket_id (as a string) from
    create_ticket, so the incident in ServiceNow references which local
    record spawned it — traceability in the other direction from
    external_ticket_ref.
 
    Raises on any non-2xx response or network failure rather than
    swallowing the error — same "a tool that fails loudly is safer than
    one that silently no-ops" principle as issue_refund/close_account.
    Callers (escalation.py) are responsible for deciding what happens to
    the already-created local ticket if this raises (e.g. leaving
    external_ticket_ref NULL as the visible signal reconciliation is
    needed — see the migration note on that column).
 
    Credentials: SERVICENOW_INSTANCE_URL, SERVICENOW_USER,
    SERVICENOW_PASSWORD, read from the environment at call time. Never
    accept these as function arguments — see module docstring.
    """
    instance_url = os.environ["SERVICENOW_INSTANCE_URL"]
    user = os.environ["SERVICENOW_USER"]
    password = os.environ["SERVICENOW_PASSWORD"]

    urgency = _PRIORITY_TO_SERVICENOW_URGENCY.get(priority, "2")

    url = f"{instance_url.rstrip("/")}/api/now/table/incident"
    payload = {
        "short_description": short_description,
        "description": description,
        "urgency": urgency,
        "impact": urgency,
    }
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url,
            json=payload,
            auth=(user, password),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

    result = data["result"]
    return {
        "sys_id": result["sys_id"],
        "number": result["number"],
        "urgency": result.get("urgency"),
        "impact": result.get("impact"),
    }