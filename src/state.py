"""State models used by the LangGraph invoice workflow."""

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from src.tools import (
    ApprovalDecision,
    FinalReport,
    InvoiceData,
    LogEntry,
    PaymentResult,
    ValidationResult,
    WorkflowStep,
)


class AgentState(BaseModel):
    """Shared state for the LangGraph invoice workflow."""

    invoice_path: str

    invoice: Optional[InvoiceData] = None
    validation: Optional[ValidationResult] = None
    approval: Optional[ApprovalDecision] = None
    payment: Optional[PaymentResult] = None
    workflow: List[WorkflowStep] = Field(default_factory=list)
    logs: List[LogEntry] = Field(default_factory=list)
    final_report: Optional[FinalReport] = None

    next: Optional[str] = None
    ingestion_attempts: int = 0
    max_ingestion_attempts: int = 2
    reingestion_feedback: Optional[str] = None
    overseer_reasoning: Optional[str] = None

    history: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
