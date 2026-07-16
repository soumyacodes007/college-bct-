"""Single OpenRouter client for all LLM calls, replacing separate OpenAI/Claude SDK clients.

OpenRouter is OpenAI-SDK compatible (just a base_url swap), and routes to any provider's models
through one API key -- so "try Claude, fall back to OpenAI" becomes a model-string fallback instead
of maintaining two client objects and two request-shaping code paths.
"""

import os
import time

from dotenv import load_dotenv
from langsmith import traceable
from openai import OpenAI

load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "anthropic/claude-sonnet-4.5")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "openai/gpt-4.1")


def get_client() -> OpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set in the environment")
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


@traceable(name="openrouter_call", run_type="llm")
def call_llm(client: OpenAI, system: str, prompt: str, max_tokens: int = 16000, max_retries: int = 3) -> str:
    """Calls PRIMARY_MODEL, falling back to FALLBACK_MODEL on repeated failure.

    Returns the raw response text. Raises on total failure so callers can decide how to handle it.
    """
    last_error: Exception | None = None

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    extra_headers={
                        "HTTP-Referer": "https://github.com/conceptreel",
                        "X-Title": "Conceptreel",
                    },
                )
                content = response.choices[0].message.content
                if content:
                    return content.strip()
                last_error = Exception(
                    f"Empty content from {model}, finish_reason={response.choices[0].finish_reason}"
                )
            except Exception as e:
                last_error = e
                print(f"[llm_client] {model} attempt {attempt + 1}/{max_retries} failed: {e}")

            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

        print(f"[llm_client] Exhausted retries on {model}, moving to fallback" if model == PRIMARY_MODEL else "")

    raise Exception(f"All models failed. Last error: {last_error}")
