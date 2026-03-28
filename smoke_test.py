"""Quick real-call smoke test for Gemini and OpenAI providers.
Run with: uv run python smoke_test.py
(env vars must be set from .env first)
"""
import asyncio
import os
import sys
from pathlib import Path


def load_dotenv(path: str) -> None:
    """Minimal .env loader — no dependency on python-dotenv."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


load_dotenv(str(Path(__file__).parent / ".env"))


async def test_gemini() -> None:
    from repowise.core.providers.llm.gemini import GeminiProvider
    p = GeminiProvider()
    print(f"  key: {p._api_key[:10]}...")
    r = await p.generate(
        "You are a helpful assistant.",
        "Reply with exactly the word: GEMINI_OK",
    )
    print(f"  response: {repr(r.content.strip())}")
    print(f"  tokens: in={r.input_tokens} out={r.output_tokens}")
    assert r.content.strip(), "Empty response"


async def test_openai() -> None:
    from repowise.core.providers.llm.openai import OpenAIProvider
    key = os.environ.get("OPENAI_API_KEY", "")
    p = OpenAIProvider(api_key=key, model="gpt-4.1-mini")
    print(f"  key: {key[:10]}...")
    r = await p.generate(
        "You are a helpful assistant.",
        "Reply with exactly the word: OPENAI_OK",
    )
    print(f"  response: {repr(r.content.strip())}")
    print(f"  tokens: in={r.input_tokens} out={r.output_tokens}")
    assert r.content.strip(), "Empty response"


async def main() -> None:
    results: dict[str, str] = {}

    print("=== Gemini ===")
    try:
        await test_gemini()
        results["gemini"] = "PASS"
    except Exception as e:
        results["gemini"] = f"FAIL — {e}"

    print()
    print("=== OpenAI ===")
    try:
        await test_openai()
        results["openai"] = "PASS"
    except Exception as e:
        results["openai"] = f"FAIL — {e}"

    print()
    print("=== Results ===")
    all_pass = True
    for name, status in results.items():
        icon = "✓" if status == "PASS" else "✗"
        print(f"  {icon} {name}: {status}")
        if status != "PASS":
            all_pass = False

    sys.exit(0 if all_pass else 1)


asyncio.run(main())
