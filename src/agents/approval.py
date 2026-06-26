"""Approval agent logic for invoice review decisions."""

import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from src.grok import get_grok_api
from src.tools import ApprovalDecision, InvoiceData, ValidationResult


APPROVAL_THRESHOLD = 10000.0


def approve_invoice(invoice: InvoiceData, validation: ValidationResult) -> ApprovalDecision:
    """Produce the approval decision for a validated invoice.

    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :return: Final approval decision.
    """

    guardrail_decision = _reject_hard_validation_failures(invoice, validation)
    if guardrail_decision:
        return guardrail_decision

    scrutiny_decision = _reject_for_additional_scrutiny(invoice)
    if scrutiny_decision:
        return scrutiny_decision

    grok = get_grok_api()
    messages = _approval_messages(invoice, validation)
    decision = _invoke_grok_approval(grok, messages, invoice)
    decision = _critique_approval(grok, invoice, validation, decision)
    return _determine_scrutiny(decision, invoice)


def _reject_hard_validation_failures(
    invoice: InvoiceData,
    validation: ValidationResult,
) -> ApprovalDecision | None:
    """
    Reject invoices that have blocking validation errors.

    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :return: Rejection decision when hard errors exist, otherwise ``None``.
    """

    errors = [issue for issue in validation.issues if issue.severity == "error"]
    if not errors:
        return None

    reason = "Rejected because validation found hard errors: " + "; ".join(
        issue.message for issue in errors
    )
    return ApprovalDecision(
        approved=False,
        reason=reason,
        requires_scrutiny=invoice.amount > APPROVAL_THRESHOLD,
        critique="Validation errors are hard approval blockers.",
    )


def _reject_for_additional_scrutiny(invoice: InvoiceData) -> ApprovalDecision | None:
    """
    Route large invoices to human review instead of auto-approval.

    :param invoice: Normalized invoice data.
    :return: Scrutiny decision for large invoices, otherwise ``None``.
    """

    if invoice.amount <= APPROVAL_THRESHOLD:
        return None
    return ApprovalDecision(
        approved=False,
        reason=(
            f"Rejected for human review because invoice amount {invoice.amount:.2f} exceeds "
            f"the approval threshold of {APPROVAL_THRESHOLD:.2f}."
        ),
        requires_scrutiny=True,
        critique="Invoices over the approval threshold require human review.",
    )

def _approval_messages(invoice: InvoiceData, validation: ValidationResult) -> list[object]:
    """
    Build the prompt messages for the approval model.

    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :return: Prompt messages for Grok.
    """

    return [
        SystemMessage(
            content=(
                "You are a VP-level invoice approval agent for a manufacturing company. "
                "Reason about approval risk, payment urgency, suspicious values, extraction warnings, "
                "validation explanations, suggested corrections, authoritative validation date facts, "
                "extraction confidence, and amount thresholds. Do not contradict validation.date_facts when "
                "describing invoice dates, due dates, or whether a date is in the future. "
                "An invoice being past due is not, by itself, a rejection reason or a risk flag. "
                "Treat past-due status as payment urgency only. Do not reject or escalate solely because "
                "the due date is earlier than the current date. Only mention date-related risk when "
                "validation.date_facts shows an invalid date, an invoice date in the future, or a due date "
                "before the invoice date."
                "Invoices over $10K require heightened scrutiny. Return only JSON matching this schema: "
                '{"approved": boolean, "reason": string, '
                '"requires_scrutiny": boolean, "critique": string}.'
            )
        ),
        HumanMessage(
            content=json.dumps(
                {
                    "invoice": invoice.model_dump(),
                    "validation": validation.model_dump(),
                    "policy": {
                        "approval_threshold": APPROVAL_THRESHOLD,
                        "hard_validation_errors_block_approval": True,
                    },
                },
                indent=2,
                default=str,
            )
        ),
    ]


def _critique_approval(
    llm: BaseChatModel,
    invoice: InvoiceData,
    validation: ValidationResult,
    decision: ApprovalDecision,
) -> ApprovalDecision:
    """
    Run a second-pass critique on the draft approval decision.

    :param llm: Grok model used for the critique pass.
    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :param decision: Draft approval decision to review.
    :return: Critiqued approval decision.
    """

    messages = [
        SystemMessage(
            content=(
                "You are a critical reviewer for an invoice approval decision. Look for missed risks, "
                "policy violations, extraction uncertainty, payment urgency, and whether >$10K scrutiny "
                "was handled. Treat validation.date_facts as authoritative and do not contradict them when "
                "describing invoice dates, due dates, or whether a date is in the future. Return only JSON "
                "matching the ApprovalDecision schema. You may revise the decision if the critique reveals a "
                "material risk."
            )
        ),
        HumanMessage(
            content=json.dumps(
                {
                    "invoice": invoice.model_dump(),
                    "validation": validation.model_dump(),
                    "draft_approval_decision": decision.model_dump(),
                    "policy": {
                        "approval_threshold": APPROVAL_THRESHOLD,
                        "hard_validation_errors_block_approval": True,
                    },
                },
                indent=2,
                default=str,
            )
        ),
    ]

    try:
        revised = llm.with_structured_output(ApprovalDecision).invoke(messages)
        if isinstance(revised, ApprovalDecision):
            return _determine_scrutiny(revised, invoice)
        if isinstance(revised, dict):
            return _determine_scrutiny(ApprovalDecision.model_validate(revised), invoice)
    except (AttributeError, NotImplementedError, TypeError, ValueError, ValidationError):
        pass

    response = llm.invoke(messages)
    parsed = _extract_json_object(response.content)
    if parsed:
        try:
            return _determine_scrutiny(ApprovalDecision.model_validate(parsed), invoice)
        except ValidationError:
            pass

    decision.critique = decision.critique or "The approval review did not return a valid structured decision."
    return decision


def _invoke_grok_approval(
    llm: BaseChatModel,
    messages: list[object],
    invoice: InvoiceData,
) -> ApprovalDecision:
    """
    Call Grok and normalize its approval response.

    :param llm: Grok model used for approval.
    :param messages: Prompt messages for the model.
    :param invoice: Normalized invoice data.
    :return: Parsed approval decision.
    """

    try:
        decision = llm.with_structured_output(ApprovalDecision).invoke(messages)
        if isinstance(decision, ApprovalDecision):
            return _determine_scrutiny(decision, invoice)
        if isinstance(decision, dict):
            return _determine_scrutiny(ApprovalDecision.model_validate(decision), invoice)
    except (AttributeError, NotImplementedError, TypeError, ValueError, ValidationError):
        pass

    response = llm.invoke(messages)
    return _approval_from_response(response.content, invoice)

def _approval_from_response(content: str, invoice: InvoiceData) -> ApprovalDecision:
    """
    Parse an approval decision from raw model output.

    :param content: Raw model response text.
    :param invoice: Normalized invoice data.
    :return: Parsed or fallback approval decision.
    """

    parsed = _extract_json_object(content)
    if parsed:
        try:
            decision = ApprovalDecision.model_validate(parsed)
            return _determine_scrutiny(decision, invoice)
        except ValidationError:
            pass

    return ApprovalDecision(
        approved=False,
        reason="The approval review did not return a valid structured decision.",
        requires_scrutiny=invoice.amount > APPROVAL_THRESHOLD,
        critique=content,
    )

def _extract_json_object(content: str) -> dict | None:
    """
    Extract the first JSON object candidate from text.

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

def _determine_scrutiny(
    decision: ApprovalDecision,
    invoice: InvoiceData,
) -> ApprovalDecision:
    """
    Apply the scrutiny flag based on invoice amount.

    :param decision: Approval decision to adjust.
    :param invoice: Normalized invoice data.
    :return: Approval decision with the correct scrutiny flag.
    """

    if invoice.amount > APPROVAL_THRESHOLD:
        decision.requires_scrutiny = True
    return decision
