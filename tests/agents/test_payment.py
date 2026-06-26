from src.agents import payment
from src.tools import ApprovalDecision, InvoiceData, Item


def test_payment_is_not_attempted_for_rejected_invoice() -> None:
    invoice = InvoiceData(
        invoice_id="INV-STOP",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    approval = ApprovalDecision(approved=False, reason="Validation failed.")

    result = payment.process_payment(invoice, approval)

    assert result.status == "not_attempted"
    assert result.detail == "Validation failed."


def test_payment_returns_success(monkeypatch) -> None:
    invoice = InvoiceData(
        invoice_id="INV-PAY",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    approval = ApprovalDecision(approved=True, reason="Approved.")
    monkeypatch.setattr(payment, "mock_payment", lambda vendor, amount: {"status": "success", "vendor": vendor, "amount": amount})

    result = payment.process_payment(invoice, approval)

    assert result.status == "success"
    assert "success" in result.detail


def test_payment_returns_failed_when_mock_payment_raises(monkeypatch) -> None:
    invoice = InvoiceData(
        invoice_id="INV-PAY",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
    )
    approval = ApprovalDecision(approved=True, reason="Approved.")

    def fail_payment(_vendor: str, _amount: float) -> dict:
        raise RuntimeError("banking API offline")

    monkeypatch.setattr(payment, "mock_payment", fail_payment)

    result = payment.process_payment(invoice, approval)

    assert result.status == "failed"
    assert result.detail == "banking API offline"
