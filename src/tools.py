"""Shared models and utility helpers for invoice processing."""

from pathlib import Path
import sqlite3
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

class Item(BaseModel):
    """Represents one line item on an invoice."""

    name: str
    quantity: int
    unit_price: Optional[float] = None
    line_total: Optional[float] = None

class InvoiceData(BaseModel):
    """Stores normalized invoice data after ingestion."""

    invoice_id: str
    vendor: str
    amount: float
    items: List[Item]
    subtotal: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    due_date: Optional[str] = None
    invoice_date: Optional[str] = None
    currency: str = "USD"
    source_path: Optional[str] = None
    extraction_confidence: Optional[float] = None
    extraction_warnings: List[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    """Describes one issue found during invoice validation."""

    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "error"
    item_name: Optional[str] = None


class ValidationDateFacts(BaseModel):
    """Tracks parsed date facts used during validation."""

    current_date: str
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    invoice_date_valid: bool = False
    due_date_valid: bool = False
    invoice_date_in_future: bool = False
    due_date_in_future: bool = False
    due_date_before_invoice_date: bool = False


class ValidationResult(BaseModel):
    """Summarizes whether an invoice passed validation."""

    passed: bool
    issues: List[ValidationIssue] = Field(default_factory=list)
    explanation: Optional[str] = None
    suggested_corrections: List[str] = Field(default_factory=list)
    date_facts: Optional[ValidationDateFacts] = None


class ApprovalDecision(BaseModel):
    """Captures the final approval decision for an invoice."""

    approved: bool
    reason: str
    requires_scrutiny: bool = False
    critique: Optional[str] = None


class PaymentResult(BaseModel):
    """Reports the outcome of the payment step."""

    status: Literal["not_attempted", "success", "failed"]
    vendor: Optional[str] = None
    amount: Optional[float] = None
    detail: Optional[str] = None


class WorkflowStep(BaseModel):
    """Records one routed step in the workflow."""

    agent: Literal["overseer", "ingestion", "validation", "approval", "payment"]
    next: Literal["ingest", "validate", "approve", "pay", "reject", "complete", "error"]
    reason: str
    status: Literal["pending", "completed", "skipped", "failed"] = "completed"


class LogEntry(BaseModel):
    """Stores a timestamped workflow log entry."""

    timestamp: str
    agent: Literal["overseer", "ingestion", "validation", "approval", "payment", "system"]
    event: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class FinalReport(BaseModel):
    """Represents the final summary produced by the workflow."""

    outcome: Literal["approved_paid", "rejected", "payment_failed", "error"]
    summary: str
    risk_flags: List[str] = Field(default_factory=list)
    next_action: str


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_DB = PROJECT_ROOT / "inventory.db"

def get_inventory():
    """Open a connection to the inventory database.

    :return: SQLite connection for the inventory database.
    """

    return sqlite3.connect(INVENTORY_DB)

def mock_payment(vendor: str, amount: float):
    """Simulate paying a vendor for the requested amount.

    :param vendor: Vendor receiving the payment.
    :param amount: Amount to pay.
    :return: Mock payment result data.
    """

    print(f"Paid ${amount} to {vendor}")
    return {"status": "success"}
