import asyncio
import os
import sys
import warnings
warnings.filterwarnings("ignore")
 
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
 
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
 
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
 
from app.db.connection import init_pool, close_pool
from app.graph.graph import run_account_turn, resume_account_turn
 
# Adjust to match a real customer/account in your seed data.
TEST_USER_ID = 3
TEST_ACCOUNT_ID = 3
 
 
async def run_one_cycle(pool, checkpointer, thread_id: str, user_query: str, label: str):
    print(f"\n{'='*70}\n{label} — PHASE 1: submitting query\n{'='*70}")
    print(f"query: {user_query}")
 
    result = await run_account_turn(
        pool, checkpointer, thread_id,
        user_id=TEST_USER_ID,
        user_query=user_query,
    )
 
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print(f"\n✅ Paused for approval. Proposal:\n{payload}")
        print(f"📧 A real approval email should now be in REVIEWER_EMAIL's inbox.")
 
        # --- OPTION A: manual resume, bypasses the real email round-trip ---
        # Comment this block out if you want to test OPTION B instead (see
        # module docstring) — reply to the real email and let
        # scripts/run_reply_poller.py pick it up on its own.
        # print(f"\n{label} — PHASE 2: simulating reviewer approval (manual, bypasses email/poller)")
        # final = await resume_account_turn(
        #     pool, checkpointer, thread_id, "approved", user_id=TEST_USER_ID
        # )
        # print(f"\n✅ Resumed. agent_outputs:\n{final['agent_outputs']['account']}")
    else:
        print(f"\nNo interrupt raised — agent decided no change was warranted.")
        print(f"agent_outputs:\n{result['agent_outputs']['account']}")
 
 
async def main():
    pool = await init_pool()
 
    async with AsyncPostgresSaver.from_conn_string(os.environ["DATABASE_URL"]) as checkpointer:
        await checkpointer.setup()
 
        # --- Cycle 1: account closure ---
        await run_one_cycle(
            pool, checkpointer,
            thread_id="account-e2e-closure-1.3",
            user_query=f"Please close account {TEST_ACCOUNT_ID}, I don't need it anymore.",
            label="CLOSURE",
        )
 
        # --- Cycle 2: payment method removal ---
        # NOTE: adjust the query to reference a real payment_method_id from
        # your seed data if this one doesn't resolve — the agent should
        # call get_account_details/list_accounts itself to find it if you
        # phrase this more vaguely, but being explicit here keeps this
        # runner deterministic for a first pass.
        await run_one_cycle(
            pool, checkpointer,
            thread_id="account-e2e-paymethod-1.3",
            user_query=f"Can you remove the payment method on account {TEST_ACCOUNT_ID}? It's expired.",
            label="PAYMENT METHOD REMOVAL",
        )
 
    await close_pool()
 
 
if __name__ == "__main__":
    asyncio.run(main())