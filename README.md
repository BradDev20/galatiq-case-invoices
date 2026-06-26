# Galatiq Invoice Processing

Local invoice review workflow with both a command-line runner and a Streamlit dashboard. The project processes one invoice at a time, validates it against local inventory data, decides whether payment can proceed, simulates payment for approved invoices, and presents a business-friendly outcome with workflow details.

## Functionality

- Accepts invoice files in PDF, TXT, CSV, JSON, and XML formats.
- Extracts normalized invoice fields such as vendor, invoice ID, dates, line items, amount, currency, and payment terms.
- Validates invoice data against the included SQLite inventory database.
- Detects business issues such as unknown items, zero stock, quantity over stock, invalid quantities, invalid amounts, missing due dates, and date inconsistencies.
- Applies approval rules for validation failures, large invoices, and payment eligibility.
- Simulates payment for approved invoices.
- Produces a final decision, business summary, next action, workflow status, detailed stage data, and logs.

## Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create a local environment file:

```powershell
Copy-Item .env.example .env
```

Set the required service key in `.env`:

```text
XAI_API_KEY=YOUR_KEY_HERE
```

The app uses `inventory.db` in the project root for inventory validation.

## Run From The CLI

Process one invoice and print a human-friendly result:

```powershell
python main.py --invoice_path path\to\invoice.txt
```

Print the full workflow state as JSON:

```powershell
python main.py --invoice_path path\to\invoice.txt --verbose
```

The CLI returns a nonzero exit code and a structured error payload if processing fails.

## Run The Streamlit Dashboard

Start the local dashboard:

```powershell
streamlit run streamlit_app.py
```

Use the sidebar to upload one supported invoice file, then select **Process Invoice**.

The dashboard shows:

- Invoice decision with vendor, amount, invoice ID, and uploaded source file.
- Business summary and next action.
- Workflow status for ingestion, validation, approval, and payment.
- Raw invoice, validation, approval, payment, and log details in tabs.

## Test

Run the full test suite:

```powershell
python -m pytest
```

Run a focused test file:

```powershell
python -m pytest tests/test_workflow_integration.py
```

Run tests with verbose output:

```powershell
python -m pytest -v
```

The tests cover ingestion, validation, approval, payment, final reporting, CLI behavior, UI support helpers, workflow orchestration, and integration scenarios.

## Project Structure

- `main.py`: CLI entry point.
- `streamlit_app.py`: Streamlit dashboard.
- `src/agents/`: ingestion, validation, approval, payment, and final reporting logic.
- `src/services/workflow_runner.py`: workflow execution helpers.
- `src/presentation.py`: CLI and UI formatting helpers.
- `src/tools.py`: shared data models and local utility functions.
- `src/state.py`: workflow state model.
- `tests/`: automated test suite.
