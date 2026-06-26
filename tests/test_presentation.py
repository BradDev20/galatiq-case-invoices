from src.presentation import (
    describe_workflow_error,
    format_log_line,
    format_outcome_label,
    format_risk_flag,
    outcome_tone,
    stage_overview,
    summarize_validation_issues,
)
from src.state import AgentState
from src.tools import (
    ApprovalDecision,
    FinalReport,
    InvoiceData,
    Item,
    LogEntry,
    PaymentResult,
    ValidationIssue,
    ValidationResult,
    WorkflowStep,
)


def test_presentation_helpers_format_approved_paid_state() -> None:
    state = AgentState(
        invoice_path="invoice.txt",
        invoice=InvoiceData(
            invoice_id="INV-1",
            vendor="Widgets Inc.",
            amount=100.0,
            items=[Item(name="WidgetA", quantity=1)],
        ),
        validation=ValidationResult(passed=True, issues=[], explanation="Validation passed."),
        approval=ApprovalDecision(approved=True, reason="Approved."),
        payment=PaymentResult(status="success", vendor="Widgets Inc.", amount=100.0, detail="Paid."),
        final_report=FinalReport(
            outcome="approved_paid",
            summary="Invoice approved and paid.",
            risk_flags=[],
            next_action="No further action required.",
        ),
        workflow=[
            WorkflowStep(agent="ingestion", next="validate", reason="Ingested."),
            WorkflowStep(agent="approval", next="pay", reason="Approved."),
            WorkflowStep(agent="payment", next="complete", reason="Paid."),
        ],
        logs=[
            LogEntry(
                timestamp="2026-01-15T12:00:00",
                agent="payment",
                event="payment_processed",
                message="Payment completed successfully.",
            )
        ],
    )

    assert format_outcome_label(state.final_report.outcome) == "Approved and paid"
    assert outcome_tone(state.final_report.outcome) == "success"
    assert summarize_validation_issues(state) == []
    assert stage_overview(state)[-1]["status"] == "completed"
    assert "payment/payment_processed" in format_log_line(state.logs[0])


def test_presentation_helpers_format_rejected_state() -> None:
    state = AgentState(
        invoice_path="invoice.txt",
        invoice=InvoiceData(
            invoice_id="INV-2",
            vendor="Widgets Inc.",
            amount=100.0,
            items=[Item(name="WidgetA", quantity=1)],
        ),
        validation=ValidationResult(
            passed=False,
            issues=[ValidationIssue(code="unknown_item", message="Unknown item found.", severity="error")],
            explanation="Validation failed.",
        ),
        approval=ApprovalDecision(approved=False, reason="Rejected due to validation issues."),
        final_report=FinalReport(
            outcome="rejected",
            summary="Invoice rejected.",
            risk_flags=["unknown_item"],
            next_action="Request a corrected invoice.",
        ),
    )

    assert summarize_validation_issues(state) == ["Unknown item found."]
    assert format_risk_flag("unknown_item") == "Unknown Item"
    payment_stage = stage_overview(state)[-1]
    assert payment_stage["status"] == "skipped"
    assert "not approved" in payment_stage["reason"]


def test_presentation_helpers_format_payment_failure_state() -> None:
    state = AgentState(
        invoice_path="invoice.txt",
        invoice=InvoiceData(
            invoice_id="INV-3",
            vendor="Widgets Inc.",
            amount=100.0,
            items=[Item(name="WidgetA", quantity=1)],
        ),
        validation=ValidationResult(passed=True, issues=[], explanation="Validation passed."),
        approval=ApprovalDecision(approved=True, reason="Approved."),
        payment=PaymentResult(status="failed", vendor="Widgets Inc.", amount=100.0, detail="Bank offline."),
        final_report=FinalReport(
            outcome="payment_failed",
            summary="Payment failed.",
            risk_flags=[],
            next_action="Retry payment.",
        ),
        workflow=[
            WorkflowStep(agent="payment", next="complete", reason="Payment was attempted but failed.", status="failed"),
        ],
    )

    assert format_outcome_label(state.final_report.outcome) == "Payment failed"
    assert outcome_tone(state.final_report.outcome) == "error"
    assert stage_overview(state)[-1]["status"] == "failed"


def test_describe_workflow_error_returns_business_friendly_messages() -> None:
    title, message = describe_workflow_error(RuntimeError("XAI_API_KEY is not set in this environment"))
    assert title == "Missing API Configuration"
    assert "required service API key" in message
