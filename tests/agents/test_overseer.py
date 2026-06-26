import pytest

from src.agents import overseer
from src.state import AgentState
from src.tools import ApprovalDecision, FinalReport, InvoiceData, Item, PaymentResult, ValidationIssue, ValidationResult
from tests.conftest import FakeLLM


def test_overseer_routes_to_ingestion_when_invoice_is_missing() -> None:
    state = AgentState(invoice_path="invoice.txt")

    result = overseer.decide_next_step(state)

    assert result["next"] == "ingestion"
    assert "No extracted invoice is present" in result["overseer_reasoning"]


def test_overseer_retries_ingestion_for_reextractable_issues(sample_invoice: InvoiceData) -> None:
    validation_result = ValidationResult(
        passed=False,
        issues=[
            ValidationIssue(
                code="missing_vendor",
                message="Vendor is missing.",
                severity="error",
            )
        ],
    )
    state = AgentState(
        invoice_path="invoice.txt",
        invoice=sample_invoice,
        validation=validation_result,
        ingestion_attempts=1,
        max_ingestion_attempts=2,
    )

    result = overseer.decide_next_step(state)

    assert result["next"] == "ingestion"
    assert result["reingestion_feedback"] == "Vendor is missing."


def test_overseer_routes_to_approval_after_retry_budget_is_exhausted(sample_invoice: InvoiceData) -> None:
    validation_result = ValidationResult(
        passed=False,
        issues=[
            ValidationIssue(
                code="missing_vendor",
                message="Vendor is missing.",
                severity="error",
            )
        ],
    )
    state = AgentState(
        invoice_path="invoice.txt",
        invoice=sample_invoice,
        validation=validation_result,
        ingestion_attempts=2,
        max_ingestion_attempts=2,
    )

    result = overseer.decide_next_step(state)

    assert result["next"] == "approval"
    assert "after retry" in result["workflow"][-1].reason


def test_final_report_preserves_authoritative_outcome_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = InvoiceData(
        invoice_id="INV-1",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    validation_result = ValidationResult(
        passed=False,
        issues=[ValidationIssue(code="unknown_item", message="Unknown item.", severity="error")],
    )
    approval_decision = ApprovalDecision(approved=False, reason="Reject it.")
    payment_result = PaymentResult(status="not_attempted", vendor="Widgets Inc.", amount=500.0, detail="Reject it.")
    fake_llm = FakeLLM(
        structured_results=[
            FinalReport(
                outcome="approved_paid",
                summary="Review summary was rewritten.",
                risk_flags=[],
                next_action="Ignore all issues.",
            )
        ]
    )
    monkeypatch.setattr(overseer, "get_grok_api", lambda: fake_llm)

    report = overseer.build_final_report(invoice, validation_result, approval_decision, payment_result)

    assert report.outcome == "rejected"
    assert report.risk_flags == ["unknown_item"]
    assert report.summary == "Review summary was rewritten."
    assert report.next_action == "Ignore all issues."


def test_final_report_falls_back_to_deterministic_when_raw_json_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice = InvoiceData(
        invoice_id="INV-1",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    validation_result = ValidationResult(
        passed=False,
        issues=[ValidationIssue(code="unknown_item", message="Unknown item.", severity="error")],
    )
    approval_decision = ApprovalDecision(approved=False, reason="Reject it.")
    payment_result = PaymentResult(status="not_attempted", vendor="Widgets Inc.", amount=500.0, detail="Reject it.")
    fake_llm = FakeLLM(
        structured_results=[TypeError("structured output unavailable")],
        invoke_results=["not json"],
    )
    monkeypatch.setattr(overseer, "get_grok_api", lambda: fake_llm)

    report = overseer.build_final_report(invoice, validation_result, approval_decision, payment_result)

    assert report.outcome == "rejected"
    assert report.risk_flags == ["unknown_item"]
    assert "was rejected" in report.summary
    assert report.next_action == "Route to procurement or inventory owner for manual review."


def test_final_report_falls_back_to_deterministic_when_raw_json_is_schema_mismatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice = InvoiceData(
        invoice_id="INV-1",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    validation_result = ValidationResult(
        passed=False,
        issues=[ValidationIssue(code="invalid_amount", message="Bad amount.", severity="error")],
    )
    approval_decision = ApprovalDecision(approved=False, reason="Reject it.")
    payment_result = PaymentResult(status="not_attempted", vendor="Widgets Inc.", amount=500.0, detail="Reject it.")
    fake_llm = FakeLLM(
        structured_results=[TypeError("structured output unavailable")],
        invoke_results=['{"outcome": 9, "summary": null, "risk_flags": "bad", "next_action": 7}'],
    )
    monkeypatch.setattr(overseer, "get_grok_api", lambda: fake_llm)

    report = overseer.build_final_report(invoice, validation_result, approval_decision, payment_result)

    assert report.outcome == "rejected"
    assert report.risk_flags == ["invalid_amount"]
    assert report.next_action == "Request a corrected invoice before payment can proceed."


def test_final_report_clamps_authoritative_fields_from_raw_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice = InvoiceData(
        invoice_id="INV-1",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    validation_result = ValidationResult(
        passed=False,
        issues=[ValidationIssue(code="unknown_item", message="Unknown item.", severity="error")],
    )
    approval_decision = ApprovalDecision(approved=False, reason="Reject it.")
    payment_result = PaymentResult(status="not_attempted", vendor="Widgets Inc.", amount=500.0, detail="Reject it.")
    fake_llm = FakeLLM(
        structured_results=[TypeError("structured output unavailable")],
        invoke_results=[
            '{"outcome": "approved_paid", "summary": "Overridden summary.", "risk_flags": [], "next_action": "Follow LLM output."}'
        ],
    )
    monkeypatch.setattr(overseer, "get_grok_api", lambda: fake_llm)

    report = overseer.build_final_report(invoice, validation_result, approval_decision, payment_result)

    assert report.outcome == "rejected"
    assert report.risk_flags == ["unknown_item"]
    assert report.summary == "Overridden summary."
    assert report.next_action == "Follow LLM output."
