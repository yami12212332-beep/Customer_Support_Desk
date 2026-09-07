import asyncio
import os
import sys
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.db.connection import init_pool, close_pool
from app.graph.graph import run_escalation_turn

TEST_USER_ID = 4

async def run_one_cycle(
        pool, checkpointer, thread_id: str, user_query: str, sentiment: str,
        escalation_reason: str, routing_confidence: float, label: str,
):
    print(f"\n{'='*70}\n{label} — submitting escalation\n{'='*70}")
    print(f"query: {user_query}")
    print(f"sentiment={sentiment} reason={escalation_reason} confidence={routing_confidence}")

    result = await run_escalation_turn(
        pool, checkpointer, thread_id,
        user_id=TEST_USER_ID,
        user_query=user_query,
        sentiment=sentiment,
        escalation_reason=escalation_reason,
        routing_confidence=routing_confidence,
    )

    output = result["agent_outputs"]["escalation"]
    print(f"\nsummary: {output.summary}")
    print(f"structured_data: {output.structured_data}")

    if output.structured_data.get("servicenow_error"):
        print(
            f"\n⚠️  Local ticket #{output.structured_data['ticket_id']} was created, "
            f"but ServiceNow sync FAILED — check SERVICENOW_INSTANCE_URL/USER/PASSWORD "
            f"in .env, and confirm the instance is reachable."
        )
    else:
        print(
            f"\n✅ Local ticket #{output.structured_data['ticket_id']} created "
            f"and synced to ServiceNow incident {output.structured_data['external_ticket_ref']}."
        )

async def main():
    pool = await init_pool()

    serde = JsonPlusSerializer(allowed_msgpack_modules=[
        ("app.graph.state", "AgentOutput"),
        ("app.graph.state", "ApprovalRequest"),
    ])
    async with AsyncPostgresSaver.from_conn_string(os.environ["DATABASE_URL"], serde=serde) as checkpointer:
        await checkpointer.setup()

        # --- Cycle 1: low-confidence routing fallback ---
        await run_one_cycle(
            pool, checkpointer,
            thread_id="escalation-e2e-lowconf-1",
            user_query="I don't know, my bill and my account both seem messed up somehow, not sure what's going on.",
            sentiment="neutral",
            escalation_reason="low_confidence",
            routing_confidence=0.4,
            label="LOW CONFIDENCE",
        )

        # --- Cycle 2: angry sentiment override ---
        await run_one_cycle(
            pool, checkpointer,
            thread_id="escalation-e2e-angry-1",
            user_query="This is the THIRD time I've been charged wrong and nobody has fixed it. I want this resolved now.",
            sentiment="angry",
            escalation_reason="angry_sentiment",
            routing_confidence=0.9,
            label="ANGRY SENTIMENT",
        )

        # --- Cycle 3: agent fallback (e.g. billing agent errored) ---
        await run_one_cycle(
            pool, checkpointer,
            thread_id="escalation-e2e-fallback-1",
            user_query="Can you check invoice 2, I think I was double charged.",
            sentiment="frustrated",
            escalation_reason="agent_fallback",
            routing_confidence=0.85,
            label="AGENT FALLBACK",
        )

        await close_pool()

if __name__ == "__main__":
    asyncio.run(main())