from pathlib import Path

import pytest

from src.services import workflow_runner
from src.state import AgentState
from src.tools import InvoiceData, Item


def test_run_invoice_workflow_passes_thread_id_and_validates_state(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_invoke(payload: dict[str, object], config: dict[str, object]) -> dict[str, object]:
        captured["payload"] = payload
        captured["config"] = config
        return AgentState(
            invoice_path=str(payload["invoice_path"]),
            invoice=InvoiceData(
                invoice_id="INV-RUNNER",
                vendor="Widgets Inc.",
                amount=250.0,
                items=[Item(name="WidgetA", quantity=1)],
            ),
            next="end",
        ).model_dump()

    monkeypatch.setattr(workflow_runner.graph, "invoke", fake_invoke)

    result = workflow_runner.run_invoice_workflow("data/invoices/invoice_1001.txt")

    assert isinstance(result, AgentState)
    assert result.invoice is not None
    assert result.invoice.invoice_id == "INV-RUNNER"
    assert captured["payload"] == {"invoice_path": str(Path("data/invoices/invoice_1001.txt"))}
    assert captured["config"] == {"configurable": {"thread_id": str(Path("data/invoices/invoice_1001.txt"))}}
