import asyncpg
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.graph.state import GraphState
from app.graph.agents.billing import make_billing_node
from app.graph.agents.account import make_account_node
from app.graph.hitl import on_interrupt

# ============================== BILLING ==============================

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
    Starts a NEW billing conversation turn. If the graph pauses for
    approval, this is where the approval email actually gets sent — see
    hitl.py's docstring for why it MUST happen here, in driver code,
    rather than inside billing_agent_node.
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

    if "__interrupt__" in result:
        await on_interrupt(pool, thread_id, user_id, result["__interrupt__"][0].value)

    return result

async def resume_billing_turn(
        pool: asyncpg.Pool,
        checkpointer: AsyncPostgresSaver,
        thread_id: str,
        decision_status: str,
        user_id: int,
) -> dict:
    """
    Resumes a PAUSED billing turn. Called either by a manual script or by
    hitl.dispatch_resume() once the poller confirms a genuinely new
    decision. Does NOT call on_interrupt again — a resume run only ever
    returns __interrupt__ if it hits a SECOND, different interrupt, which
    doesn't happen in this single-approval-per-turn design.
    """

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

# ============================== ACCOUNT ==============================

async def build_account_only_graph(pool: asyncpg.Pool, checkpointer: AsyncPostgresSaver):
    g = StateGraph(GraphState)
    g.add_node("account", make_account_node(pool))
    g.set_entry_point("account")
    g.add_edge("account", END)
    return g.compile(checkpointer=checkpointer)

async def run_account_turn(
        pool: asyncpg.Pool,
        checkpointer: AsyncPostgresSaver,
        thread_id: str,
        user_id: int,
        user_query: str,
) -> dict:
    graph = await build_account_only_graph(pool, checkpointer)
    config = {
        "configurable": {"thread_id": thread_id},
        "tags": ["account", "e2e-manual-run"],
        "metadata": {"user_id": user_id, "thread_id": thread_id},
    }
    result = await graph.ainvoke(
        GraphState(user_query=user_query, user_id=user_id, trace_id=thread_id),
        config=config
    )

    if "__interrupt__" in result:
        await on_interrupt(pool, thread_id, user_id, result["__interrupt__"][0].value)

    return result

async def resume_account_turn(
        pool: asyncpg.Pool,
        checkpointer: AsyncPostgresSaver,
        thread_id: str,
        decision_status: str,
        user_id: int,
) -> dict:
    graph = await build_account_only_graph(pool, checkpointer)
    config = {
        "configurable": {"thread_id": thread_id},
        "tags": ["account", "e2e-manual-run"],
        "metadata": {"user_id": user_id, "thread_id": thread_id},
    }
    result = await graph.ainvoke(
        Command(resume={"status": decision_status}),
        config=config,
    )
    return result