import json

import pytest

from src.agents import approval
from src.tools import ApprovalDecision, InvoiceData, ValidationDateFacts, ValidationIssue, ValidationResult
from tests.conftest import FakeLLM


def test_approval_rejects_hard_validation_errors_without_llm(
    sample_invoice: InvoiceData,
    blocking_validation: ValidationResult,
) -> None:
    decision = approval.approve_invoice(sample_invoice, blocking_validation)

    assert decision.approved is False
    assert "validation found hard errors" in decision.reason
    assert "Validation errors are hard approval blockers" in decision.critique


def test_approval_rejects_invoice_over_threshold_without_llm(
    clean_validation: ValidationResult,
) -> None:
    invoice = InvoiceData(
        invoice_id="INV-BIG",
        vendor="Widgets Inc.",
        amount=15000.0,
        items=[],
        due_date="2026-02-01",
    )

    decision = approval.approve_invoice(invoice, clean_validation)

    assert decision.approved is False
    assert decision.requires_scrutiny is True
    assert "exceeds the approval threshold" in decision.reason
    assert "approval threshold require human review" in decision.critique


def test_approval_runs_decision_and_critique_passes_under_threshold(
    monkeypatch: pytest.MonkeyPatch,
    clean_validation: ValidationResult,
) -> None:
    invoice = InvoiceData(
        invoice_id="INV-MID",
        vendor="Widgets Inc.",
        amount=5000.0,
        items=[],
        due_date="2026-02-01",
    )
    fake_llm = FakeLLM(
        structured_results=[
            ApprovalDecision(
                approved=True,
                reason="Looks acceptable pending scrutiny.",
                requires_scrutiny=False,
                critique="Initial decision.",
            ),
            ApprovalDecision(
                approved=False,
                reason="Critique found concentration risk.",
                requires_scrutiny=False,
                critique="Amount exceeds policy comfort.",
            ),
        ]
    )
    monkeypatch.setattr(approval, "get_grok_api", lambda: fake_llm)

    decision = approval.approve_invoice(invoice, clean_validation)

    assert decision.approved is False
    assert decision.reason == "Critique found concentration risk."
    assert decision.requires_scrutiny is False


def test_approval_falls_back_when_llm_returns_invalid_content(
    monkeypatch: pytest.MonkeyPatch,
    sample_invoice: InvoiceData,
    clean_validation: ValidationResult,
) -> None:
    fake_llm = FakeLLM(
        structured_results=[TypeError("structured output unavailable"), TypeError("critique unavailable")],
        invoke_results=["not json", "still not json"],
    )
    monkeypatch.setattr(approval, "get_grok_api", lambda: fake_llm)

    decision = approval.approve_invoice(sample_invoice, clean_validation)

    assert decision.approved is False
    assert decision.reason == "The approval review did not return a valid structured decision."
    assert decision.critique == "not json"


def test_approval_falls_back_when_initial_raw_json_is_schema_mismatched(
    monkeypatch: pytest.MonkeyPatch,
    sample_invoice: InvoiceData,
    clean_validation: ValidationResult,
) -> None:
    fake_llm = FakeLLM(
        structured_results=[TypeError("structured output unavailable"), TypeError("critique unavailable")],
        invoke_results=[
            '{"approved": "yes", "reason": 123, "requires_scrutiny": "no"}',
            "still not json",
        ],
    )
    monkeypatch.setattr(approval, "get_grok_api", lambda: fake_llm)

    decision = approval.approve_invoice(sample_invoice, clean_validation)

    assert decision.approved is False
    assert decision.reason == "The approval review did not return a valid structured decision."
    assert decision.critique == '{"approved": "yes", "reason": 123, "requires_scrutiny": "no"}'


def test_approval_preserves_initial_decision_when_critique_json_is_schema_mismatched(
    monkeypatch: pytest.MonkeyPatch,
    sample_invoice: InvoiceData,
    clean_validation: ValidationResult,
    make_approval_decision,
) -> None:
    fake_llm = FakeLLM(
        structured_results=[
            make_approval_decision(approved=True, reason="Initial approval stands.", critique="Initial review."),
            TypeError("critique structured output unavailable"),
        ],
        invoke_results=['{"approved": "nope", "reason": 7, "requires_scrutiny": "high"}'],
    )
    monkeypatch.setattr(approval, "get_grok_api", lambda: fake_llm)

    decision = approval.approve_invoice(sample_invoice, clean_validation)

    assert decision.approved is True
    assert decision.reason == "Initial approval stands."
    assert decision.critique == "Initial review."


def test_approval_messages_include_authoritative_date_facts(clean_validation: ValidationResult) -> None:
    invoice = InvoiceData(
        invoice_id="INV-DATE",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[],
        invoice_date="2026-01-24",
        due_date="2026-03-24",
    )
    clean_validation.date_facts = ValidationDateFacts(
        current_date="2026-06-25",
        invoice_date="2026-01-24",
        due_date="2026-03-24",
        invoice_date_valid=True,
        due_date_valid=True,
        invoice_date_in_future=False,
        due_date_in_future=False,
        due_date_before_invoice_date=False,
    )

    messages = approval._approval_messages(invoice, clean_validation)

    assert "Do not contradict validation.date_facts" in messages[0].content
    payload = json.loads(messages[1].content)
    assert payload["validation"]["date_facts"]["current_date"] == "2026-06-25"
    assert payload["validation"]["date_facts"]["due_date_in_future"] is False


def test_approval_rejects_new_deterministic_validation_errors(
    sample_invoice: InvoiceData,
) -> None:
    validation_result = ValidationResult(
        passed=False,
        issues=[
            ValidationIssue(
                code="aggregate_quantity_exceeds_stock",
                message="Requested 22 total units of 'WidgetA' across the invoice, but only 15 are available.",
                severity="error",
                item_name="WidgetA",
            ),
            ValidationIssue(
                code="invoice_total_mismatch",
                message="Invoice amount 22562.80 does not match summed line totals of 21040.00.",
                severity="error",
            ),
        ],
        explanation="Deterministic validation failed.",
    )

    decision = approval.approve_invoice(sample_invoice, validation_result)

    assert decision.approved is False
    assert "aggregate_quantity_exceeds_stock" not in decision.reason
    assert "Requested 22 total units of 'WidgetA'" in decision.reason
    assert "Invoice amount 22562.80 does not match summed line totals of 21040.00." in decision.reason
