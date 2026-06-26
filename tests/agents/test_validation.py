import sqlite3

import pytest

from src.agents import validation
from src.agents.ingestion import ingest_invoice
from src.tools import InvoiceData, Item
from tests.conftest import FakeLLM


def test_validate_invoice_passes_clean_invoice(
    monkeypatch: pytest.MonkeyPatch,
    sample_invoice: InvoiceData,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10, "WidgetB": 5})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationNarrative(
                    explanation="Inventory and header checks passed.",
                    suggested_corrections=[],
                )
            ]
        ),
    )

    result = validation.validate_invoice(sample_invoice)

    assert result.passed is True
    assert result.issues == []
    assert result.explanation == "Inventory and header checks passed."
    assert result.date_facts is not None
    assert result.date_facts.invoice_date_valid is True
    assert result.date_facts.due_date_valid is True
    assert result.date_facts.due_date_in_future is True


def test_validate_invoice_collects_business_rule_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 15, "FakeItem": 0})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(code="missing_invoice_id", message="Invoice ID is missing.", severity="error"),
                        validation.ValidationIssue(code="missing_vendor", message="Vendor is missing.", severity="error"),
                        validation.ValidationIssue(code="missing_due_date", message="Due date is missing.", severity="error"),
                        validation.ValidationIssue(code="invalid_amount", message="Invoice amount must be positive; got -5.0.", severity="error"),
                        validation.ValidationIssue(code="missing_item_name", message="Line item name is missing.", severity="error"),
                        validation.ValidationIssue(code="invalid_quantity", message="Quantity must be positive; got 0.", severity="error"),
                        validation.ValidationIssue(code="quantity_exceeds_stock", message="Requested 20 units of 'WidgetA', but only 15 are available.", severity="error", item_name="WidgetA"),
                        validation.ValidationIssue(code="zero_stock", message="Item 'FakeItem' has zero stock.", severity="error", item_name="FakeItem"),
                        validation.ValidationIssue(code="unknown_item", message="Item 'GhostPart' was not found in inventory.", severity="error", item_name="GhostPart"),
                    ],
                    review_notes="Confirmed deterministic validation issues.",
                ),
                validation.ValidationNarrative(
                    explanation="Several blocking issues were detected.",
                    suggested_corrections=["Correct quantities and replace unknown items."],
                )
            ]
        ),
    )

    invoice = InvoiceData(
        invoice_id="",
        vendor="",
        amount=-5.0,
        items=[
            Item(name="", quantity=0),
            Item(name="WidgetA", quantity=20),
            Item(name="FakeItem", quantity=1),
            Item(name="GhostPart", quantity=1),
        ],
        due_date=None,
    )

    result = validation.validate_invoice(invoice)
    issue_codes = {issue.code for issue in result.issues}

    assert result.passed is False
    assert {
        "missing_invoice_id",
        "missing_vendor",
        "missing_due_date",
        "invalid_amount",
        "missing_item_name",
        "invalid_quantity",
        "quantity_exceeds_stock",
        "zero_stock",
        "unknown_item",
    }.issubset(issue_codes)


def test_validate_invoice_uses_fallback_narrative_when_grok_output_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    sample_invoice: InvoiceData,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10, "WidgetB": 5})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[],
                    review_notes="No blocking issues remain after review.",
                ),
                TypeError("structured output unavailable"),
            ],
            invoke_results=["not json"],
        ),
    )

    result = validation.validate_invoice(sample_invoice)

    assert result.explanation == "The validation review did not return a valid explanation."
    assert result.suggested_corrections == []


def test_validate_invoice_reads_from_real_sqlite_inventory(
    monkeypatch: pytest.MonkeyPatch,
    sample_invoice: InvoiceData,
    sqlite_inventory_db,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    db_path = sqlite_inventory_db([("WidgetA", 10), ("WidgetB", 5)])
    monkeypatch.setattr(validation, "get_inventory", lambda: sqlite3.connect(db_path))
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationNarrative(
                    explanation="SQLite-backed validation passed.",
                    suggested_corrections=[],
                )
            ]
        ),
    )

    result = validation.validate_invoice(sample_invoice)

    assert result.passed is True
    assert result.issues == []
    assert result.explanation == "SQLite-backed validation passed."


def test_validate_invoice_reports_inventory_issues_from_real_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_inventory_db,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    db_path = sqlite_inventory_db([("WidgetA", 5), ("FakeItem", 0)])
    monkeypatch.setattr(validation, "get_inventory", lambda: sqlite3.connect(db_path))
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(code="quantity_exceeds_stock", message="Requested 10 units of 'WidgetA', but only 5 are available.", severity="error", item_name="WidgetA"),
                        validation.ValidationIssue(code="zero_stock", message="Item 'FakeItem' has zero stock.", severity="error", item_name="FakeItem"),
                        validation.ValidationIssue(code="unknown_item", message="Item 'GhostPart' was not found in inventory.", severity="error", item_name="GhostPart"),
                    ],
                    review_notes="Confirmed inventory issues.",
                ),
                validation.ValidationNarrative(
                    explanation="Inventory mismatches found.",
                    suggested_corrections=["Review stock before payment."],
                )
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-SQL",
        vendor="Widgets Inc.",
        amount=1000.0,
        items=[
            Item(name="WidgetA", quantity=10),
            Item(name="FakeItem", quantity=1),
            Item(name="GhostPart", quantity=1),
        ],
        due_date="2026-02-01",
    )

    result = validation.validate_invoice(invoice)
    issue_codes = {issue.code for issue in result.issues}

    assert result.passed is False
    assert {"quantity_exceeds_stock", "zero_stock", "unknown_item"} <= issue_codes


def test_validate_invoice_adds_deterministic_date_issues_and_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 6, 25))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(
                            code="invoice_date_in_future",
                            message="Invoice date '2026-07-01' is after current date '2026-06-25'.",
                            severity="warning",
                        ),
                        validation.ValidationIssue(
                            code="due_date_before_invoice_date",
                            message="Due date '2026-06-01' is before invoice date '2026-07-01'.",
                            severity="error",
                        ),
                    ],
                    review_notes="Confirmed date issues.",
                ),
                validation.ValidationNarrative(
                    explanation="Date issues found.",
                    suggested_corrections=["Correct the invoice header dates."],
                )
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-DATE",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=1)],
        invoice_date="2026-07-01",
        due_date="2026-06-01",
    )

    result = validation.validate_invoice(invoice)
    issue_codes = {issue.code for issue in result.issues}

    assert {"invoice_date_in_future", "due_date_before_invoice_date"} <= issue_codes
    assert result.date_facts is not None
    assert result.date_facts.current_date == "2026-06-25"
    assert result.date_facts.invoice_date_in_future is True
    assert result.date_facts.due_date_in_future is False
    assert result.date_facts.due_date_before_invoice_date is True


def test_validate_invoice_flags_aggregate_quantity_exceeds_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10, "WidgetB": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(
                            code="aggregate_quantity_exceeds_stock",
                            message="Requested 11 total units of 'WidgetA' across the invoice, but only 10 are available.",
                            severity="error",
                            item_name="WidgetA",
                        )
                    ],
                    review_notes="Confirmed aggregate stock issue.",
                ),
                validation.ValidationNarrative(
                    explanation="Aggregate stock issue found.",
                    suggested_corrections=["Reduce duplicate line item quantities."],
                )
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-AGG",
        vendor="Widgets Inc.",
        amount=1500.0,
        items=[
            Item(name="WidgetA", quantity=6, unit_price=100.0, line_total=600.0),
            Item(name="WidgetA", quantity=5, unit_price=100.0, line_total=500.0),
            Item(name="WidgetB", quantity=4, unit_price=100.0, line_total=400.0),
        ],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    aggregate_issues = [issue for issue in result.issues if issue.code == "aggregate_quantity_exceeds_stock"]

    assert result.passed is False
    assert len(aggregate_issues) == 1
    assert aggregate_issues[0].item_name == "WidgetA"
    assert "11 total units" in aggregate_issues[0].message


def test_validate_invoice_flags_invoice_total_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10, "WidgetB": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(
                            code="invoice_total_mismatch",
                            message="Invoice amount 2000.00 does not match computed invoice total of 900.00.",
                            severity="error",
                        )
                    ],
                    review_notes="Confirmed invoice total mismatch.",
                ),
                validation.ValidationNarrative(
                    explanation="Invoice total mismatch found.",
                    suggested_corrections=["Correct the invoice header total."],
                )
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-TOTAL",
        vendor="Widgets Inc.",
        amount=2000.0,
        items=[
            Item(name="WidgetA", quantity=5, unit_price=100.0, line_total=500.0),
            Item(name="WidgetB", quantity=4, unit_price=100.0, line_total=400.0),
        ],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    mismatch_issues = [issue for issue in result.issues if issue.code == "invoice_total_mismatch"]

    assert result.passed is False
    assert len(mismatch_issues) == 1
    assert "2000.00" in mismatch_issues[0].message
    assert "900.00" in mismatch_issues[0].message


def test_validate_invoice_flags_line_total_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(
                            code="line_total_mismatch",
                            message="Line total for 'WidgetA' is 500.00, but quantity 2 x unit price 100.00 equals 200.00.",
                            severity="error",
                            item_name="WidgetA",
                        )
                    ],
                    review_notes="Confirmed line total mismatch.",
                ),
                validation.ValidationNarrative(
                    explanation="Line total mismatch found.",
                    suggested_corrections=["Correct the line total or unit price."],
                ),
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-LINE",
        vendor="Widgets Inc.",
        amount=500.0,
        items=[Item(name="WidgetA", quantity=2, unit_price=100.0, line_total=500.0)],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    issues = [issue for issue in result.issues if issue.code == "line_total_mismatch"]

    assert result.passed is False
    assert len(issues) == 1
    assert issues[0].item_name == "WidgetA"


def test_validate_invoice_flags_missing_item_numeric_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(
                            code="missing_item_numeric_data",
                            message="Line item 'WidgetA' is missing both unit price and line total.",
                            severity="error",
                            item_name="WidgetA",
                        )
                    ],
                    review_notes="Confirmed missing item numeric data.",
                ),
                validation.ValidationNarrative(
                    explanation="Missing item numeric data found.",
                    suggested_corrections=["Provide either a unit price or line total."],
                ),
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-MISSING-NUMERIC",
        vendor="Widgets Inc.",
        amount=100.0,
        items=[Item(name="WidgetA", quantity=1, unit_price=None, line_total=None)],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    issues = [issue for issue in result.issues if issue.code == "missing_item_numeric_data"]

    assert result.passed is False
    assert len(issues) == 1
    assert issues[0].item_name == "WidgetA"


def test_validate_invoice_flags_partial_item_numeric_data_as_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationNarrative(
                    explanation="Partial item numeric data found.",
                    suggested_corrections=["Add the missing line total for clearer reconciliation."],
                ),
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-PARTIAL-ITEM",
        vendor="Widgets Inc.",
        amount=100.0,
        items=[Item(name="WidgetA", quantity=1, unit_price=100.0, line_total=None)],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    issues = [issue for issue in result.issues if issue.code == "partial_item_numeric_data"]

    assert result.passed is True
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_validate_invoice_flags_subtotal_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10, "WidgetB": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(
                            code="subtotal_mismatch",
                            message="Invoice subtotal 1000.00 does not match computed subtotal of 900.00.",
                            severity="error",
                        )
                    ],
                    review_notes="Confirmed subtotal mismatch.",
                ),
                validation.ValidationNarrative(
                    explanation="Subtotal mismatch found.",
                    suggested_corrections=["Correct the invoice subtotal."],
                ),
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-SUBTOTAL",
        vendor="Widgets Inc.",
        amount=1000.0,
        subtotal=1000.0,
        items=[
            Item(name="WidgetA", quantity=5, unit_price=100.0, line_total=500.0),
            Item(name="WidgetB", quantity=4, unit_price=100.0, line_total=400.0),
        ],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    issues = [issue for issue in result.issues if issue.code == "subtotal_mismatch"]

    assert result.passed is False
    assert len(issues) == 1
    assert "1000.00" in issues[0].message
    assert "900.00" in issues[0].message


def test_validate_invoice_flags_partial_invoice_numeric_data_as_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10, "WidgetB": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationNarrative(
                    explanation="Partial invoice numeric data found.",
                    suggested_corrections=["Provide both tax rate and tax amount for full reconciliation."],
                ),
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-PARTIAL-INVOICE",
        vendor="Widgets Inc.",
        amount=972.0,
        subtotal=900.0,
        tax_rate=0.08,
        tax_amount=None,
        items=[
            Item(name="WidgetA", quantity=5, unit_price=100.0, line_total=500.0),
            Item(name="WidgetB", quantity=4, unit_price=100.0, line_total=400.0),
        ],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    issues = [issue for issue in result.issues if issue.code == "partial_invoice_numeric_data"]

    assert result.passed is True
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_validate_invoice_flags_tax_amount_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10, "WidgetB": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(
                            code="tax_amount_mismatch",
                            message="Invoice tax amount 100.00 does not match computed tax amount of 72.00 from subtotal 900.00 and tax rate 0.0800.",
                            severity="error",
                        )
                    ],
                    review_notes="Confirmed tax amount mismatch.",
                ),
                validation.ValidationNarrative(
                    explanation="Tax amount mismatch found.",
                    suggested_corrections=["Correct the tax amount or tax rate."],
                ),
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-TAX-MISMATCH",
        vendor="Widgets Inc.",
        amount=1000.0,
        subtotal=900.0,
        tax_rate=0.08,
        tax_amount=100.0,
        items=[
            Item(name="WidgetA", quantity=5, unit_price=100.0, line_total=500.0),
            Item(name="WidgetB", quantity=4, unit_price=100.0, line_total=400.0),
        ],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    issues = [issue for issue in result.issues if issue.code == "tax_amount_mismatch"]

    assert result.passed is False
    assert len(issues) == 1
    assert "100.00" in issues[0].message
    assert "72.00" in issues[0].message


def test_validate_invoice_accounts_for_tax_in_computed_invoice_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 10, "WidgetB": 10})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationNarrative(
                    explanation="Tax-inclusive total matches.",
                    suggested_corrections=[],
                )
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-TAX",
        vendor="Widgets Inc.",
        amount=972.0,
        items=[
            Item(name="WidgetA", quantity=5, unit_price=100.0, line_total=500.0),
            Item(name="WidgetB", quantity=4, unit_price=100.0, line_total=400.0),
        ],
        subtotal=900.0,
        tax_rate=0.08,
        tax_amount=72.0,
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)
    issue_codes = {issue.code for issue in result.issues}

    assert result.passed is True
    assert "invoice_total_mismatch" not in issue_codes


def test_validate_invoice_flags_aggregate_stock_and_total_mismatch_on_structured_invoice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 6, 25))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"WidgetA": 15, "WidgetB": 10, "GadgetX": 5})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[
                        validation.ValidationIssue(
                            code="aggregate_quantity_exceeds_stock",
                            message="Requested 22 total units of 'WidgetA' across the invoice, but only 15 are available.",
                            severity="error",
                            item_name="WidgetA",
                        ),
                        validation.ValidationIssue(
                            code="aggregate_quantity_exceeds_stock",
                            message="Requested 18 total units of 'WidgetB' across the invoice, but only 10 are available.",
                            severity="error",
                            item_name="WidgetB",
                        ),
                        validation.ValidationIssue(
                            code="aggregate_quantity_exceeds_stock",
                            message="Requested 9 total units of 'GadgetX' across the invoice, but only 5 are available.",
                            severity="error",
                            item_name="GadgetX",
                        ),
                        validation.ValidationIssue(
                            code="invoice_total_mismatch",
                            message="Invoice amount 22562.80 does not match computed invoice total of 22512.80.",
                            severity="error",
                        ),
                    ],
                    review_notes="Confirmed aggregate stock and total mismatch.",
                ),
                validation.ValidationNarrative(
                    explanation="Aggregate stock and total mismatch found.",
                    suggested_corrections=["Reduce quantities and correct the invoice total."],
                )
            ]
        ),
    )
    invoice = ingest_invoice("data/invoices/invoice_1013.json")

    result = validation.validate_invoice(invoice)
    issue_codes = {issue.code for issue in result.issues}

    assert result.passed is False
    assert "aggregate_quantity_exceeds_stock" in issue_codes
    assert "invoice_total_mismatch" in issue_codes


def test_validate_invoice_uses_llm_review_to_dismiss_false_unknown_item_typo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation, "_today", lambda: validation.date(2026, 1, 20))
    monkeypatch.setattr(validation, "_load_inventory", lambda: {"GadgetX": 5})
    monkeypatch.setattr(
        validation,
        "get_grok_api",
        lambda: FakeLLM(
            structured_results=[
                validation.ValidationReview(
                    confirmed_issues=[],
                    review_notes="Dismissed false positive: 'Gadget X' matches inventory item 'GadgetX'.",
                ),
                validation.ValidationNarrative(
                    explanation="Validation passed after review.",
                    suggested_corrections=[],
                ),
            ]
        ),
    )
    invoice = InvoiceData(
        invoice_id="INV-TYPO",
        vendor="Widgets Inc.",
        amount=100.0,
        items=[Item(name="Gadget X", quantity=1, unit_price=100.0, line_total=100.0)],
        invoice_date="2026-01-10",
        due_date="2026-02-10",
    )

    result = validation.validate_invoice(invoice)

    assert result.passed is True
    assert result.issues == []
    assert result.explanation == "Validation passed after review."
