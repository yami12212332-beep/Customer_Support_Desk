from typing import Optional
import asyncpg

async def get_invoice(pool: asyncpg.Pool, customer_id: int, invoice_id: int) -> Optional[dict]:
    """
    Fetch a single invoice, scoped to this customer.
    Returns None if the invoice doesn't exist OR doesn't belong to this
    customer — both cases look identical to the caller, which is deliberate
    (don't leak "it exists but isn't yours" via a different error shape).
    """
    query = """
        SELECT i.invoice_id, i.account_id, i.subscription_id, i.amount_cents,
            i.status, i.duplicate_of_invoice_id, i.issued_at
        FROM invoices i
        JOIN accounts a ON a.account_id = i.account_id
        WHERE a.customer_id = $1 AND i.invoice_id = $2
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, customer_id, invoice_id)
    return dict(row) if row else None

async def get_payment_history(pool: asyncpg.Pool, customer_id: int, account_id: Optional[int] = None, limit: int = 20) -> list[dict]:
    """
    Recent invoices for this customer, optionally narrowed to one account.
    If account_id is given but doesn't belong to this customer, returns []
    rather than raising — same "don't distinguish not-found from not-yours"
    principle as get_invoice.
    """
    if account_id is not None:
        query = """
            SELECT i.invoice_id, i.account_id, i.amount_cents, i.status,
                   i.duplicate_of_invoice_id, i.issued_at
            FROM invoices i
            JOIN accounts a ON a.account_id = i.account_id
            WHERE a.customer_id = $1 AND i.account_id = $2
            ORDER BY i.issued_at DESC
            LIMIT $3
        """
        args = (customer_id, account_id, limit)
    else:
        query = """
            SELECT i.invoice_id, i.account_id, i.amount_cents, i.status,
                   i.duplicate_of_invoice_id, i.issued_at
            FROM invoices i
            JOIN accounts a ON a.account_id = i.account_id
            WHERE a.customer_id = $1
            ORDER BY i.issued_at DESC
            LIMIT $2
        """
        args = (customer_id, limit)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [dict(r) for r in rows]

async def check_duplicate(pool: asyncpg.Pool, customer_id: int, invoice_id: int) -> dict:
    """
    Ground-truth duplicate check.
 
    Per design-doc.md 4b: duplication is determined by whether
    `duplicate_of_invoice_id` is populated — NOT by comparing amount/date
    across invoices. This function deliberately does not do fuzzy matching;
    that's the point (a false claim of duplication has this field NULL and
    this function must say so, even if another invoice happens to have the
    same amount).
    """
    invoice = await get_invoice(pool, customer_id, invoice_id)
    if invoice is None:
        return {"found": False, "is_duplicate": False, "original_invoice_id": None}

    dup_of = invoice["duplicate_of_invoice_id"]
    return {
        "found": True,
        "is_duplicate": dup_of is not None,
        "original_invoice_id": dup_of
    }

async def issue_refund(
        pool: asyncpg.Pool,
        customer_id: int,
        invoice_id: int,
        amount_cents: int,
        reason: str,
) -> dict:
    """
    Executes a refund. This is a WRITE / side-effecting call.
 
    IMPORTANT (see the interrupt() spike): this must only be invoked AFTER
    a human approval has resolved to "approved" — i.e. called from the code
    path that runs *after* `interrupt()` returns, never before it. Because
    LangGraph re-runs a node from the top on resume, calling this before
    interrupt() in the same node would issue the refund twice on replay.
 
    Validates the invoice belongs to this customer and isn't already
    refunded before writing, and raises rather than silently no-op-ing on
    invalid input — a refund tool swallowing an error silently is worse
    than one that throws, since the agent needs to know it failed.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            invoice = await conn.fetchrow(
                """
                SELECT i.invoice_id, i.status, i.amount_cents
                FROM invoices i
                JOIN accounts a ON a.account_id = i.account_id
                WHERE a.customer_id = $1 AND i.invoice_id = $2
                FOR UPDATE OF i
                """,
                customer_id,
                invoice_id,
            )
            if invoice is None:
                raise ValueError(f"Invoice {invoice_id} not found for this customer.")
            if invoice["status"] == "refunded":
                raise ValueError(f"Invoice {invoice_id} is alredy refunded.")
            if amount_cents > invoice["amount_cents"]:
                raise ValueError(f"Refund amount {amount_cents} exceeds invoice amount {invoice["amount_cents"]}")

            await conn.execute(
                "UPDATE invoices SET status = 'refunded' WHERE invoice_id = $1",
                invoice_id,
            )

    return {
        "invoice_id": invoice_id,
        "refunded_amount_cents": amount_cents,
        "reason": reason,
        "status": "refunded"
    }