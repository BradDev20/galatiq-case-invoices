"""Validation agent logic for checking extracted invoice data."""

from collections.abc import Callable
from datetime import date, datetime
import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from src.grok import get_grok_api
from src.tools import InvoiceData, Item, ValidationDateFacts, ValidationIssue, ValidationResult, get_inventory

Inventory = dict[str, int]
InvoiceRule = Callable[[InvoiceData, ValidationDateFacts], list[ValidationIssue]]
ItemRule = Callable[[Item, Inventory], list[ValidationIssue]]


class ValidationNarrative(BaseModel):
    """Stores the human-readable validation explanation."""

    explanation: str
    suggested_corrections: list[str] = Field(default_factory=list)


class ValidationReview(BaseModel):
    """Stores the QA review of candidate validation issues."""

    confirmed_issues: list[ValidationIssue] = Field(default_factory=list)
    review_notes: str = ""


def validate_invoice(invoice: InvoiceData) -> ValidationResult:
    """Run invoice validation.

    :param invoice: Normalized invoice data.
    :return: Completed validation result.
    """

    grok = get_grok_api()
    inventory = _load_inventory()
    date_facts = _date_facts(invoice)
    issues = (
        _run_invoice_rules(invoice, date_facts)
        + _run_item_rules(invoice.items, inventory)
        + _aggregate_inventory_issues(invoice, inventory)
    )
    issues = _review_failed_validation_issues(grok, invoice, issues, inventory)
    result = ValidationResult(
        passed=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        date_facts=date_facts,
    )
    narrative = _explain_validation(grok, invoice, result)
    result.explanation = narrative.explanation
    result.suggested_corrections = narrative.suggested_corrections
    return result

def _load_inventory() -> Inventory:
    """Load current inventory counts from SQLite.

    :return: Inventory mapping by item name.
    """

    with get_inventory() as conn:
        rows = conn.execute("SELECT item, stock FROM inventory").fetchall()
    return {item: stock for item, stock in rows}


def _explain_validation(grok: Any, invoice: InvoiceData, validation: ValidationResult) -> ValidationNarrative:
    """Build a business-friendly explanation of the validation result.

    :param grok: Chat model used for explanation.
    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :return: Validation explanation and suggested corrections.
    """

    messages = _validation_messages(invoice, validation)

    try:
        narrative = grok.with_structured_output(ValidationNarrative).invoke(messages)
        if isinstance(narrative, ValidationNarrative):
            return narrative
        if isinstance(narrative, dict):
            return ValidationNarrative.model_validate(narrative)
    except (AttributeError, NotImplementedError, TypeError, ValueError, ValidationError):
        pass

    response = grok.invoke(messages)
    parsed = _extract_json_object(response.content)
    if parsed:
        try:
            return ValidationNarrative.model_validate(parsed)
        except ValidationError:
            pass

    return ValidationNarrative(
        explanation="The validation review did not return a valid explanation.",
        suggested_corrections=[],
    )


def _review_failed_validation_issues(
    grok: Any,
    invoice: InvoiceData,
    issues: list[ValidationIssue],
    inventory: Inventory,
) -> list[ValidationIssue]:
    """Let Grok dismiss obvious false-positive validation errors.

    :param grok: Chat model used for review.
    :param invoice: Normalized invoice data.
    :param issues: Candidate validation issues.
    :param inventory: Current inventory snapshot.
    :return: Reviewed list of validation issues.
    """

    error_issues = [issue for issue in issues if issue.severity == "error"]
    if not error_issues:
        return issues

    messages = _validation_review_messages(invoice, error_issues, inventory)

    try:
        review = grok.with_structured_output(ValidationReview).invoke(messages)
        if isinstance(review, ValidationReview):
            return _apply_reviewed_issues(issues, error_issues, review.confirmed_issues)
        if isinstance(review, dict):
            parsed = ValidationReview.model_validate(review)
            return _apply_reviewed_issues(issues, error_issues, parsed.confirmed_issues)
    except (AttributeError, NotImplementedError, TypeError, ValueError, ValidationError):
        pass

    response = grok.invoke(messages)
    parsed = _extract_json_object(response.content)
    if parsed:
        try:
            review = ValidationReview.model_validate(parsed)
            return _apply_reviewed_issues(issues, error_issues, review.confirmed_issues)
        except ValidationError:
            pass

    return issues


def _validation_messages(invoice: InvoiceData, validation: ValidationResult) -> list[object]:
    """Build prompt messages for the validation explanation.

    :param invoice: Normalized invoice data.
    :param validation: Validation result for the invoice.
    :return: Prompt messages for Grok.
    """

    return [
        SystemMessage(
            content=(
                "You are a validation reasoning agent. The Python rule engine and SQLite inventory "
                "checks are the source of truth. Explain the validation result in business language "
                "and suggest corrections. Do not change pass/fail status or issue codes. Return only "
                'JSON with {"explanation": string, "suggested_corrections": string[]}.'
            )
        ),
        HumanMessage(
            content=json.dumps(
                {
                    "invoice": invoice.model_dump(),
                    "validation_result": validation.model_dump(),
                },
                indent=2,
                default=str,
            )
        ),
    ]


def _validation_review_messages(
    invoice: InvoiceData,
    error_issues: list[ValidationIssue],
    inventory: Inventory,
) -> list[object]:
    """
    Build prompt messages for validation issue review.

    :param invoice: Normalized invoice data.
    :param error_issues: Error issues to review.
    :param inventory: Current inventory snapshot.
    :return: Prompt messages for Grok.
    """

    return [
        SystemMessage(
            content=(
                "You are a validation QA reviewer. Review the candidate validation errors against the "
                "normalized invoice data and current inventory. You may only confirm or dismiss the "
                "provided error issues; do not invent new issues. Dismiss an issue only when it is "
                "clearly a false positive caused by formatting, spacing, punctuation, date/time formatting, or minor naming "
                "variation such as 'Gadget X' vs 'GadgetX'. Preserve issues when the evidence is not "
                "clear. Return only JSON with {'confirmed_issues': ValidationIssue[], 'review_notes': string}."
            )
        ),
        HumanMessage(
            content=json.dumps(
                {
                    "invoice": invoice.model_dump(),
                    "candidate_error_issues": [issue.model_dump() for issue in error_issues],
                    "inventory_snapshot": inventory,
                    "review_policy": {
                        "only_confirm_or_dismiss_existing_issues": True,
                        "preserve_issue_if_uncertain": True,
                        "allow_typo_and_spacing_reconciliation": True,
                    },
                },
                indent=2,
                default=str,
            )
        ),
    ]


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


def _apply_reviewed_issues(
    original_issues: list[ValidationIssue],
    original_error_issues: list[ValidationIssue],
    confirmed_error_issues: list[ValidationIssue],
) -> list[ValidationIssue]:
    """
    Keep only the error issues confirmed by the review pass.

    :param original_issues: Full list of original issues.
    :param original_error_issues: Original error-severity issues.
    :param confirmed_error_issues: Error issues confirmed by review.
    :return: Final reviewed issue list.
    """

    original_error_keys = {_issue_key(issue) for issue in original_error_issues}
    confirmed_error_keys = {_issue_key(issue) for issue in confirmed_error_issues}
    allowed_error_keys = original_error_keys & confirmed_error_keys

    final_issues: list[ValidationIssue] = []
    for issue in original_issues:
        if issue.severity != "error":
            final_issues.append(issue)
            continue
        if _issue_key(issue) in allowed_error_keys:
            final_issues.append(issue)
    return final_issues


def _issue_key(issue: ValidationIssue) -> tuple[str, str | None, str]:
    """
    Build a stable comparison key for a validation issue.

    :param issue: Validation issue to key.
    :return: Stable comparison key.
    """

    return (issue.code, issue.item_name, issue.severity)

def _run_invoice_rules(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Run every invoice-level validation rule.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Invoice-level validation issues.
    """

    return [
        issue
        for rule in INVOICE_RULES
        for issue in rule(invoice, date_facts)
    ]

def _run_item_rules(items: list[Item], inventory: Inventory) -> list[ValidationIssue]:
    """
    Run every item-level validation rule.

    :param items: Invoice line items.
    :param inventory: Current inventory snapshot.
    :return: Item-level validation issues.
    """

    return [
        issue
        for item in items
        for rule in ITEM_RULES
        for issue in rule(item, inventory)
    ]


def _aggregate_inventory_issues(invoice: InvoiceData, inventory: Inventory) -> list[ValidationIssue]:
    """
    Check invoice-wide quantity and total consistency issues.

    :param invoice: Normalized invoice data.
    :param inventory: Current inventory snapshot.
    :return: Aggregate validation issues.
    """

    issues: list[ValidationIssue] = []

    item_quantities: dict[str, int] = {}
    for item in invoice.items:
        name = item.name.strip()
        if not name:
            continue
        item_quantities[name] = item_quantities.get(name, 0) + item.quantity

    for item_name, total_quantity in item_quantities.items():
        stock = inventory.get(item_name)
        if stock is None or stock == 0 or total_quantity <= stock:
            continue
        issues.append(
            ValidationIssue(
                code="aggregate_quantity_exceeds_stock",
                message=(
                    f"Requested {total_quantity} total units of '{item_name}' across the invoice, "
                    f"but only {stock} are available."
                ),
                severity="error",
                item_name=item_name,
            )
        )

    line_total_issues = _line_total_issues(invoice.items)
    issues.extend(line_total_issues)

    computed_subtotal = _computed_invoice_subtotal(invoice.items)
    if invoice.subtotal is not None and computed_subtotal is not None and abs(invoice.subtotal - computed_subtotal) > 0.01:
        issues.append(
            _issue(
                "subtotal_mismatch",
                (
                    f"Invoice subtotal {invoice.subtotal:.2f} does not match computed subtotal "
                    f"of {computed_subtotal:.2f}."
                ),
            )
        )

    tax_amount_mismatch = _tax_amount_mismatch_issue(invoice, computed_subtotal)
    if tax_amount_mismatch is not None:
        issues.append(tax_amount_mismatch)

    computed_total = _computed_invoice_total(invoice)
    if computed_total is not None and abs(invoice.amount - computed_total) > 0.01:
        issues.append(
            _issue(
                "invoice_total_mismatch",
                (
                    f"Invoice amount {invoice.amount:.2f} does not match computed invoice total "
                    f"of {computed_total:.2f}."
                ),
            )
        )

    return issues

def _missing_invoice_id(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Flag invoices that are missing an invoice ID.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if invoice.invoice_id.strip():
        return []
    return [_issue("missing_invoice_id", "Invoice ID is missing.")]


def _missing_vendor(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Flag invoices that are missing a vendor name.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if invoice.vendor.strip():
        return []
    return [_issue("missing_vendor", "Vendor is missing.")]

def _missing_due_date(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Flag invoices that are missing a due date.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if invoice.due_date:
        return []
    return [_issue("missing_due_date", "Due date is missing.")]

def _missing_items(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Flag invoices that have no line items.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if invoice.items:
        return []
    return [_issue("missing_items", "Invoice has no line items.")]

def _invalid_amount(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Flag invoices whose total amount is not positive.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if invoice.amount > 0:
        return []
    return [_issue("invalid_amount", f"Invoice amount must be positive; got {invoice.amount}.")]


def _invalid_invoice_date(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Flag invoices with an invalid invoice date.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if invoice.invoice_date and not date_facts.invoice_date_valid:
        return [_issue("invalid_invoice_date", f"Invoice date '{invoice.invoice_date}' is invalid.")]
    return []


def _invalid_due_date(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Flag invoices with an invalid due date.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if invoice.due_date and not date_facts.due_date_valid:
        return [_issue("invalid_due_date", f"Due date '{invoice.due_date}' is invalid.")]
    return []


def _invoice_date_in_future(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Warn when the invoice date is in the future.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if date_facts.invoice_date_in_future:
        return [
            _issue(
                "invoice_date_in_future",
                f"Invoice date '{date_facts.invoice_date}' is after current date '{date_facts.current_date}'.",
                severity="warning",
            )
        ]
    return []


def _due_date_before_invoice_date(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Flag invoices whose due date comes before the invoice date.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    if date_facts.due_date_before_invoice_date:
        return [
            _issue(
                "due_date_before_invoice_date",
                f"Due date '{date_facts.due_date}' is before invoice date '{date_facts.invoice_date}'.",
            )
        ]
    return []

def _missing_item_name(item: Item, inventory: Inventory) -> list[ValidationIssue]:
    """
    Flag line items that do not have a name.

    :param item: Invoice line item to check.
    :param inventory: Current inventory snapshot.
    :return: Related validation issues.
    """

    if item.name.strip():
        return []
    return [_item_issue("missing_item_name", "Line item name is missing.", item)]

def _missing_item_numeric_data(item: Item, inventory: Inventory) -> list[ValidationIssue]:
    """
    Flag line items missing both price-related fields.

    :param item: Invoice line item to check.
    :param inventory: Current inventory snapshot.
    :return: Related validation issues.
    """

    if item.unit_price is not None or item.line_total is not None:
        return []
    return [
        _item_issue(
            "missing_item_numeric_data",
            f"Line item '{item.name or 'unknown item'}' is missing both unit price and line total.",
            item,
        )
    ]


def _partial_item_numeric_data(item: Item, inventory: Inventory) -> list[ValidationIssue]:
    """
    Warn when a line item has incomplete numeric data.

    :param item: Invoice line item to check.
    :param inventory: Current inventory snapshot.
    :return: Related validation issues.
    """

    if item.unit_price is None and item.line_total is not None:
        return [
            _item_issue(
                "partial_item_numeric_data",
                f"Line item '{item.name or 'unknown item'}' is missing unit price.",
                item,
                severity="warning",
            )
        ]
    if item.unit_price is not None and item.line_total is None:
        return [
            _item_issue(
                "partial_item_numeric_data",
                f"Line item '{item.name or 'unknown item'}' is missing line total.",
                item,
                severity="warning",
            )
        ]
    return []


def _partial_invoice_numeric_data(invoice: InvoiceData, date_facts: ValidationDateFacts) -> list[ValidationIssue]:
    """
    Warn when invoice-level numeric fields are only partially filled.

    :param invoice: Normalized invoice data.
    :param date_facts: Parsed date facts for the invoice.
    :return: Related validation issues.
    """

    issues: list[ValidationIssue] = []

    if invoice.tax_rate is not None and invoice.tax_amount is None:
        issues.append(
            _issue(
                "partial_invoice_numeric_data",
                "Invoice includes a tax rate but is missing tax amount.",
                severity="warning",
            )
        )
    if invoice.tax_amount is not None and invoice.tax_rate is None:
        issues.append(
            _issue(
                "partial_invoice_numeric_data",
                "Invoice includes a tax amount but is missing tax rate.",
                severity="warning",
            )
        )
    if invoice.subtotal is not None and invoice.amount == 0:
        issues.append(
            _issue(
                "partial_invoice_numeric_data",
                "Invoice includes a subtotal but is missing a valid invoice total.",
                severity="warning",
            )
        )

    return issues


def _invalid_quantity(item: Item, inventory: Inventory) -> list[ValidationIssue]:
    """
    Flag line items whose quantity is not positive.

    :param item: Invoice line item to check.
    :param inventory: Current inventory snapshot.
    :return: Related validation issues.
    """

    if item.quantity > 0:
        return []
    return [_item_issue("invalid_quantity", f"Quantity must be positive; got {item.quantity}.", item)]

def _unknown_item(item: Item, inventory: Inventory) -> list[ValidationIssue]:
    """
    Flag line items that do not exist in inventory.

    :param item: Invoice line item to check.
    :param inventory: Current inventory snapshot.
    :return: Related validation issues.
    """

    if not item.name.strip() or item.name in inventory:
        return []
    return [_item_issue("unknown_item", f"Item '{item.name}' was not found in inventory.", item)]

def _zero_stock_item(item: Item, inventory: Inventory) -> list[ValidationIssue]:
    """
    Flag line items that are known but out of stock.

    :param item: Invoice line item to check.
    :param inventory: Current inventory snapshot.
    :return: Related validation issues.
    """

    stock = inventory.get(item.name)
    if stock is None or stock > 0:
        return []
    return [_item_issue("zero_stock", f"Item '{item.name}' has zero stock.", item)]

def _quantity_exceeds_stock(item: Item, inventory: Inventory) -> list[ValidationIssue]:
    """
    Flag line items that request more than available stock.

    :param item: Invoice line item to check.
    :param inventory: Current inventory snapshot.
    :return: Related validation issues.
    """

    stock = inventory.get(item.name)
    if stock is None or stock == 0 or item.quantity <= stock:
        return []
    return [
        _item_issue(
            "quantity_exceeds_stock",
            f"Requested {item.quantity} units of '{item.name}', but only {stock} are available.",
            item,
        )
    ]

def _issue(code: str, message: str, severity: str = "error") -> ValidationIssue:
    """
    Build a validation issue not tied to a specific item.

    :param code: Machine-readable issue code.
    :param message: Human-readable issue message.
    :param severity: Severity level for the issue.
    :return: Validation issue instance.
    """

    return ValidationIssue(code=code, message=message, severity=severity)

def _item_issue(code: str, message: str, item: Item, severity: str = "error") -> ValidationIssue:
    """
    Build a validation issue tied to one item.

    :param code: Machine-readable issue code.
    :param message: Human-readable issue message.
    :param item: Related invoice line item.
    :param severity: Severity level for the issue.
    :return: Validation issue instance.
    """

    return ValidationIssue(
        code=code,
        message=message,
        severity=severity,
        item_name=item.name or None,
    )

def _date_facts(invoice: InvoiceData) -> ValidationDateFacts:
    """
    Parse and compare invoice date fields.

    :param invoice: Normalized invoice data.
    :return: Parsed validation date facts.
    """

    today = _today()
    invoice_dt = _parse_iso_date(invoice.invoice_date)
    due_dt = _parse_iso_date(invoice.due_date)
    return ValidationDateFacts(
        current_date=today.isoformat(),
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        invoice_date_valid=invoice_dt is not None,
        due_date_valid=due_dt is not None,
        invoice_date_in_future=invoice_dt is not None and invoice_dt > today,
        due_date_in_future=due_dt is not None and due_dt > today,
        due_date_before_invoice_date=invoice_dt is not None and due_dt is not None and due_dt < invoice_dt,
    )


def _parse_iso_date(value: str | None) -> date | None:
    """
    Parse a date string in ISO format.

    :param value: Raw date string.
    :return: Parsed date, or ``None``.
    """

    if value is None or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _today() -> date:
    """
    Return the current local date.

    :return: Current local date.
    """

    return date.today()


def _line_total_issues(items: list[Item]) -> list[ValidationIssue]:
    """
    Check whether line totals match quantity times price.

    :param items: Invoice line items.
    :return: Related validation issues.
    """

    issues: list[ValidationIssue] = []
    for item in items:
        if item.unit_price is None or item.line_total is None:
            continue
        computed_line_total = item.quantity * item.unit_price
        if abs(item.line_total - computed_line_total) > 0.01:
            issues.append(
                _item_issue(
                    "line_total_mismatch",
                    (
                        f"Line total for '{item.name}' is {item.line_total:.2f}, but quantity {item.quantity} "
                        f"x unit price {item.unit_price:.2f} equals {computed_line_total:.2f}."
                    ),
                    item,
                )
            )
    return issues


def _computed_invoice_subtotal(items: list[Item]) -> float | None:
    """
    Compute an invoice subtotal from its line items.

    :param items: Invoice line items.
    :return: Computed subtotal, or ``None``.
    """

    subtotal = 0.0
    for item in items:
        if item.line_total is not None:
            subtotal += item.line_total
            continue
        if item.unit_price is None:
            return None
        subtotal += item.quantity * item.unit_price
    return subtotal


def _tax_amount_mismatch_issue(invoice: InvoiceData, computed_subtotal: float | None) -> ValidationIssue | None:
    """
    Check whether the tax amount matches the subtotal and rate.

    :param invoice: Normalized invoice data.
    :param computed_subtotal: Computed subtotal for the invoice.
    :return: Tax mismatch issue, or ``None``.
    """

    if invoice.tax_amount is None or invoice.tax_rate is None or computed_subtotal is None:
        return None
    computed_tax_amount = computed_subtotal * invoice.tax_rate
    if abs(invoice.tax_amount - computed_tax_amount) <= 0.01:
        return None
    return _issue(
        "tax_amount_mismatch",
        (
            f"Invoice tax amount {invoice.tax_amount:.2f} does not match computed tax amount "
            f"of {computed_tax_amount:.2f} from subtotal {computed_subtotal:.2f} and tax rate {invoice.tax_rate:.4f}."
        ),
    )


def _computed_invoice_total(invoice: InvoiceData) -> float | None:
    """
    Compute the invoice total from subtotal and tax fields.

    :param invoice: Normalized invoice data.
    :return: Computed invoice total, or ``None``.
    """

    subtotal = _computed_invoice_subtotal(invoice.items)
    if subtotal is None:
        return None

    if invoice.tax_amount is not None:
        return subtotal + invoice.tax_amount
    if invoice.tax_rate is not None:
        return subtotal + (subtotal * invoice.tax_rate)
    return subtotal


INVOICE_RULES: list[InvoiceRule] = [
    _missing_invoice_id,
    _missing_vendor,
    _missing_due_date,
    _missing_items,
    _invalid_amount,
    _partial_invoice_numeric_data,
    _invalid_invoice_date,
    _invalid_due_date,
    _invoice_date_in_future,
    _due_date_before_invoice_date,
]

ITEM_RULES: list[ItemRule] = [
    _missing_item_name,
    _missing_item_numeric_data,
    _partial_item_numeric_data,
    _invalid_quantity,
    _unknown_item,
    _zero_stock_item,
    _quantity_exceeds_stock,
]
