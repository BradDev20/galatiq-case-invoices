"""Helpers for creating the Grok client used by the agents."""

import os

from dotenv import find_dotenv, load_dotenv
from langchain_xai import ChatXAI

DEFAULT_MODEL = "grok-4.3"

def get_grok_api(model: str = DEFAULT_MODEL, temperature: float = 0.0) -> ChatXAI:
    """
    Build a configured Grok chat client from environment settings.

    :param model: Model name to send requests to.
    :param temperature: Sampling temperature for responses.
    :return: Configured Grok chat client.
    """

    load_dotenv(find_dotenv(usecwd=True), override=False)
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "XAI_API_KEY is not set in this environment. You can get one from https://console.x.ai."
        )

    return ChatXAI(
        model=model,
        temperature=temperature,
        max_tokens=2048,
        streaming=False,
    )
