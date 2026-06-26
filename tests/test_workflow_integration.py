import pytest

from src import graph as graph_module
from src.tools import ApprovalDecision, FinalReport, InvoiceData, Item, PaymentResult, ValidationIssue, ValidationResult


def test_workflow_integration_approved_then_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = InvoiceData(
        invoice_id="INV-SUCCESS",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
        due_date="2026-02-01",
    )
    monkeypatch.setattr(graph_module, "ingest_invoice", lambda path, feedback=None: invoice)
    monkeypatch.setattr(
        graph_module,
        "validate_invoice",
        lambda _invoice: ValidationResult(passed=True, issues=[], explanation="ok"),
    )
    monkeypatch.setattr(
        graph_module,
        "approve_invoice",
        lambda _invoice, _validation: ApprovalDecision(approved=True, reason="Approved.", critique="ok"),
    )
    monkeypatch.setattr(
        graph_module,
        "process_payment",
        lambda _invoice, _approval: PaymentResult(status="success", vendor="Widgets Inc.", amount=500.0, detail="paid"),
    )
    monkeypatch.setattr(
        graph_module,
        "build_final_report",
        lambda **_kwargs: FinalReport(
            outcome="approved_paid",
            summary="Invoice approved and paid.",
            risk_flags=[],
            next_action="No further action required.",
        ),
    )

    workflow_graph = graph_module.build_graph()
    result = workflow_graph.invoke(
        {"invoice_path": "data/invoices/invoice_1001.txt"},
        config={"configurable": {"thread_id": "success"}},
    )

    steps = [(step.agent, step.next) for step in result["workflow"]]
    assert result["next"] == "end"
    assert result["payment"].status == "success"
    assert result["final_report"].outcome == "approved_paid"
    assert "workflow finalized" in result["history"]
    assert steps == [
        ("overseer", "ingest"),
        ("ingestion", "validate"),
        ("overseer", "validate"),
        ("validation", "approve"),
        ("approval", "pay"),
        ("overseer", "pay"),
        ("payment", "complete"),
        ("overseer", "complete"),
    ]


def test_workflow_integration_rejected_skips_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = InvoiceData(
        invoice_id="INV-REJECT",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
        due_date="2026-02-01",
    )
    monkeypatch.setattr(graph_module, "ingest_invoice", lambda path, feedback=None: invoice)
    monkeypatch.setattr(
        graph_module,
        "validate_invoice",
        lambda _invoice: ValidationResult(passed=True, issues=[], explanation="ok"),
    )
    monkeypatch.setattr(
        graph_module,
        "approve_invoice",
        lambda _invoice, _validation: ApprovalDecision(approved=False, reason="Rejected.", critique="no"),
    )
    monkeypatch.setattr(
        graph_module,
        "build_final_report",
        lambda **_kwargs: FinalReport(
            outcome="rejected",
            summary="Invoice rejected.",
            risk_flags=["unknown_item"],
            next_action="Manual review.",
        ),
    )

    workflow_graph = graph_module.build_graph()
    result = workflow_graph.invoke(
        {"invoice_path": "data/invoices/invoice_1001.txt"},
        config={"configurable": {"thread_id": "rejected"}},
    )

    steps = [(step.agent, step.next) for step in result["workflow"]]
    assert result["next"] == "end"
    assert result["payment"].status == "not_attempted"
    assert result["final_report"].outcome == "rejected"
    assert steps[-3:] == [
        ("overseer", "complete"),
        ("payment", "complete"),
        ("overseer", "complete"),
    ]


def test_workflow_integration_payment_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = InvoiceData(
        invoice_id="INV-FAIL",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
        due_date="2026-02-01",
    )
    monkeypatch.setattr(graph_module, "ingest_invoice", lambda path, feedback=None: invoice)
    monkeypatch.setattr(
        graph_module,
        "validate_invoice",
        lambda _invoice: ValidationResult(passed=True, issues=[], explanation="ok"),
    )
    monkeypatch.setattr(
        graph_module,
        "approve_invoice",
        lambda _invoice, _validation: ApprovalDecision(approved=True, reason="Approved.", critique="ok"),
    )
    monkeypatch.setattr(
        graph_module,
        "process_payment",
        lambda _invoice, _approval: PaymentResult(status="failed", vendor="Widgets Inc.", amount=500.0, detail="bank offline"),
    )
    monkeypatch.setattr(
        graph_module,
        "build_final_report",
        lambda **_kwargs: FinalReport(
            outcome="payment_failed",
            summary="Payment failed.",
            risk_flags=[],
            next_action="Retry payment.",
        ),
    )

    workflow_graph = graph_module.build_graph()
    result = workflow_graph.invoke(
        {"invoice_path": "data/invoices/invoice_1001.txt"},
        config={"configurable": {"thread_id": "failed"}},
    )

    assert result["next"] == "end"
    assert result["payment"].status == "failed"
    assert result["final_report"].outcome == "payment_failed"
    assert result["workflow"][-2].status == "failed"


def test_workflow_integration_reingests_once_before_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    feedbacks: list[str | None] = []
    validation_results = [
        ValidationResult(
            passed=False,
            issues=[ValidationIssue(code="missing_vendor", message="Vendor is missing.", severity="error")],
            explanation="Missing vendor.",
        ),
        ValidationResult(passed=True, issues=[], explanation="ok"),
    ]
    invoices = [
        InvoiceData(
            invoice_id="INV-RETRY",
            vendor="",
            amount=500.0,
            items=[Item(name="WidgetA", quantity=1)],
            due_date="2026-02-01",
        ),
        InvoiceData(
            invoice_id="INV-RETRY",
            vendor="Widgets Inc.",
            amount=500.0,
            items=[Item(name="WidgetA", quantity=1)],
            due_date="2026-02-01",
        ),
    ]

    def fake_ingest(_path, feedback=None):
        feedbacks.append(feedback)
        return invoices.pop(0)

    monkeypatch.setattr(graph_module, "ingest_invoice", fake_ingest)
    monkeypatch.setattr(graph_module, "validate_invoice", lambda _invoice: validation_results.pop(0))
    monkeypatch.setattr(
        graph_module,
        "approve_invoice",
        lambda _invoice, _validation: ApprovalDecision(approved=True, reason="Approved.", critique="ok"),
    )
    monkeypatch.setattr(
        graph_module,
        "process_payment",
        lambda _invoice, _approval: PaymentResult(status="success", vendor="Widgets Inc.", amount=500.0, detail="paid"),
    )
    monkeypatch.setattr(
        graph_module,
        "build_final_report",
        lambda **_kwargs: FinalReport(
            outcome="approved_paid",
            summary="Invoice approved and paid.",
            risk_flags=[],
            next_action="No further action required.",
        ),
    )

    workflow_graph = graph_module.build_graph()
    result = workflow_graph.invoke(
        {"invoice_path": "data/invoices/invoice_1001.txt"},
        config={"configurable": {"thread_id": "retry"}},
    )

    assert result["next"] == "end"
    assert result["ingestion_attempts"] == 2
    assert feedbacks == [None, "Vendor is missing."]
    assert "overseer routed to re-ingestion" in result["history"]
    assert any(
        step.next == "ingest" and "retrying ingestion" in step.reason
        for step in result["workflow"]
    )
