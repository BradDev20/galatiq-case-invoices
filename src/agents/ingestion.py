"""Ingestion agent logic for parsing invoices from multiple formats."""

import csv
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from src.grok import get_grok_api
from src.tools import InvoiceData, Item


MONEY_RE = re.compile(r"(?P<sign>-)?\s*\$?\s*(?P<amount>[0-9][0-9,]*(?:\.\d{2})?)")
PRICE_PATTERN = r"[\d,]+(?:\.\d{2})?"

def ingest_invoice(invoice_path: str | Path, feedback: str | None = None) -> InvoiceData:
    """
    Load an invoice file and parse it into normalized data.

    :param invoice_path: Path to the invoice file.
    :param feedback: Optional feedback for re-extraction.
    :return: Parsed invoice data.
    """

    path = Path(invoice_path)
    if not path.exists():
        raise FileNotFoundError(f"Invoice file does not exist: {path}")

    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"Unsupported invoice format: {path.suffix.lower()}")

    invoice = parser(path, feedback)

    if feedback:
        invoice.extraction_warnings.append(f"Re-extraction attempted with feedback: {feedback}")

    invoice.source_path = str(path)
    return invoice


Parser = Callable[[Path, str | None], InvoiceData]


def _parse_text_file(path: Path, feedback: str | None = None) -> InvoiceData:
    """
    Parse a plain-text invoice file.

    :param path: Path to the text file.
    :param feedback: Optional feedback for re-extraction.
    :return: Parsed invoice data.
    """

    text = path.read_text(encoding="utf-8")
    return _parse_unstructured_with_grok(
        text=text,
        feedback=feedback,
        fallback=lambda: _parse_text(text),
    )


def _parse_json(path: Path, feedback: str | None = None) -> InvoiceData:
    """
    Parse a JSON invoice file into the shared schema.

    :param path: Path to the JSON file.
    :param feedback: Optional feedback for re-extraction.
    :return: Parsed invoice data.
    """

    data = json.loads(path.read_text(encoding="utf-8"))
    vendor = data.get("vendor", "")
    if isinstance(vendor, dict):
        vendor = vendor.get("name") or ""

    items = [
        Item(
            name=str(row.get("item") or row.get("name") or "").strip(),
            quantity=int(row.get("quantity", 0)),
            unit_price=_to_float(row.get("unit_price")),
            line_total=_to_float(row.get("amount")),
        )
        for row in data.get("line_items", [])
    ]

    return InvoiceData(
        invoice_id=str(data.get("invoice_number") or data.get("invoice_id") or "").strip(),
        vendor=str(vendor).strip(),
        amount=_to_float(data.get("total")) or 0.0,
        items=items,
        subtotal=_to_float(data.get("subtotal")),
        tax_rate=_to_float(data.get("tax_rate")),
        tax_amount=_to_float(data.get("tax_amount")),
        due_date=data.get("due_date"),
        invoice_date=data.get("date"),
        currency=data.get("currency") or "USD",
    )


def _parse_csv(path: Path, feedback: str | None = None) -> InvoiceData:
    """
    Parse either supported CSV invoice layout.

    :param path: Path to the CSV file.
    :param feedback: Optional feedback for re-extraction.
    :return: Parsed invoice data.
    """

    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    if not rows:
        raise ValueError(f"CSV invoice has no rows: {path}")

    if set(rows[0].keys()) == {"field", "value"}:
        return _parse_field_value_csv(rows)
    return _parse_tabular_csv(rows)


def _parse_field_value_csv(rows: list[dict[str, str]]) -> InvoiceData:
    """
    Parse key-value CSV rows into invoice data.

    :param rows: CSV rows from the field-value layout.
    :return: Parsed invoice data.
    """

    fields: dict[str, Any] = {}
    items: list[Item] = []
    pending_item: dict[str, Any] = {}

    for row in rows:
        field = (row.get("field") or "").strip().lower()
        value = (row.get("value") or "").strip()

        if field == "item":
            if pending_item:
                items.append(_item_from_pending(pending_item))
            pending_item = {"name": value}
        elif field == "quantity":
            pending_item["quantity"] = _to_int(value)
        elif field == "unit_price":
            pending_item["unit_price"] = _to_float(value)
        elif field in {"invoice_number", "vendor", "date", "due_date", "subtotal", "tax", "tax_amount", "total"}:
            fields[field] = value

    if pending_item:
        items.append(_item_from_pending(pending_item))

    return InvoiceData(
        invoice_id=fields.get("invoice_number", ""),
        vendor=fields.get("vendor", ""),
        amount=_to_float(fields.get("total")) or 0.0,
        items=items,
        subtotal=_to_float(fields.get("subtotal")),
        tax_amount=_to_float(fields.get("tax_amount") or fields.get("tax")),
        due_date=fields.get("due_date"),
        invoice_date=fields.get("date"),
    )


def _parse_tabular_csv(rows: list[dict[str, str]]) -> InvoiceData:
    """
    Parse tabular CSV rows with invoice line items.

    :param rows: CSV rows from the tabular layout.
    :return: Parsed invoice data.
    """

    first_invoice_row = next((row for row in rows if row.get("Invoice Number")), rows[0])
    items: list[Item] = []
    subtotal = None
    tax_amount = None
    total = 0.0

    for row in rows:
        item_name = (row.get("Item") or "").strip()
        if item_name:
            items.append(
                Item(
                    name=item_name,
                    quantity=_to_int(row.get("Qty")),
                    unit_price=_to_float(row.get("Unit Price")),
                    line_total=_to_float(row.get("Line Total")),
                )
            )
            continue

        label = (row.get("Unit Price") or "").strip().lower()
        if label.startswith("subtotal"):
            subtotal = _to_float(row.get("Line Total"))
            continue
        if label.startswith("tax"):
            tax_amount = _to_float(row.get("Line Total"))
            continue
        if label.startswith("total"):
            total = _to_float(row.get("Line Total")) or 0.0

    return InvoiceData(
        invoice_id=(first_invoice_row.get("Invoice Number") or "").strip(),
        vendor=(first_invoice_row.get("Vendor") or "").strip(),
        amount=total,
        items=items,
        subtotal=subtotal,
        tax_amount=tax_amount,
        due_date=(first_invoice_row.get("Due Date") or "").strip() or None,
        invoice_date=(first_invoice_row.get("Date") or "").strip() or None,
    )


def _parse_xml(path: Path, feedback: str | None = None) -> InvoiceData:
    """
    Parse an XML invoice file.

    :param path: Path to the XML file.
    :param feedback: Optional feedback for re-extraction.
    :return: Parsed invoice data.
    """

    root = ET.fromstring(path.read_text(encoding="utf-8"))
    header = root.find("header")
    totals = root.find("totals")
    items: list[Item] = []

    for item in root.findall("./line_items/item"):
        items.append(
            Item(
                name=_text(item, "name"),
                quantity=_to_int(_text(item, "quantity")),
                unit_price=_to_float(_text(item, "unit_price")),
            )
        )

    return InvoiceData(
        invoice_id=_text(header, "invoice_number"),
        vendor=_text(header, "vendor"),
        amount=_to_float(_text(totals, "total")) or 0.0,
        items=items,
        subtotal=_to_float(_text(totals, "subtotal")),
        tax_rate=_to_float(_text(totals, "tax_rate")),
        tax_amount=_to_float(_text(totals, "tax_amount")),
        due_date=_text(header, "due_date") or None,
        invoice_date=_text(header, "date") or None,
        currency=_text(header, "currency") or "USD",
    )


def _parse_pdf(path: Path, feedback: str | None = None) -> InvoiceData:
    """
    Extract text from a PDF invoice and parse it.

    :param path: Path to the PDF file.
    :param feedback: Optional feedback for re-extraction.
    :return: Parsed invoice data.
    """

    text = _extract_pdf_text(path)
    if not text.strip():
        raise ValueError(f"No text could be extracted from PDF: {path}")

    deterministic_invoice = _parse_text(text)
    if not feedback and _is_clean_pdf_parse(deterministic_invoice):
        invoice = deterministic_invoice
        invoice.extraction_confidence = 0.95
    else:
        invoice = _parse_unstructured_with_grok(
            text=text,
            feedback=feedback,
            fallback=lambda: deterministic_invoice,
        )
    invoice.extraction_warnings.append("Extracted from PDF text layer.")
    return invoice


def _extract_pdf_text(path: Path) -> str:
    """
    Extract text from a PDF using available local libraries.

    :param path: Path to the PDF file.
    :return: Extracted text content.
    """

    try:
        from markitdown import MarkItDown

        converter = MarkItDown(enable_plugins=False)
        if hasattr(converter, "convert_local"):
            result = converter.convert_local(str(path))
        else:
            result = converter.convert(str(path))
        text = getattr(result, "text_content", None) or getattr(result, "markdown", None)
        if text:
            return text
    except ImportError as exc:
        markitdown_exc = exc
    else:
        markitdown_exc = None

    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError as exc:
        if markitdown_exc is not None:
            raise RuntimeError("Install markitdown[pdf] or pdfplumber to ingest PDF invoices.") from markitdown_exc
        raise RuntimeError("Install markitdown[pdf] or pdfplumber to ingest PDF invoices.") from exc


def _is_clean_pdf_parse(invoice: InvoiceData) -> bool:
    """
    Check whether deterministic PDF parsing looks complete.

    :param invoice: Parsed invoice data.
    :return: ``True`` when the parse looks complete, otherwise ``False``.
    """

    return all(
        [
            bool(invoice.invoice_id.strip()),
            bool(invoice.vendor.strip()),
            invoice.amount > 0,
            bool(invoice.items),
            bool(invoice.due_date),
            not invoice.extraction_warnings,
        ]
    )



def _parse_unstructured_with_grok(
    text: str,
    feedback: str | None,
    fallback: Callable[[], InvoiceData],
) -> InvoiceData:
    """
    Use Grok to parse messy invoice text with a fallback parser.

    :param text: Raw invoice text to parse.
    :param feedback: Optional feedback for re-extraction.
    :param fallback: Deterministic fallback parser.
    :return: Parsed invoice data.
    """

    grok = get_grok_api()
    messages = _grok_extraction_messages(text, feedback)

    try:
        result = grok.with_structured_output(InvoiceData).invoke(messages)
        return _invoice_from_grok_result(result)
    except (AttributeError, NotImplementedError, TypeError, ValueError, ValidationError):
        pass

    response = grok.invoke(messages)
    parsed = _extract_json_object(response.content)
    if parsed:
        try:
            return _invoice_from_grok_result(parsed)
        except (TypeError, ValueError, ValidationError):
            pass

    invoice = fallback()
    invoice.extraction_warnings.append("Structured extraction returned invalid output; used deterministic parser fallback.")
    return invoice


def _grok_extraction_messages(text: str, feedback: str | None) -> list[object]:
    """
    Build extraction prompts for the ingestion model.

    :param text: Raw invoice text to parse.
    :param feedback: Optional feedback for re-extraction.
    :return: Prompt messages for Grok.
    """

    feedback_text = feedback or "No prior feedback."
    return [
        SystemMessage(
            content=(
                "You are an invoice ingestion agent. Extract normalized invoice data from messy "
                "or unstructured text, including typos and ambiguous labels. Return only JSON "
                "matching the InvoiceData schema with invoice_id, vendor, amount, items, due_date, "
                "invoice_date, currency, extraction_confidence, and extraction_warnings. "
                "Use null for unknown optional fields and include warnings for missing or uncertain fields."
            )
        ),
        HumanMessage(
            content=json.dumps(
                {
                    "overseer_feedback": feedback_text,
                    "raw_invoice_text": text,
                },
                indent=2,
            )
        ),
    ]


def _invoice_from_grok_result(result: Any) -> InvoiceData:
    """
    Normalize structured Grok output into ``InvoiceData``.

    :param result: Model output to normalize.
    :return: Validated invoice data.
    """

    if isinstance(result, InvoiceData):
        return result
    if isinstance(result, dict):
        return InvoiceData.model_validate(result)
    return InvoiceData.model_validate_json(str(result))


def _parse_text(text: str) -> InvoiceData:
    """
    Parse invoice details from unstructured plain text.

    :param text: Raw invoice text to parse.
    :return: Parsed invoice data.
    """

    warnings: list[str] = []
    invoice_id = _first_match(
        text,
        r"Invoice Number:\s*(INV-\d+)",
        r"INVOICE\s+#\s*(INV-\d+)",
        r"Invoice:\s*(INV-\d+)",
        r"Inv\s*#:\s*(?:INV-)?(\d+)",
    )
    if invoice_id and invoice_id.isdigit():
        invoice_id = f"INV-{invoice_id}"

    vendor = _first_match(text, r"Vendor:\s*(.+)", r"Vndr:\s*(.+)")
    due_date = _first_match(text, r"Due Date:\s*(.+)", r"Due Dt:\s*(.+)", r"Due:\s*(.+)")
    invoice_date = _first_match(text, r"^Date:\s*(.+)", r"^Dt:\s*(.+)", flags=re.MULTILINE)
    amount_text = _first_match(text, r"Total Amount:\s*([$\d,.-]+)", r"\bTOTAL:\s*([$\d,.-]+)", r"\bTotal:\s*([$\d,.-]+)", r"\bAmt:\s*([$\d,.-]+)")
    subtotal_text = _first_match(text, r"Subtotal:\s*([$\d,.-]+)")
    tax_amount_text = _first_match(text, r"Tax\s*(?:\([^)]*\))?:\s*([$\d,.-]+)")
    tax_rate_text = _first_match(text, r"Tax\s*\(([\d.]+)%\):")
    amount = _to_float(amount_text) or 0.0

    items = _parse_text_items(text)
    if not items:
        warnings.append("No line items were detected by the deterministic text parser.")
    if not invoice_id:
        warnings.append("Invoice ID was not detected.")
    if not vendor:
        warnings.append("Vendor was not detected.")

    return InvoiceData(
        invoice_id=invoice_id or "",
        vendor=vendor or "",
        amount=amount,
        items=items,
        subtotal=_to_float(subtotal_text),
        tax_rate=_percent_to_decimal(tax_rate_text),
        tax_amount=_to_float(tax_amount_text),
        due_date=due_date,
        invoice_date=invoice_date,
        extraction_warnings=warnings,
    )


def _parse_text_items(text: str) -> list[Item]:
    """
    Find line items in plain-text invoice content.

    :param text: Raw invoice text to scan.
    :return: Parsed line items.
    """

    items: list[Item] = []

    patterns = [
        re.compile(rf"^\s*(?P<name>[A-Za-z][\w ]*?)\s+qty:\s*(?P<qty>-?\d+)(?:\s+unit price:\s*\$(?P<price>{PRICE_PATTERN}))?", re.IGNORECASE),
        re.compile(rf"^\s*(?P<name>[A-Za-z][\w ]*?)\s+qty\s+(?P<qty>-?\d+)\s+@\s*\$(?P<price>{PRICE_PATTERN})", re.IGNORECASE),
        re.compile(rf"^\s*-\s*(?P<name>[A-Za-z][\w ]*?)\s+x(?P<qty>-?\d+)\s+\$(?P<price>{PRICE_PATTERN})", re.IGNORECASE),
        re.compile(rf"^\s*(?P<name>[A-Za-z][\w ]*(?:\s+\([^)]+\))?)\s+(?P<qty>-?\d+)\s+\$(?P<price>{PRICE_PATTERN})\s+\$(?P<line_total>{PRICE_PATTERN})", re.IGNORECASE),
    ]

    for line in text.splitlines():
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                name = re.sub(r"\s+\([^)]+\)", "", match.group("name")).strip()
                items.append(
                    Item(
                        name=name,
                        quantity=_to_int(match.group("qty")),
                        unit_price=_to_float(match.group("price")),
                        line_total=_to_float(match.groupdict().get("line_total")),
                    )
                )
                break
    return items


def _item_from_pending(pending: dict[str, Any]) -> Item:
    """
    Build an item from partially collected CSV fields.

    :param pending: Partially collected item values.
    :return: Completed invoice item.
    """

    return Item(
        name=str(pending.get("name") or "").strip(),
        quantity=_to_int(pending.get("quantity")),
        unit_price=_to_float(pending.get("unit_price")),
    )


def _text(node: ET.Element | None, child: str) -> str:
    """
    Read and trim text from an XML child node.

    :param node: Parent XML element.
    :param child: Child element name to read.
    :return: Trimmed child text, or an empty string.
    """

    if node is None:
        return ""
    found = node.find(child)
    return (found.text or "").strip() if found is not None else ""


def _first_match(text: str, *patterns: str, flags: int = 0) -> str | None:
    """
    Return the first regex capture that matches the text.

    :param text: Text to search.
    :param patterns: Regex patterns to try in order.
    :param flags: Extra regex flags.
    :return: First captured match, or ``None``.
    """

    for pattern in patterns:
        match = re.search(pattern, text, flags | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _to_int(value: Any) -> int:
    """
    Convert loose numeric input into an integer.

    :param value: Value to convert.
    :return: Integer version of the value.
    """

    if value is None or value == "":
        return 0
    return int(float(str(value).replace(",", "").strip()))


def _to_float(value: Any) -> float | None:
    """
    Convert loose currency-like input into a float.

    :param value: Value to convert.
    :return: Float version of the value, or ``None``.
    """

    if value is None or value == "":
        return None
    match = MONEY_RE.search(str(value).replace(",", "").strip())
    if not match:
        return None
    amount = float(match.group("amount").replace(",", ""))
    return -amount if match.group("sign") else amount


def _percent_to_decimal(value: Any) -> float | None:
    """
    Convert a percent value into decimal form.

    :param value: Percent-like value to convert.
    :return: Decimal value, or ``None``.
    """

    raw = _to_float(value)
    if raw is None:
        return None
    return raw / 100.0


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

PARSERS: dict[str, Parser] = {
    ".json": _parse_json,
    ".csv": _parse_csv,
    ".xml": _parse_xml,
    ".txt": _parse_text_file,
    ".pdf": _parse_pdf,
}
