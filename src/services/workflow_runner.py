"""Shared workflow runner used by the CLI and Streamlit UI."""

from pathlib import Path
from typing import Iterator

from dotenv import find_dotenv, load_dotenv

from src.graph import graph
from src.state import AgentState


load_dotenv(find_dotenv(usecwd=True), override=False)


def run_invoice_workflow(invoice_path: str) -> AgentState:
    """Run the invoice workflow for one local invoice file.

    :param invoice_path: Local path to the invoice file.
    :return: Final validated workflow state.
    """

    normalized_path = str(Path(invoice_path))
    result = graph.invoke(
        {"invoice_path": normalized_path},
        config={"configurable": {"thread_id": normalized_path}},
    )
    return AgentState.model_validate(result)


def stream_invoice_workflow(invoice_path: str) -> Iterator[AgentState]:
    """Yield workflow states as they are produced by the graph.

    :param invoice_path: Local path to the invoice file.
    :return: Iterator of validated workflow states.
    """

    normalized_path = str(Path(invoice_path))
    for state in graph.stream(
        {"invoice_path": normalized_path},
        config={"configurable": {"thread_id": normalized_path}},
        stream_mode="values",
    ):
        yield AgentState.model_validate(state)
