import json
import sys
from pathlib import Path

import pytest

import main
from src.state import AgentState
from src.tools import FinalReport, InvoiceData, Item, LogEntry, PaymentResult, ValidationResult


def test_main_prints_human_friendly_success_output_and_passes_thread_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_run(invoice_path: str) -> AgentState:
        captured["invoice_path"] = invoice_path
        return AgentState(
            invoice_path=invoice_path,
            invoice=InvoiceData(
                invoice_id="INV-CLI",
                vendor="Widgets Inc.",
                amount=500.0,
                items=[Item(name="WidgetA", quantity=1)],
            ),
            validation=ValidationResult(passed=True, issues=[]),
            payment=PaymentResult(status="success", vendor="Widgets Inc.", amount=500.0, detail="paid"),
            final_report=FinalReport(
                outcome="approved_paid",
                summary="Invoice approved and paid.",
                risk_flags=[],
                next_action="No further action required.",
            ),
            logs=[
                LogEntry(
                    timestamp="2026-01-15T12:00:00",
                    agent="ingestion",
                    event="invoice_ingested",
                    message="Invoice data was extracted and normalized for validation.",
                )
            ],
            next="end",
        )

    monkeypatch.setattr(main, "run_invoice_workflow", fake_run)
    monkeypatch.setattr(sys, "argv", ["main.py", "--invoice_path", "data/invoices/invoice_1001.txt"])

    main.main()

    output = capsys.readouterr().out
    assert "Invoice Processing Result" in output
    assert "Invoice: INV-CLI" in output
    assert "Vendor: Widgets Inc." in output
    assert "Payment: success" in output
    assert "Outcome: approved_paid" in output
    assert "Logs" in output
    assert "ingestion/invoice_ingested" in output
    assert captured["invoice_path"] == str(Path("data/invoices/invoice_1001.txt"))


def test_main_prints_full_json_with_verbose_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        main,
        "run_invoice_workflow",
        lambda invoice_path: AgentState(
            invoice_path=str(invoice_path),
            invoice=InvoiceData(
                invoice_id="INV-CLI",
                vendor="Widgets Inc.",
                amount=500.0,
                items=[Item(name="WidgetA", quantity=1)],
            ),
            next="end",
        ),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--invoice_path", "data/invoices/invoice_1001.txt", "--verbose"])

    main.main()

    output = json.loads(capsys.readouterr().out)
    assert output["invoice_path"].endswith("data\\invoices\\invoice_1001.txt")
    assert output["invoice"]["invoice_id"] == "INV-CLI"


def test_main_prints_structured_error_json_for_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(main, "run_invoice_workflow", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sys, "argv", ["main.py", "--invoice_path", "data/invoices/invoice_1001.txt"])

    with pytest.raises(SystemExit) as exc:
        main.main()

    output = json.loads(capsys.readouterr().out)
    assert exc.value.code == 1
    assert output["status"] == "error"
    assert output["error_type"] == "RuntimeError"
    assert output["message"] == "boom"
    assert output["hint"] == "Review the error message and workflow input, then retry."


def test_main_returns_xai_api_key_hint_when_error_mentions_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        main,
        "run_invoice_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("XAI_API_KEY is not set in this environment")),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--invoice_path", "data/invoices/invoice_1001.txt"])

    with pytest.raises(SystemExit):
        main.main()

    output = json.loads(capsys.readouterr().out)
    assert output["hint"] == "Set the required service API key, then retry."
