import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from src.state import AgentState
from src.tools import (
    ApprovalDecision,
    FinalReport,
    InvoiceData,
    Item,
    PaymentResult,
    ValidationIssue,
    ValidationResult,
)


class FakeStructuredInvoker:
    def __init__(self, llm: "FakeLLM") -> None:
        self.llm = llm

    def invoke(self, messages: list[object]) -> object:
        self.llm.structured_messages.append(messages)
        return self.llm.pop_structured()


class FakeLLM:
    def __init__(
        self,
        structured_results: list[object] | None = None,
        invoke_results: list[object] | None = None,
    ) -> None:
        self.structured_results = list(structured_results or [])
        self.invoke_results = list(invoke_results or [])
        self.structured_messages: list[list[object]] = []
        self.invoke_messages: list[list[object]] = []

    def with_structured_output(self, _schema: object) -> FakeStructuredInvoker:
        return FakeStructuredInvoker(self)

    def invoke(self, messages: list[object]) -> SimpleNamespace:
        self.invoke_messages.append(messages)
        result = self._pop(self.invoke_results)
        return SimpleNamespace(content=result)

    def pop_structured(self) -> object:
        return self._pop(self.structured_results)

    @staticmethod
    def _pop(results: list[object]) -> object:
        if not results:
            raise AssertionError("FakeLLM ran out of configured results.")
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def sample_items() -> list[Item]:
    return [
        Item(name="WidgetA", quantity=2, unit_price=250.0, line_total=500.0),
        Item(name="WidgetB", quantity=1, unit_price=500.0, line_total=500.0),
    ]


@pytest.fixture
def sample_invoice(sample_items: list[Item]) -> InvoiceData:
    return InvoiceData(
        invoice_id="INV-TEST",
        vendor="Widgets Inc.",
        amount=1000.0,
        items=sample_items,
        due_date="2026-02-01",
        invoice_date="2026-01-15",
    )


@pytest.fixture
def clean_validation() -> ValidationResult:
    return ValidationResult(
        passed=True,
        issues=[],
        explanation="Validation passed.",
        suggested_corrections=[],
    )


@pytest.fixture
def blocking_validation() -> ValidationResult:
    return ValidationResult(
        passed=False,
        issues=[
            ValidationIssue(
                code="unknown_item",
                message="Item 'GhostPart' was not found in inventory.",
                severity="error",
                item_name="GhostPart",
            )
        ],
        explanation="Validation failed.",
        suggested_corrections=["Replace GhostPart with a stocked item."],
    )


@pytest.fixture
def make_approval_decision() -> Any:
    def factory(**overrides: Any) -> ApprovalDecision:
        data = {
            "approved": True,
            "reason": "Approved.",
            "requires_scrutiny": False,
            "critique": "Reviewed.",
        }
        data.update(overrides)
        return ApprovalDecision(**data)

    return factory


@pytest.fixture
def make_payment_result() -> Any:
    def factory(**overrides: Any) -> PaymentResult:
        data = {
            "status": "success",
            "vendor": "Widgets Inc.",
            "amount": 5000.0,
            "detail": "{'status': 'success'}",
        }
        data.update(overrides)
        return PaymentResult(**data)

    return factory


@pytest.fixture
def make_final_report() -> Any:
    def factory(**overrides: Any) -> FinalReport:
        data = {
            "outcome": "approved_paid",
            "summary": "Invoice approved and paid.",
            "risk_flags": [],
            "next_action": "No further action required.",
        }
        data.update(overrides)
        return FinalReport(**data)

    return factory


@pytest.fixture
def make_state(sample_invoice: InvoiceData) -> Any:
    def factory(**overrides: Any) -> AgentState:
        data = {
            "invoice_path": "data/invoices/invoice_1001.txt",
            "invoice": sample_invoice,
            "validation": None,
            "approval": None,
            "payment": None,
            "workflow": [],
            "history": [],
        }
        data.update(overrides)
        return AgentState(**data)

    return factory


@pytest.fixture
def sqlite_inventory_db(tmp_path: pytest.TempPathFactory) -> Any:
    def factory(rows: list[tuple[str, int]]) -> str:
        db_path = tmp_path / "inventory.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE inventory (item TEXT PRIMARY KEY, stock INTEGER)")
            conn.executemany("INSERT INTO inventory(item, stock) VALUES (?, ?)", rows)
            conn.commit()
        return str(db_path)

    return factory
