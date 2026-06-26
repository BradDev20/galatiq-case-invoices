import pytest

from src import graph as graph_module
from src.state import AgentState
from src.tools import ApprovalDecision, FinalReport, InvoiceData, Item, PaymentResult, ValidationResult


def test_ingestion_node_initializes_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = InvoiceData(
        invoice_id="INV-1",
        vendor="Widgets Inc.",
        amount=100.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    monkeypatch.setattr(graph_module, "ingest_invoice", lambda path, feedback=None: invoice)
    state = AgentState(invoice_path="data/invoices/invoice_1001.txt")

    result = graph_module.ingestion_node(state)

    assert result["invoice"] == invoice
    assert result["ingestion_attempts"] == 1
    assert result["next"] == "overseer"
    assert len(result["workflow"]) == 2


def test_finalize_node_marks_rejected_invoice_as_skipped_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = InvoiceData(
        invoice_id="INV-1",
        vendor="Widgets Inc.",
        amount=100.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    state = AgentState(
        invoice_path="invoice.txt",
        invoice=invoice,
        validation=ValidationResult(passed=True, issues=[]),
        approval=ApprovalDecision(approved=False, reason="Rejected."),
    )
    monkeypatch.setattr(
        graph_module,
        "build_final_report",
        lambda invoice, validation, approval, payment: FinalReport(
            outcome="rejected",
            summary="Rejected invoice.",
            risk_flags=[],
            next_action="Notify accounts payable of the rejection reason.",
        ),
    )

    result = graph_module.finalize_node(state)

    assert result["payment"] == PaymentResult(
        status="not_attempted",
        vendor="Widgets Inc.",
        amount=100.0,
        detail="Rejected.",
    )
    assert result["next"] == "end"
    assert result["workflow"][-2].agent == "payment"
    assert result["workflow"][-2].reason == "Payment was skipped because the invoice was not approved."
