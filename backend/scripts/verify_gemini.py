"""Verify the configured Gemini model against the live API.

Answers, empirically rather than from documentation:

  1. does the model exist in the catalogue this API key can see?
  2. does it support text generation?
  3. does it support streaming?
  4. does a real streaming call actually work end to end?

Run it after setting GEMINI_API_KEY, and again whenever Google changes its
model line-up -- which happens often enough that a hard-coded answer in a
document goes stale. `gemini-2.0-flash` was the obvious default until it was
shut down.

    cd backend
    .venv/Scripts/python.exe -m scripts.verify_gemini

**This never prints the API key**, and never accepts one as an argument --
it reads the value the application already reads, from the environment.

Read-only: it lists models and generates a handful of tokens. It writes
nothing and changes nothing.
"""

from __future__ import annotations

import asyncio
import sys

from app.core.config import get_settings


async def main() -> int:
    settings = get_settings()

    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set in backend/.env -- nothing to verify.")
        return 2

    # Imported here, not at module scope, so the script gives a clean message
    # above rather than an ImportError when the SDK is absent.
    from google import genai
    from google.genai import errors as genai_errors

    model_id = settings.ai_model
    client = genai.Client(api_key=settings.gemini_api_key)

    print(f"Configured model: {model_id}")
    print(f"SDK: google-genai {_sdk_version()}")
    print()

    # --- 1-3: is it in the catalogue, and what can it do? -------------------
    try:
        catalogue = await client.aio.models.list()
        models = [model async for model in catalogue]
    except genai_errors.ClientError as exc:
        # 400/401/403 here almost always means the key itself is rejected.
        print(f"FAILED to list models (HTTP {exc.code}). The API key was refused.")
        return 1

    # The API returns fully-qualified names like "models/gemini-3.7-flash".
    by_id = {model.name.removeprefix("models/"): model for model in models}

    print(f"Models visible to this key: {len(by_id)}")

    streamable = sorted(
        name
        for name, model in by_id.items()
        if "generateContent" in (model.supported_actions or [])
    )
    print(f"Supporting generateContent: {len(streamable)}")
    print()

    found = by_id.get(model_id)
    if found is None:
        print(f"NOT FOUND: {model_id!r} is not in this project's catalogue.")
        print("Closest available text models:")
        for name in streamable[:15]:
            print(f"  - {name}")
        return 1

    actions = found.supported_actions or []
    print(f"FOUND    : {found.display_name or model_id}")
    print(f"  actions        : {', '.join(actions) or '(none reported)'}")
    print(f"  input  tokens  : {found.input_token_limit}")
    print(f"  output tokens  : {found.output_token_limit}")

    if "generateContent" not in actions:
        print("  -> does NOT support generateContent; cannot be used for chat.")
        return 1

    # --- 4: prove streaming actually works ---------------------------------
    print()
    print("Streaming a real reply (a few tokens)...")
    try:
        stream = await client.aio.models.generate_content_stream(
            model=model_id,
            contents="Reply with exactly: streaming works",
            config={"max_output_tokens": 32},
        )
        chunks = 0
        text = []
        async for chunk in stream:
            if getattr(chunk, "text", None):
                chunks += 1
                text.append(chunk.text)
    except genai_errors.APIError as exc:
        print(f"  FAILED (HTTP {getattr(exc, 'code', '?')}) -- streaming call rejected.")
        return 1

    print(f"  chunks received: {chunks}")
    print(f"  reply          : {''.join(text).strip()[:120]}")
    print()
    print(
        "VERIFIED: model exists, supports generateContent, and streams "
        "over the async API."
    )
    return 0


def _sdk_version() -> str:
    import importlib.metadata as metadata

    try:
        return metadata.version("google-genai")
    except metadata.PackageNotFoundError:  # pragma: no cover
        return "not installed"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
