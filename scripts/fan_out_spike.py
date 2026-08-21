"""
THROWAWAY SPIKE — not part of the real app, same spirit as interrupt_spike.py.
 
Goal: prove Send()-based fan-out actually merges parallel writes correctly
via the custom reducers in app/graph/state.py (merge_dicts, merge_unique_list),
with NOTHING else in the way — no LLM call, no DB, no real agent logic —
so if something breaks here, it's provably the graph/reducer mechanics and
not classifier quality or agent bugs.
 
WHY THIS MATTERS (see state.py's own docstring): the default merge for a
field is last-write-wins. If two parallel branches both wrote
`state.agent_outputs = {...}` without a reducer, one would silently
overwrite the other — an entire agent's output vanishes with no error.
That's the same class of "invisible unless you specifically design for it"
bug as the interrupt() replay issue. This spike exists to catch it now,
once, in isolation, rather than discover it three agents deep.
 
Uses the REAL GraphState/AgentOutput/ApprovalRequest from app/graph/state.py
— faking those would defeat the point, since the reducers themselves are
what's under test. Everything else (the "classifier" node, the two agent
nodes) is fake/hardcoded.
 
No checkpointer needed here — unlike interrupt_spike.py, nothing pauses,
so there's nothing to persist/resume. Keeping that out is deliberate:
fewer moving parts than necessary would obscure whether a failure is
fan-out/reducer behavior or checkpointer behavior.
 
Run: python scripts/fan_out_spike.py
"""
 
import asyncio
import os
import sys
 
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
 
from langgraph.graph import StateGraph, END
from langgraph.types import Send
 
from app.graph.state import GraphState, AgentOutput, ApprovalRequest
 
 
# --- fake classifier: hardcoded, no LLM ---
def fake_classify_node(state: GraphState) -> dict:
    print("[classify] hardcoded fan-out to dummy_a + dummy_b")
    return {"agents_to_run": ["dummy_a", "dummy_b"]}
 
 
def route_to_agents(state: GraphState):
    # mirrors design-doc.md 3b's route_to_agents — LangGraph dispatches
    # via Send() to each node in agents_to_run, in parallel.
    return [Send(name, state) for name in state.agents_to_run]
 
 
# --- dummy agent A: fast, writes only agent_outputs + agents_completed ---
async def dummy_a_node(state: GraphState) -> dict:
    print("[dummy_a] starting (fast)")
    await asyncio.sleep(0.1)
    print("[dummy_a] done")
    return {
        "agent_outputs": {
            "dummy_a": AgentOutput(
                agent_name="dummy_a",
                summary="dummy_a ran",
                confidence=1.0,
            )
        },
        "agents_completed": ["dummy_a"],
    }
 
 
# --- dummy agent B: slow, ALSO writes a pending_approvals entry, so this
# spike checks a second field's reducer (merge_dicts) in the same run,
# and that a branch which writes fewer keys than the other doesn't cause
# a KeyError or clobber anything ---
async def dummy_b_node(state: GraphState) -> dict:
    print("[dummy_b] starting (slow)")
    await asyncio.sleep(0.5)
    print("[dummy_b] done")
    return {
        "agent_outputs": {
            "dummy_b": AgentOutput(
                agent_name="dummy_b",
                summary="dummy_b ran",
                confidence=1.0,
                requires_approval=True,
            )
        },
        "agents_completed": ["dummy_b"],
        "pending_approvals": {
            "dummy_b": ApprovalRequest(
                action_type="refund",
                agent="dummy_b",
                details={"note": "fake approval, spike only"},
                risk_level="low",
                status="pending",
            )
        },
    }
 
 
# --- join node: both branches land here before END, so the merged state
# is inspectable in one place, same role synthesis plays in the real graph ---
def join_node(state: GraphState) -> dict:
    print("[join] both branches complete, inspecting merged state")
    return {}
 
 
def build_spike_graph():
    g = StateGraph(GraphState)
    g.add_node("classify", fake_classify_node)
    g.add_node("dummy_a", dummy_a_node)
    g.add_node("dummy_b", dummy_b_node)
    g.add_node("join", join_node)
 
    g.set_entry_point("classify")
    g.add_conditional_edges("classify", route_to_agents, ["dummy_a", "dummy_b"])
    g.add_edge("dummy_a", "join")
    g.add_edge("dummy_b", "join")
    g.add_edge("join", END)
 
    return g.compile()
 
 
async def main():
    graph = build_spike_graph()
 
    print("=== FAN-OUT SPIKE: classify -> Send([dummy_a, dummy_b]) -> join ===\n")
    result = await graph.ainvoke(
        GraphState(user_query="fan-out spike", user_id=1, trace_id="fanout-spike-1")
    )
 
    print(f"\n--- final merged state ---")
    print(f"agent_outputs keys: {list(result['agent_outputs'].keys())}")
    print(f"agents_completed:   {result['agents_completed']}")
    print(f"pending_approvals keys: {list(result['pending_approvals'].keys())}")
 
    # --- the actual assertions this spike exists to make ---
    assert "dummy_a" in result["agent_outputs"], "dummy_a's output was lost — merge_dicts failed"
    assert "dummy_b" in result["agent_outputs"], "dummy_b's output was lost — merge_dicts failed"
    assert sorted(result["agents_completed"]) == ["dummy_a", "dummy_b"], (
        f"agents_completed merge wrong: {result['agents_completed']}"
    )
    assert "dummy_b" in result["pending_approvals"], "dummy_b's pending_approvals entry was lost"
    assert "dummy_a" not in result["pending_approvals"], (
        "dummy_a never wrote a pending_approvals entry — it appearing here would mean "
        "the reducer is bleeding keys across branches that never wrote them"
    )
 
    print("\n✅ SPIKE PASSED: parallel Send() dispatch + custom reducers merge correctly.")
    print("   Known limitation, not a bug: if two branches wrote the SAME key in")
    print("   agent_outputs, merge_dicts' shallow {**current, **update} would let one")
    print("   silently win — that's fine since each agent should only ever write its")
    print("   own name as key, but it's not a case this spike (or the reducer) guards against.")
 
 
if __name__ == "__main__":
    asyncio.run(main())
