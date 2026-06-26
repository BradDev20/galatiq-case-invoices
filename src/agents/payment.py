"""Payment agent logic for the invoice workflow."""

from src.tools import ApprovalDecision, InvoiceData, PaymentResult, mock_payment


def process_payment(invoice: InvoiceData, approval: ApprovalDecision) -> PaymentResult:
    """
    Attempt payment when an invoice has been approved.

    :param invoice: Invoice selected for payment.
    :param approval: Approval decision for the invoice.
    :return: Result of the payment step.
    """

    if not approval.approved:
        return PaymentResult(
            status="not_attempted",
            vendor=invoice.vendor,
            amount=invoice.amount,
            detail=approval.reason,
        )

    try:
        result = mock_payment(invoice.vendor, invoice.amount)
    except Exception as exc:
        return PaymentResult(
            status="failed",
            vendor=invoice.vendor,
            amount=invoice.amount,
            detail=str(exc),
        )

    return PaymentResult(
        status="success",
        vendor=invoice.vendor,
        amount=invoice.amount,
        detail=str(result),
    )
