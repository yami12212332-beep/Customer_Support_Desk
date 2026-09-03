import asyncpg
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.graph.state import GraphState
from app.graph.agents.billing import make_billing_node

async def build_billing_only_graph(pool: asyncpg.Pool, checkpointer: AsyncPostgresSaver):
    """
    checkpointer is passed in (not created here) because it needs an async
    context manager lifetime managed by the caller — see run() below for
    the pattern. Building it inside this function would tie its lifetime
    to a function call that returns before you're done using the graph.
    """
    g = StateGraph(GraphState)
    g.add_node("billing", make_billing_node(pool))
    g.set_entry_point("billing")
    g.add_edge("billing", END)
    return g.compile(checkpointer=checkpointer)

async def run_billing_turn(
        pool: asyncpg.Pool,
        checkpointer: AsyncPostgresSaver,
        thread_id: str,
        user_id: int,
        user_query: str,
) -> dict:
    """
    Starts a NEW billing conversation turn. Returns whatever the graph
    returns after this invoke — either a final state (no approval needed)
    or an interrupt payload (approval needed, caller must resume later).
    """
    graph = await build_billing_only_graph(pool, checkpointer)
    config = {
        "configurable": {"thread_id": thread_id},
        "tags": ["billing", "e2e-manual-run"],
        "metadata": {"user_id": user_id, "thread_id": thread_id},
    }
    result = await graph.ainvoke(
        GraphState(user_query=user_query, user_id=user_id, trace_id=thread_id),
        config=config,
    )
    return result

async def resume_billing_turn(
        pool: asyncpg.Pool,
        checkpointer: AsyncPostgresSaver,
        thread_id: str,
        decision_status: str,
        user_id: int,
) -> dict:
    """
    Resumes a PAUSED billing conversation (one that previously returned an
    __interrupt__ from run_billing_turn). This is what the reviewer-facing
    endpoint (app/api/review.py, not yet built) will call.
    """
    from langgraph.types import Command

    graph = await build_billing_only_graph(pool, checkpointer)
    config = {
        "configurable": {"thread_id": thread_id},
        "tags": ["billing", "e2e-manual-run"],
        "metadata": {"user_id": user_id, "thread_id": thread_id}
    }
    result = await graph.ainvoke(
        Command(resume={"status":decision_status}),
        config=config,
    )
    return result