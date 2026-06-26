from pathlib import Path
import sys
import types

import pytest

from src.agents import ingestion
from src.tools import InvoiceData
from tests.conftest import FakeLLM


def test_ingest_invoice_rejects_missing_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError):
        ingestion.ingest_invoice(missing_path)


def test_ingest_invoice_rejects_unsupported_format(tmp_path: Path) -> None:
    invoice_path = tmp_path / "invoice.unsupported"
    invoice_path.write_text("nope", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported invoice format"):
        ingestion.ingest_invoice(invoice_path)


def test_text_ingestion_falls_back_to_deterministic_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llm = FakeLLM(
        structured_results=[TypeError("structured output unavailable")],
        invoke_results=["not valid json"],
    )
    monkeypatch.setattr(ingestion, "get_grok_api", lambda: fake_llm)

    invoice = ingestion.ingest_invoice("data/invoices/invoice_1001.txt")

    assert invoice.invoice_id == "INV-1001"
    assert invoice.vendor == "Widgets Inc."
    assert invoice.amount == 5000.0
    assert [item.name for item in invoice.items] == ["WidgetA", "WidgetB"]
    assert invoice.source_path.endswith("invoice_1001.txt")
    assert any("deterministic parser fallback" in warning for warning in invoice.extraction_warnings)


def test_text_ingestion_appends_reextraction_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llm = FakeLLM(
        structured_results=[TypeError("structured output unavailable")],
        invoke_results=["still not valid json"],
    )
    monkeypatch.setattr(ingestion, "get_grok_api", lambda: fake_llm)

    invoice = ingestion.ingest_invoice(
        "data/invoices/invoice_1002.txt",
        feedback="Recover the missing vendor and invoice number.",
    )

    assert invoice.invoice_id == "INV-1002"
    assert any("Re-extraction attempted with feedback" in warning for warning in invoice.extraction_warnings)


def test_json_ingestion_parses_nested_vendor() -> None:
    invoice = ingestion.ingest_invoice("data/invoices/invoice_1009.json")

    assert invoice.invoice_id == "INV-1009"
    assert invoice.vendor == ""
    assert invoice.amount == -250.0
    assert invoice.subtotal == 1000.0
    assert invoice.tax_rate == 0.0
    assert invoice.tax_amount == 0.0
    assert invoice.currency == "USD"
    assert invoice.items[0].quantity == -5


def test_xml_ingestion_parses_structured_invoice(tmp_path: Path) -> None:
    invoice_path = tmp_path / "invoice.xml"
    invoice_path.write_text(
        """
<invoice>
  <header>
    <invoice_number>INV-XML</invoice_number>
    <vendor>XML Corp</vendor>
    <date>2026-01-05</date>
    <due_date>2026-01-31</due_date>
    <currency>USD</currency>
  </header>
  <line_items>
    <item>
      <name>WidgetA</name>
      <quantity>3</quantity>
      <unit_price>250.00</unit_price>
    </item>
  </line_items>
  <totals>
    <total>750.00</total>
  </totals>
</invoice>
""".strip(),
        encoding="utf-8",
    )

    invoice = ingestion.ingest_invoice(invoice_path)

    assert isinstance(invoice, InvoiceData)
    assert invoice.invoice_id == "INV-XML"
    assert invoice.vendor == "XML Corp"
    assert invoice.amount == 750.0
    assert invoice.items[0].name == "WidgetA"


def test_pdf_ingestion_extracts_clean_sample_with_real_pdf_text() -> None:
    invoice = ingestion.ingest_invoice("data/invoices/invoice_1011.pdf")

    assert invoice.invoice_id == "INV-1011"
    assert invoice.vendor == "Summit Manufacturing Co."
    assert invoice.amount == 3000.0
    assert invoice.due_date == "2026-02-20"
    assert [item.name for item in invoice.items] == ["WidgetA", "WidgetB"]
    assert invoice.extraction_confidence == 0.95
    assert "Extracted from PDF text layer." in invoice.extraction_warnings


def test_pdf_ingestion_falls_back_deterministically_after_invalid_llm_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeLLM(
        structured_results=[TypeError("structured output unavailable")],
        invoke_results=["not valid json"],
    )
    monkeypatch.setattr(ingestion, "get_grok_api", lambda: fake_llm)

    invoice = ingestion.ingest_invoice("data/invoices/invoice_1012.pdf")

    assert invoice.invoice_id == ""
    assert invoice.vendor == ""
    assert invoice.amount == 9975.0
    assert [item.name for item in invoice.items] == ["Widget A", "WidgetB", "Gadget X"]
    assert any("deterministic parser fallback" in warning for warning in invoice.extraction_warnings)
    assert "Extracted from PDF text layer." in invoice.extraction_warnings


def test_pdf_ingestion_raises_when_text_layer_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "empty.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(ingestion, "_extract_pdf_text", lambda _path: "")

    with pytest.raises(ValueError, match="No text could be extracted from PDF"):
        ingestion.ingest_invoice(pdf_path)


def test_pdf_text_extraction_prefers_markitdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    class FakeMarkItDown:
        def __init__(self, enable_plugins: bool = False) -> None:
            self.enable_plugins = enable_plugins

        def convert_local(self, path: str):
            assert path == str(pdf_path)
            return types.SimpleNamespace(text_content="converted markdown text")

    monkeypatch.setitem(sys.modules, "markitdown", types.SimpleNamespace(MarkItDown=FakeMarkItDown))

    text = ingestion._extract_pdf_text(pdf_path)

    assert text == "converted markdown text"
