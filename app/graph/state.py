from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

def merge_dicts(current: dict, update: dict) -> dict:
    """Shallow-merge two dicts. Used for agent_outputs / pending_approvals,
    which are keyed by agent_name — each parallel agent only ever writes
    its own key, so a shallow merge never loses data across branches."""
    return {**current, **update}

def merge_unique_list(current: list[str], update: list[str]) -> list[str]:
    """Append, de-duplicated. Used for agents_completed — each agent
    reports itself done exactly once; dedup guards against a node
    accidentally running twice (e.g. a retried Send dispatch) double-
    counting completion."""
    combined = current + update
    seen = set()
    out = []
    for item in combined:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

class AgentOutput(BaseModel):
    agent_name: str
    summnary: str = Field(..., description="Natural-language summary for synthesis")
    structured_data: dict = Field(default_factory=dict)
    requires_approval: bool = False
    proposed_action: Optional[dict] = None
    confidence: float = Field(..., ge=0.0, le=1.0)

class ApprovalRequest(BaseModel):
    action_type: Literal["refund", "account_closure", "payment_method_change"]
    agent: str
    details: dict
    risk_level: Literal["low","medium","high"]
    status: Literal["pending", "approved", "rejected", "auto_approved", "timed_out"] = "pending"

class IntentClassification(BaseModel):
    """Classifier node output"""
    intents: list[Literal["billing", "technical", "account", "escalation"]]
    confidence: float = Field(..., ge=0.0, le=1.0)
    sentiment = Literal["neutral", "frustrated", "angry"]
    reasoning: str

class GraphState(BaseModel):
    messages: Annotated[list[BaseMessage], add_messages] = Field(default_factory=list)
    user_query: str = ""

    intents: list[Literal["billing", "technical", "account", "escalation"]] = Field(default_factory=list)
    routing_confidence: float = 0.0
    sentiment: Literal["neutral", "frustrated", "angry"] = "netrual"

    agent_outputs: Annotated[dict[str, AgentOutput], merge_dicts] = Field(default_factory=dict)
    agent_to_run: list[str] = Field(default_factory=list)
    agents_completed: Annotated[list[str], merge_unique_list] = Field(default_factory=list)

    pending_approvals: Annotated[dict[str, ApprovalRequest], merge_dicts] = Field(default_factory=dict)

    final_response: Optional[str] = None

    trace_id: str = ""
    user_id: int = 0

    turn_count: int = 0


    model_config = {"arbitrary_types_allowed": True}
