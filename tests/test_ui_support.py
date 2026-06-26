from pathlib import Path

import pytest

from src.ui_support import (
    SAMPLE_INVOICE_DIR,
    SUPPORTED_INVOICE_EXTENSIONS,
    list_sample_invoices,
    resolve_sample_invoice,
    save_uploaded_invoice,
)


def test_list_sample_invoices_returns_supported_files() -> None:
    samples = list_sample_invoices()

    assert samples
    assert all(Path(name).suffix.lower() in SUPPORTED_INVOICE_EXTENSIONS for name in samples)


def test_resolve_sample_invoice_returns_existing_file() -> None:
    sample_name = list_sample_invoices()[0]

    sample_path = resolve_sample_invoice(sample_name)

    assert sample_path.exists()
    assert SAMPLE_INVOICE_DIR.resolve() in sample_path.parents


def test_resolve_sample_invoice_rejects_path_traversal() -> None:
    with pytest.raises(ValueError):
        resolve_sample_invoice("..\\README.md")


def test_save_uploaded_invoice_writes_temp_file() -> None:
    saved_path = save_uploaded_invoice("invoice_upload.txt", b"invoice body")

    assert saved_path.exists()
    assert saved_path.read_bytes() == b"invoice body"
    assert saved_path.name == "invoice_upload.txt"


def test_save_uploaded_invoice_rejects_unsupported_extension() -> None:
    with pytest.raises(ValueError):
        save_uploaded_invoice("invoice.exe", b"bad")
