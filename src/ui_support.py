"""Helpers for Streamlit invoice file handling."""

from pathlib import Path
import tempfile

from src.tools import PROJECT_ROOT


SUPPORTED_INVOICE_EXTENSIONS = {".pdf", ".txt", ".csv", ".json", ".xml"}
SAMPLE_INVOICE_DIR = PROJECT_ROOT / "data" / "invoices"


def list_sample_invoices() -> list[str]:
    """List bundled sample invoice filenames.

    :return: Sorted sample invoice filenames.
    """

    if not SAMPLE_INVOICE_DIR.exists():
        return []
    return sorted(
        path.name
        for path in SAMPLE_INVOICE_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_INVOICE_EXTENSIONS
    )


def resolve_sample_invoice(filename: str) -> Path:
    """Resolve a bundled sample invoice safely.

    :param filename: Sample invoice filename.
    :return: Absolute path to the sample invoice.
    :raises ValueError: If the file type or location is invalid.
    :raises FileNotFoundError: If the sample file does not exist.
    """

    candidate = (SAMPLE_INVOICE_DIR / filename).resolve()
    if candidate.suffix.lower() not in SUPPORTED_INVOICE_EXTENSIONS:
        raise ValueError(f"Unsupported invoice format: {candidate.suffix.lower()}")
    if SAMPLE_INVOICE_DIR.resolve() not in candidate.parents:
        raise ValueError("Sample invoice must be inside the sample invoice directory.")
    if not candidate.exists():
        raise FileNotFoundError(f"Sample invoice does not exist: {candidate}")
    return candidate


def save_uploaded_invoice(filename: str, content: bytes) -> Path:
    """Save an uploaded invoice into a temporary session directory.

    :param filename: Original uploaded filename.
    :param content: Uploaded file contents.
    :return: Temporary path to the saved upload.
    :raises ValueError: If the file type is not supported.
    """

    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_INVOICE_EXTENSIONS:
        raise ValueError(f"Unsupported invoice format: {extension}")

    temp_dir = Path(tempfile.mkdtemp(prefix="galatiq-invoice-"))
    safe_name = Path(filename).name or f"uploaded-invoice{extension}"
    destination = temp_dir / safe_name
    destination.write_bytes(content)
    return destination
