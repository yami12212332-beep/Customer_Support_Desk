from typing import Optional
import asyncpg

async def get_account_details(pool: asyncpg.Pool, customer_id: int, account_id: int) -> Optional[dict]:
    """
    Full detail for one account, scoped to this customer, including its
    active subscriptions and payment methods — the agent needs this BEFORE
    proposing a closure, so it can see (and mention in its proposal) any
    active subscriptions that closure would leave dangling, rather than
    proposing a closure blind to that.
 
    Returns None if the account doesn't exist or doesn't belong to this
    customer — same "don't distinguish not-found from not-yours" principle
    as billing_tools.get_invoice.
    """
    async with pool.acquire() as conn:
        account = await conn.fetchrow(
            """
            SELECT account_id, customer_id, status, fraud_flag_reason, closed_at
            FROM accounts
            WHERE customer_id = $1 AND account_id = $2
            """,
            customer_id, account_id,
        )
        if account is None:
            return None

        subscription = await conn.fetch(
            """
            SELECT subscription_id, plan_name, status, price_cents, canceled_at
            FROM subscriptions
            WHERE account_id = $1
            ORDER BY subscription_id
            """,
            account_id,
        )
        payment_methods = await conn.fetch(
            """
            SELECT payment_method_id, method_type, status
            FROM payment_methods
            WHERE account_id = $1
            ORDER BY payment_method_id
            """,
            account_id,
        )

    return {
        **dict(account),
        "subscriptions": [dict(s) for s in subscription],
        "payment_methods": [dict(p) for p in payment_methods],
    }

async def list_accounts(pool: asyncpg.Pool, customer_id: int) -> list[dict]:
    """
    Lightweight list of every account belonging to this customer — useful
    when the query doesn't specify an account_id and the agent needs to
    figure out which account is relevant (a customer can hold multiple,
    per design-doc.md 4b).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT account_id, status, closed_at
            FROM accounts
            WHERE customer_id = $1
            ORDER BY account_id
            """,
            customer_id,
        )

    return [dict(r) for r in rows]

async def close_account(pool: asyncpg.Pool, customer_id: int, account_id: int, reason: str) -> dict:
    """
    Executes account closure. WRITE / side-effecting.
 
    Same replay-safety rule as billing_tools.issue_refund: this must only
    ever be called from the code path AFTER interrupt() returns approved —
    never before it, since interrupt() replays the node from the top on
    resume, and calling this before interrupt() would close the account
    twice on replay (harmless here since it's idempotent-guarded below,
    but the ordering rule is the same for a reason: don't rely on the
    guard to paper over calling a write tool at the wrong point).
 
    Idempotency guard: raises if already canceled, rather than silently
    no-op-ing — an agent needs to know a "close this account" action
    didn't actually do anything, not have that fact silently swallowed.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            account = await conn.fetchrow(
                """
                SELECT account_id, status
                FROM accounts
                WHERE customer_id = $1 AND account_id = $2
                FOR UPDATE
                """,
                customer_id, account_id,
            )
            if account is None:
                raise ValueError(f"Account {account_id} not found for this customer.")
            if account["status"] == "canceled":
                raise ValueError(f"Account {account_id} is alredy canceled.")

            await conn.execute(
                "UPDATE accounts SET state = 'canceled, closed_at = now() WHERE account_id = $1",
                account_id,
            )

    return {
        "account_id": account_id,
        "status": "canceled",
        "reason": reason
    }

async def update_payment_method_status(
    pool: asyncpg.Pool, customer_id: int, payment_method_id: int, new_status: str
) -> dict:
    """
    Executes a payment method status change. WRITE / side-effecting.
    Same post-interrupt-only rule as close_account/issue_refund.
 
    Deliberately restricted to new_status == 'removed' only — this tool
    represents "customer wants an expired/wrong card taken off their
    account," not general payment_methods field editing. Adding a NEW
    payment method is a different, larger feature (needs real card
    data/tokenization) explicitly out of scope for this project.
    """
    if new_status != "removed":
        raise ValueError(
            f"update_payment_method_status only supports new_status='removed', got {new_status!r}"
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            pm = await conn.fetchrow(
                """
                SELECT pm.payment_method_id, pm.status
                FROM payment_methods pm
                JOIN accounts a ON a.account_id = pm.account_id
                WHERE a.customer_id = $1 AND pm.payment_method_id = $2
                FOR UPDATE OF pm
                """,
                customer_id, payment_method_id,
            )
            if pm is None:
                raise ValueError(f"payment method {payment_method_id} not found for this customer.")
            if pm["status"] == "removed":
                raise ValueError(f"Payment method {payment_method_id} is alredy removed.")
            await conn.execute(
                "UPDATE payment_methods SET status = 'removed' WHERE payment_method_id = $1",
                payment_method_id,
            )

    return {"payment_method_id": payment_method_id, "status": "removed"}