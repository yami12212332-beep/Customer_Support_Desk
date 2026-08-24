import asyncio
import os
import sys
import warnings
warnings.filterwarnings("ignore")
 
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
 
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
 
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
 
from app.db.connection import init_pool, close_pool
from app.graph.state import GraphState
from app.graph.agents.account import make_account_node
 
# Adjust to match a real customer/account in your seed data.
TEST_USER_ID = 4
TEST_ACCOUNT_ID = 4
 
 
def build_account_only_graph(pool, checkpointer):
    g = StateGraph(GraphState)
    g.add_node("account", make_account_node(pool))
    g.set_entry_point("account")
    g.add_edge("account", END)
    return g.compile(checkpointer=checkpointer)
 
 
async def run_one_cycle(pool, checkpointer, thread_id: str, user_query: str, label: str):
    graph = build_account_only_graph(pool, checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
 
    print(f"\n{'='*70}\n{label} — PHASE 1: submitting query\n{'='*70}")
    print(f"query: {user_query}")
 
    result = await graph.ainvoke(
        GraphState(user_query=user_query, user_id=TEST_USER_ID, trace_id=thread_id),
        config=config,
    )
 
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print(f"\n✅ Paused for approval. Proposal:\n{payload}")
 
        print(f"\n{label} — PHASE 2: simulating reviewer approval")
        final = await graph.ainvoke(Command(resume={"status": "approved"}), config=config)
        print(f"\n✅ Resumed. agent_outputs:\n{final['agent_outputs']['account']}")
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
            thread_id="account-e2e-closure-2",
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
            thread_id="account-e2e-paymethod-2",
            user_query=f"Can you remove the payment method on account that is expired.",
            label="PAYMENT METHOD REMOVAL",
        )
 
    await close_pool()
 
 
if __name__ == "__main__":
    asyncio.run(main())