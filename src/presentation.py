"""Presentation helpers for CLI and Streamlit output."""

from datetime import datetime

from src.state import AgentState
from src.tools import LogEntry, ValidationResult


OUTCOME_LABELS = {
    "approved_paid": "Approved and paid",
    "rejected": "Rejected",
    "payment_failed": "Payment failed",
    "error": "Processing error",
}

OUTCOME_TONES = {
    "approved_paid": "success",
    "rejected": "warning",
    "payment_failed": "error",
    "error": "error",
}

STAGE_DEFINITIONS = [
    ("ingestion", "Ingestion"),
    ("validation", "Validation"),
    ("approval", "Approval"),
    ("payment", "Payment"),
]


def format_cli_output(state: AgentState) -> str:
    """Format the workflow result for the command line.

    :param state: Final workflow state.
    :return: Human-readable CLI output.
    """

    invoice = state.invoice
    validation = state.validation
    approval = state.approval
    payment = state.payment
    report = state.final_report

    lines = [
        "Invoice Processing Result",
        f"Invoice: {_fallback(invoice.invoice_id if invoice else None, 'unknown')}",
        f"Vendor: {_fallback(invoice.vendor if invoice else None, 'unknown')}",
    ]

    if invoice is not None:
        lines.append(f"Amount: {invoice.currency} {invoice.amount:,.2f}")
        lines.append(f"Items: {len(invoice.items)}")

    lines.append(f"Validation: {format_validation_label(validation)}")
    lines.append(f"Approval: {format_approval_label(state)}")
    lines.append(f"Payment: {format_payment_label(state)}")
    lines.append(f"Outcome: {_fallback(report.outcome if report else None, 'not_available')}")

    if report is not None:
        lines.append(f"Summary: {report.summary}")
        lines.append(f"Next Action: {report.next_action}")
        if report.risk_flags:
            lines.append(f"Risk Flags: {', '.join(report.risk_flags)}")

    if state.logs:
        lines.append("")
        lines.append("Logs")
        for entry in state.logs:
            lines.append(f"- {format_log_line(entry)}")

    return "\n".join(lines)


def error_hint(exc: Exception) -> str:
    """Return a short recovery hint for CLI errors.

    :param exc: Raised exception.
    :return: Short recovery hint.
    """

    message = str(exc)
    if "XAI_API_KEY" in message:
        return "Set the required service API key, then retry."
    return "Review the error message and workflow input, then retry."


def describe_workflow_error(exc: Exception) -> tuple[str, str]:
    """Convert a workflow exception into business-friendly UI text.

    :param exc: Raised exception.
    :return: Error title and user-facing message.
    """

    message = str(exc)
    if "XAI_API_KEY" in message:
        return (
            "Missing API Configuration",
            "A required service API key is not configured. Add it to your environment or .env file, then try again.",
        )
    if "Unsupported invoice format" in message:
        return (
            "Unsupported Invoice Type",
            "This invoice file type is not supported. Upload a PDF, TXT, CSV, JSON, or XML invoice.",
        )
    if "does not exist" in message:
        return (
            "Invoice File Not Found",
            "The selected invoice could not be found. Choose another file and try again.",
        )
    return (
        "Invoice Processing Could Not Finish",
        "The workflow ran into an unexpected problem. Review the details below and try again.",
    )


def format_validation_label(validation: ValidationResult | None) -> str:
    """Format the validation status for display.

    :param validation: Validation result, if available.
    :return: Human-readable validation label.
    """

    if validation is None:
        return "not_run"
    return f"{'passed' if validation.passed else 'failed'} ({len(validation.issues)} issues)"


def format_approval_label(state: AgentState) -> str:
    """Format the approval status for display.

    :param state: Final workflow state.
    :return: Human-readable approval label.
    """

    if state.approval is None:
        return "not_run"
    return "approved" if state.approval.approved else "rejected"


def format_payment_label(state: AgentState) -> str:
    """Format the payment status for display.

    :param state: Final workflow state.
    :return: Human-readable payment label.
    """

    if state.payment is None:
        return "not_attempted"
    return state.payment.status


def format_outcome_label(outcome: str | None) -> str:
    """Format a final outcome code for display.

    :param outcome: Final outcome code.
    :return: Human-readable outcome label.
    """

    if outcome is None:
        return "Not available"
    return OUTCOME_LABELS.get(outcome, outcome.replace("_", " ").title())


def outcome_tone(outcome: str | None) -> str:
    """Return the display tone for an outcome.

    :param outcome: Final outcome code.
    :return: One of the Streamlit-style status tones.
    """

    if outcome is None:
        return "info"
    return OUTCOME_TONES.get(outcome, "info")


def format_risk_flag(flag: str) -> str:
    """Format a machine-friendly risk flag into readable text.

    :param flag: Machine-readable risk flag.
    :return: Human-readable risk label.
    """

    return flag.replace("_", " ").strip().title()


def summarize_validation_issues(state: AgentState) -> list[str]:
    """Summarize validation issues in plain language.

    :param state: Final workflow state.
    :return: Human-readable validation issue summaries.
    """

    if state.validation is None or not state.validation.issues:
        return []
    return [issue.message for issue in state.validation.issues]


def stage_overview(state: AgentState) -> list[dict[str, str]]:
    """Build UI-friendly stage summaries from the workflow state.

    :param state: Final workflow state.
    :return: Ordered stage summaries for the UI.
    """

    workflow_by_agent = {step.agent: step for step in state.workflow if step.agent in {"ingestion", "approval", "payment"}}

    validation_reason = "Validation is ready for review."
    validation_status = "completed" if state.validation is not None else "pending"
    if state.validation is not None and not state.validation.passed:
        validation_reason = "Validation found issues that need attention."
    elif state.validation is not None and state.validation.passed:
        validation_reason = "Validation passed with no blocking issues."

    stage_items = []
    for agent, label in STAGE_DEFINITIONS:
        if agent == "validation":
            stage_items.append(
                {
                    "agent": agent,
                    "label": label,
                    "status": validation_status,
                    "next": "",
                    "reason": validation_reason,
                }
            )
            continue

        step = workflow_by_agent.get(agent)
        status = "pending"
        next_step = ""
        reason = f"{label} has not run yet."
        if step is not None:
            status = step.status
            next_step = step.next
            reason = step.reason
        elif agent == "payment" and state.approval is not None and not state.approval.approved:
            status = "skipped"
            next_step = "complete"
            reason = "Payment was skipped because the invoice was not approved."

        stage_items.append(
            {
                "agent": agent,
                "label": label,
                "status": status,
                "next": next_step,
                "reason": reason,
            }
        )
    return stage_items


def format_stage_status(status: str) -> str:
    """Format a stage status for the UI.

    :param status: Raw stage status.
    :return: Human-readable stage status.
    """

    return status.replace("_", " ").title()


def format_log_line(entry: LogEntry) -> str:
    """Format one log entry for human-readable display.

    :param entry: Log entry to format.
    :return: Human-readable log line.
    """

    return f"[{format_timestamp(entry.timestamp)}] {entry.agent}/{entry.event}: {entry.message}"


def format_timestamp(value: str) -> str:
    """Format an ISO timestamp for display.

    :param value: Raw ISO timestamp.
    :return: Readable timestamp string.
    """

    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def _fallback(value: str | None, default: str) -> str:
    """Return a fallback string when the value is blank.

    :param value: Candidate value.
    :param default: Fallback value.
    :return: Display value.
    """

    if value is None or not str(value).strip():
        return default
    return str(value)
