import argparse
import json
import sys
from pathlib import Path

from src.presentation import error_hint, format_cli_output
from src.services.workflow_runner import run_invoice_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Process an invoice through the LangGraph agent pipeline.")
    parser.add_argument("--invoice_path", required=True, help="Path to a single invoice file.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full JSON agent state instead of the human-friendly summary.",
    )
    args = parser.parse_args()

    invoice_path = str(Path(args.invoice_path))
    try:
        final_state = run_invoice_workflow(invoice_path)
        if args.verbose:
            print(final_state.model_dump_json(indent=2))
        else:
            print(format_cli_output(final_state))
    except Exception as exc:
        print(
            json.dumps(
                    {
                        "status": "error",
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                        "invoice_path": invoice_path,
                        "hint": error_hint(exc),
                    },
                    indent=2,
                )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
