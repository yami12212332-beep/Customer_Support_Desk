import asyncio
import os
import sys
import warnings
warnings.filterwarnings(action="ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.connection import init_pool, close_pool
from app.graph.graph import run_billing_turn, resume_billing_turn
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

TEST_USER_ID = 2

async def main():
    pool = await init_pool()

    async with AsyncPostgresSaver.from_conn_string(os.environ["DATABASE_URL"]) as checkpointer:
        await checkpointer.setup()

        thread_id = "e2e-test-thread-4"
        print("\n=== PHASE 1: submitting billing query (real LLM + real DB) ===")
        result = await run_billing_turn(
            pool, checkpointer, thread_id,
            user_id=TEST_USER_ID,
            user_query="I was cherged twice for invoice 2, can you check and refund the duplicated?"
        )

        if "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            print(f"\n✅ Graph paused for approval, as expected. Proposal: {payload}")

            print(f"\n === Phase 2: simulating reviewer approval ===")
            final = await resume_billing_turn(pool, checkpointer, thread_id, "approved", user_id= TEST_USER_ID)
            print(f"\n✅ Resumed. Final agent_outputs: {final['agent_outputs']}")

        else:
            print(f"\nNo interrupt raised - agent decided no refund was warranted")
            print(f"agent_outputs: {result['agent_outputs']}")

    await close_pool()

if __name__ == "__main__":
    asyncio.run(main())