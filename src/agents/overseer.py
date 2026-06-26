"""Overseer agent logic for routing and final reporting."""

import json
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from src.grok import get_grok_api
from src.state import AgentState
from src.tools import (
    ApprovalDecision,
    FinalReport,
    InvoiceData,
    LogEntry,
    PaymentResult,
    ValidationIssue,
    ValidationResult,
    WorkflowStep,
)


MAX_INGESTION_ATTEMPTS = 2
REEXTRACTION_ISSUE_CODES = {
    "missing_invoice_id",
    "missing_vendor",
    "missing_due_date",
    "missing_items",
    "missing_item_name",
}


def decide_next_step(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Choose the next workflow step based on current state.

    :param state: Current workflow state.
    :return: State updates with the selected route.
    """

    current = _state(state)
    workflow = list(current.workflow)
    logs = list(current.logs)

    if current.invoice is None:
        return _route(
            current=current,
            workflow=workflow,
            logs=logs,
            next_step="ingestion",
            reason="No extracted invoice is present; route to ingestion.",
        )

    if current.validation is None:
        return _route(
            current=current,
            workflow=workflow,
            logs=logs,
            next_step="validation",
            reason="Invoice data is available; route to validation.",
        )

    if current.approval is None:
        reextract_issues = reextractable_issues(current.validation)
        if reextract_issues and current.ingestion_attempts < current.max_ingestion_attempts:
            feedback = feedback_from_issues(reextract_issues)
            workflow.append(
                WorkflowStep(
                    agent="overseer",
                    next="ingest",
                    reason=f"Validation found extraction-related issues; retrying ingestion. Feedback: {feedback}",
                )
            )
            logs.append(
                _log_entry(
                    agent="overseer",
                    event="workflow_routed",
                    message="Retry ingestion for extraction-quality issues.",
                    next_step="ingestion",
                    feedback=feedback,
                )
            )
            return {
                "workflow": workflow,
                "logs": logs,
                "next": "ingestion",
                "reingestion_feedback": feedback,
                "overseer_reasoning": "Retry ingestion for extraction-quality issues.",
                "history": current.history + ["overseer routed to re-ingestion"],
            }

        workflow.append(
            WorkflowStep(
                agent="validation",
                next="approve",
                reason=validation_route_reason(
                    validation=current.validation,
                    reextract_issues=reextract_issues,
                    attempt=current.ingestion_attempts,
                    max_attempts=current.max_ingestion_attempts,
                ),
            )
        )
        return {
            "workflow": workflow,
            "logs": logs
            + [
                _log_entry(
                    agent="overseer",
                    event="workflow_routed",
                    message="Validation has completed; route to approval.",
                    next_step="approval",
                    passed=current.validation.passed,
                    issue_count=len(current.validation.issues),
                )
            ],
            "next": "approval",
            "overseer_reasoning": "Validation has completed; route to approval.",
            "history": current.history + ["overseer routed to approval"],
        }

    if current.approval.approved:
        return _route(
            current=current,
            workflow=workflow,
            logs=logs,
            next_step="payment",
            reason="Approval succeeded; route to mock payment.",
        )

    return _route(
        current=current,
        workflow=workflow,
        logs=logs,
        next_step="finalize",
        reason="Approval rejected the invoice; finalize without payment.",
    )


def reextractable_issues(validation: ValidationResult) -> list[ValidationIssue]:
    """
    Return issues that justify trying ingestion again.

    :param validation: Validation result for the invoice.
    :return: Issues that are worth re-extracting.
    """

    return [
        issue
        for issue in validation.issues
        if issue.code in REEXTRACTION_ISSUE_CODES
    ]


def feedback_from_issues(issues: list[ValidationIssue]) -> str:
    """
    Combine issue messages into re-ingestion feedback.

    :param issues: Validation issues to summarize.
    :return: Feedback string for the ingestion agent.
    """

    return "; ".join(issue.message for issue in issues)


def ingestion_route_reason(attempt: int) -> str:
    """
    Explain why the workflow is routing from ingestion.

    :param attempt: Current ingestion attempt number.
    :return: Human-readable routing reason.
    """

    if attempt == 1:
        return "Invoice data was extracted and normalized for validation."
    return "Invoice data was re-extracted with overseer feedback and routed to validation."


def validation_route_reason(
    validation: ValidationResult,
    reextract_issues: list[ValidationIssue],
    attempt: int,
    max_attempts: int,
) -> str:
    """
    Explain why validation routes to the next stage.

    :param validation: Validation result for the invoice.
    :param reextract_issues: Extraction-related issues from validation.
    :param attempt: Current ingestion attempt number.
    :param max_attempts: Maximum allowed ingestion attempts.
    :return: Human-readable routing reason.
    """

    if validation.passed:
        return "Validation passed; route to approval reasoning."
    if reextract_issues and attempt >= max_attempts:
        return "Validation still has extraction-related issues after retry; route to approval for guarded rejection."
    return "Validation found blocking business issues; route to approval for guarded rejection."


def payment_route_reason(status: str) -> str:
    """
    Map payment status values to readable route reasons.

    :param status: Payment status value.
    :return: Human-readable routing reason.
    """

    reasons = {
        "success": "Payment completed successfully.",
        "not_attempted": "Payment was skipped because the invoice was not approved.",
        "failed": "Payment was attempted but failed.",
    }
    return reasons.get(status, "Payment stage completed with an unrecognized status.")


def build_final_report(
    invoice: InvoiceData,
    validation: ValidationResult,
    approval: ApprovalDecision,
    payment: PaymentResult,
) -> FinalReport:
    """
    Build the final workflow report for the invoice.

    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :param approval: Final approval decision.
    :param payment: Payment result for the invoice.
    :return: Final workflow report.
    """

    deterministic_report = _deterministic_final_report(invoice, validation, approval, payment)
    return _grok_final_report(
        invoice=invoice,
        validation=validation,
        approval=approval,
        payment=payment,
        deterministic_report=deterministic_report,
    )


def _state(state: AgentState | dict[str, Any]) -> AgentState:
    """
    Normalize dictionary state into an ``AgentState`` object.

    :param state: State object or raw state dictionary.
    :return: Validated ``AgentState`` instance.
    """

    if isinstance(state, AgentState):
        return state
    return AgentState.model_validate(state)


def _route(
    current: AgentState,
    workflow: list[WorkflowStep],
    logs: list[LogEntry],
    next_step: str,
    reason: str,
) -> dict[str, Any]:
    """
    Record a routing decision and return the state update.

    :param current: Current validated workflow state.
    :param workflow: Workflow history to extend.
    :param logs: Log history to extend.
    :param next_step: Next step to route to.
    :param reason: Human-readable routing reason.
    :return: State updates for the chosen route.
    """

    workflow.append(
        WorkflowStep(
            agent="overseer",
            next=_workflow_next(next_step),
            reason=reason,
        )
    )
    logs.append(
        _log_entry(
            agent="overseer",
            event="workflow_routed",
            message=reason,
            next_step=next_step,
        )
    )
    return {
        "workflow": workflow,
        "logs": logs,
        "next": next_step,
        "overseer_reasoning": reason,
        "history": current.history + [f"overseer routed to {next_step}"],
    }


def _workflow_next(next_step: str) -> str:
    """
    Translate internal route names into workflow labels.

    :param next_step: Internal route name.
    :return: Workflow label for that route.
    """

    mapping = {
        "ingestion": "ingest",
        "validation": "validate",
        "approval": "approve",
        "payment": "pay",
        "finalize": "complete",
        "end": "complete",
    }
    return mapping.get(next_step, "error")


def _deterministic_final_report(
    invoice: InvoiceData,
    validation: ValidationResult,
    approval: ApprovalDecision,
    payment: PaymentResult,
) -> FinalReport:
    """
    Build the non-LLM baseline final report.

    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :param approval: Final approval decision.
    :param payment: Payment result for the invoice.
    :return: Deterministic final report.
    """

    risk_flags = _risk_flags(validation)

    if payment.status == "success":
        return FinalReport(
            outcome="approved_paid",
            summary=(
                f"Invoice {invoice.invoice_id} from {invoice.vendor} was approved "
                f"and paid for {invoice.currency} {invoice.amount:.2f}."
            ),
            risk_flags=risk_flags,
            next_action="No further action required.",
        )

    if payment.status == "failed":
        return FinalReport(
            outcome="payment_failed",
            summary=(
                f"Invoice {invoice.invoice_id} from {invoice.vendor} was approved, "
                f"but payment failed: {payment.detail}"
            ),
            risk_flags=risk_flags,
            next_action="Escalate to accounts payable for payment retry or manual review.",
        )

    return FinalReport(
        outcome="rejected",
        summary=(
            f"Invoice {invoice.invoice_id or 'unknown'} from {invoice.vendor or 'unknown vendor'} "
            f"was rejected. Reason: {approval.reason}"
        ),
        risk_flags=risk_flags,
        next_action=_rejection_next_action(risk_flags),
    )


def _grok_final_report(
    invoice: InvoiceData,
    validation: ValidationResult,
    approval: ApprovalDecision,
    payment: PaymentResult,
    deterministic_report: FinalReport,
) -> FinalReport:
    """
    Ask Grok to polish the final report without changing key fields.

    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :param approval: Final approval decision.
    :param payment: Payment result for the invoice.
    :param deterministic_report: Baseline report with authoritative fields.
    :return: Final report with preserved authoritative fields.
    """

    grok = get_grok_api()
    messages = [
        SystemMessage(
            content=(
                "You are the overseer agent preparing the final invoice processing report. "
                "Write a concise audit-friendly summary and next action. You must preserve the "
                "provided outcome and risk_flags exactly. Do not override validation, approval, "
                "payment, or routing decisions. Return only JSON matching FinalReport."
                "An invoice being past due is not, by itself, a rejection reason or a risk flag. "
                "Treat past-due status as payment urgency only. Do not reject, escalate, or describe risk "
                "solely because the due date is earlier than the current date. "
                "Only mention date-related risk when validation.date_facts shows an invalid date, "
                "an invoice date in the future, or a due date before the invoice date. "
            )
        ),
        HumanMessage(
            content=json.dumps(
                {
                    "invoice": invoice.model_dump(),
                    "validation": validation.model_dump(),
                    "approval": approval.model_dump(),
                    "payment": payment.model_dump(),
                    "authoritative_report_fields": deterministic_report.model_dump(),
                },
                indent=2,
                default=str,
            )
        ),
    ]

    try:
        report = grok.with_structured_output(FinalReport).invoke(messages)
        if isinstance(report, FinalReport):
            return _preserve_authoritative_report_fields(report, deterministic_report)
        if isinstance(report, dict):
            return _preserve_authoritative_report_fields(
                FinalReport.model_validate(report),
                deterministic_report,
            )
    except (AttributeError, NotImplementedError, TypeError, ValueError, ValidationError):
        pass

    response = grok.invoke(messages)
    parsed = _extract_json_object(response.content)
    if parsed:
        try:
            return _preserve_authoritative_report_fields(
                FinalReport.model_validate(parsed),
                deterministic_report,
            )
        except ValidationError:
            pass

    return deterministic_report


def _preserve_authoritative_report_fields(
    grok_report: FinalReport,
    deterministic_report: FinalReport,
) -> FinalReport:
    """Keep the authoritative outcome fields from the baseline report.

    :param grok_report: Report generated by Grok.
    :param deterministic_report: Baseline deterministic report.
    :return: Final report with preserved authoritative fields.
    """

    grok_report.outcome = deterministic_report.outcome
    grok_report.risk_flags = deterministic_report.risk_flags
    return grok_report


def _risk_flags(validation: ValidationResult) -> list[str]:
    """Collect unique validation issue codes as risk flags.

    :param validation: Validation result for the invoice.
    :return: Sorted list of unique risk flags.
    """

    return sorted({issue.code for issue in validation.issues})


def _rejection_next_action(risk_flags: list[str]) -> str:
    """Choose the next action for a rejected invoice.

    :param risk_flags: Risk flags attached to the invoice.
    :return: Recommended next action.
    """

    if any(flag in risk_flags for flag in {"quantity_exceeds_stock", "zero_stock", "unknown_item"}):
        return "Route to procurement or inventory owner for manual review."
    if any(flag.startswith("missing_") for flag in risk_flags):
        return "Request corrected invoice details from the vendor or AP intake team."
    if any(flag in risk_flags for flag in {"invalid_amount", "invalid_quantity"}):
        return "Request a corrected invoice before payment can proceed."
    return "Notify accounts payable of the rejection reason."


def _extract_json_object(content: str) -> dict | None:
    """Extract the first JSON object candidate from text.

    :param content: Raw text that may contain JSON.
    :return: Parsed JSON object when found, otherwise ``None``.
    """

    content = content.strip()
    candidates = [content]

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(content[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _log_entry(agent: str, event: str, message: str, **metadata: Any) -> LogEntry:
    """Create a log entry with filtered metadata.

    :param agent: Agent responsible for the event.
    :param event: Event name.
    :param message: Human-readable event message.
    :param metadata: Extra metadata to attach when present.
    :return: Structured log entry.
    """

    return LogEntry(
        timestamp=datetime.now().isoformat(),
        agent=agent,
        event=event,
        message=message,
        metadata={key: value for key, value in metadata.items() if value is not None},
    )
