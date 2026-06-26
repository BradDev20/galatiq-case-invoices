"""Graph assembly and node handlers for the invoice workflow."""

from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.agents.approval import approve_invoice
from src.agents.ingestion import ingest_invoice
from src.agents.overseer import build_final_report, decide_next_step, ingestion_route_reason, payment_route_reason
from src.agents.payment import process_payment
from src.agents.validation import validate_invoice
from src.state import AgentState
from src.tools import LogEntry, PaymentResult, WorkflowStep


def ingestion_node(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Run invoice ingestion and record the workflow step.

    :param state: Current workflow state.
    :return: State updates after ingestion.
    """

    current = _state(state)
    workflow = list(current.workflow)
    logs = list(current.logs)

    if current.ingestion_attempts == 0:
        workflow.append(
            WorkflowStep(
                agent="overseer",
                next="ingest",
                reason="Received invoice path and started ingestion.",
            )
        )
        logs.append(
            _log_entry(
                agent="overseer",
                event="workflow_started",
                message="Received invoice path and started ingestion.",
                invoice_path=current.invoice_path,
            )
        )

    invoice = ingest_invoice(
        Path(current.invoice_path),
        feedback=current.reingestion_feedback,
    )
    attempt = current.ingestion_attempts + 1
    workflow.append(
            WorkflowStep(
                agent="ingestion",
                next="validate",
                reason=ingestion_route_reason(attempt),
            )
        )
    logs.append(
        _log_entry(
            agent="ingestion",
            event="invoice_ingested",
            message=ingestion_route_reason(attempt),
            attempt=attempt,
            invoice_id=invoice.invoice_id or None,
            vendor=invoice.vendor or None,
            source_path=invoice.source_path,
        )
    )

    return {
        "invoice": invoice,
        "validation": None,
        "approval": None,
        "payment": None,
        "final_report": None,
        "ingestion_attempts": attempt,
        "reingestion_feedback": None,
        "workflow": workflow,
        "logs": logs,
        "next": "overseer",
        "history": current.history + [f"ingestion attempt {attempt} completed"],
    }


def validation_node(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Validate the current invoice and log the result.

    :param state: Current workflow state.
    :return: State updates after validation.
    """

    current = _state(state)
    if current.invoice is None:
        raise ValueError("Validation node requires invoice data.")

    validation = validate_invoice(current.invoice)
    logs = list(current.logs)
    logs.append(
        _log_entry(
            agent="validation",
            event="invoice_validated",
            message="Validation completed.",
            passed=validation.passed,
            issue_count=len(validation.issues),
            issue_codes=[issue.code for issue in validation.issues],
        )
    )
    return {
        "validation": validation,
        "logs": logs,
        "next": "overseer",
        "history": current.history + ["validation completed"],
    }


def approval_node(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Request an approval decision for the validated invoice.

    :param state: Current workflow state.
    :return: State updates after approval.
    """

    current = _state(state)
    if current.invoice is None or current.validation is None:
        raise ValueError("Approval node requires invoice and validation data.")

    approval = approve_invoice(current.invoice, current.validation)
    workflow = list(current.workflow)
    logs = list(current.logs)
    workflow.append(
        WorkflowStep(
            agent="approval",
            next="pay" if approval.approved else "reject",
            reason=approval.reason,
        )
    )
    logs.append(
        _log_entry(
            agent="approval",
            event="invoice_reviewed",
            message=approval.reason,
            approved=approval.approved,
            requires_scrutiny=approval.requires_scrutiny,
        )
    )

    return {
        "approval": approval,
        "workflow": workflow,
        "logs": logs,
        "next": "overseer",
        "history": current.history + ["approval completed"],
    }


def payment_node(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Process payment for an approved invoice.

    :param state: Current workflow state.
    :return: State updates after payment.
    """

    current = _state(state)
    if current.invoice is None or current.approval is None:
        raise ValueError("Payment node requires invoice and approval data.")

    payment = process_payment(current.invoice, current.approval)
    workflow = list(current.workflow)
    logs = list(current.logs)
    workflow.append(
        WorkflowStep(
            agent="payment",
            next="complete",
            reason=payment_route_reason(payment.status),
            status="completed" if payment.status != "failed" else "failed",
        )
    )
    logs.append(
        _log_entry(
            agent="payment",
            event="payment_processed",
            message=payment_route_reason(payment.status),
            status=payment.status,
            vendor=payment.vendor,
            amount=payment.amount,
        )
    )

    return {
        "payment": payment,
        "workflow": workflow,
        "logs": logs,
        "next": "finalize",
        "history": current.history + ["payment completed"],
    }


def overseer_node(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Delegate routing decisions to the overseer agent.

    :param state: Current workflow state.
    :return: State updates from the overseer.
    """

    return decide_next_step(state)


def finalize_node(state: AgentState | dict[str, Any]) -> dict[str, Any]:
    """
    Build the final report and close the workflow.

    :param state: Current workflow state.
    :return: Final state updates for the workflow.
    """

    current = _state(state)
    if current.invoice is None or current.validation is None or current.approval is None:
        raise ValueError("Finalize node requires invoice, validation, and approval data.")

    payment = current.payment or PaymentResult(
        status="not_attempted",
        vendor=current.invoice.vendor,
        amount=current.invoice.amount,
        detail=current.approval.reason,
    )
    final_report = build_final_report(
        invoice=current.invoice,
        validation=current.validation,
        approval=current.approval,
        payment=payment,
    )
    workflow = list(current.workflow)
    logs = list(current.logs)
    if current.payment is None:
        workflow.append(
            WorkflowStep(
                agent="payment",
                next="complete",
                reason="Payment was skipped because the invoice was not approved.",
            )
        )
        logs.append(
            _log_entry(
                agent="payment",
                event="payment_skipped",
                message="Payment was skipped because the invoice was not approved.",
                status="not_attempted",
                vendor=payment.vendor,
                amount=payment.amount,
            )
        )
    workflow.append(
        WorkflowStep(
            agent="overseer",
            next="complete",
            reason="Final report generated and workflow completed.",
        )
    )
    logs.append(
        _log_entry(
            agent="overseer",
            event="workflow_completed",
            message="Final report generated and workflow completed.",
            outcome=final_report.outcome,
            payment_status=payment.status,
        )
    )

    return {
        "payment": payment,
        "workflow": workflow,
        "logs": logs,
        "final_report": final_report,
        "next": "end",
        "completed_at": datetime.now().isoformat(),
        "history": current.history + ["workflow finalized"],
    }


def route_from_overseer(state: AgentState | dict[str, Any]) -> str:
    """
    Return the next graph route selected by the overseer.

    :param state: Current workflow state.
    :return: Next graph node name.
    """

    current = _state(state)
    return current.next or "end"


def build_graph():
    """
    Construct and compile the LangGraph workflow.

    :return: Compiled LangGraph workflow.
    """

    workflow = StateGraph(AgentState)

    workflow.add_node("ingestion", ingestion_node)
    workflow.add_node("validation", validation_node)
    workflow.add_node("approval", approval_node)
    workflow.add_node("payment", payment_node)
    workflow.add_node("overseer", overseer_node)
    workflow.add_node("finalize", finalize_node)

    workflow.set_entry_point("ingestion")

    workflow.add_edge("ingestion", "overseer")
    workflow.add_edge("validation", "overseer")
    workflow.add_edge("approval", "overseer")
    workflow.add_edge("payment", "finalize")
    workflow.add_edge("finalize", END)

    workflow.add_conditional_edges(
        "overseer",
        route_from_overseer,
        {
            "ingestion": "ingestion",
            "validation": "validation",
            "approval": "approval",
            "payment": "payment",
            "finalize": "finalize",
            "end": END,
        },
    )

    return workflow.compile(checkpointer=MemorySaver())


def _state(state: AgentState | dict[str, Any]) -> AgentState:
    """
    Normalize dictionary state into an ``AgentState`` object.

    :param state: State object or raw state dictionary.
    :return: Validated ``AgentState`` instance.
    """

    if isinstance(state, AgentState):
        return state
    return AgentState.model_validate(state)


def _log_entry(agent: str, event: str, message: str, **metadata: Any) -> LogEntry:
    """
    Create a log entry with filtered metadata.

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


graph = build_graph()
