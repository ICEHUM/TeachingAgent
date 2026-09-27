"""Small live checks: model access, OpenHands text response, and tool-call schema."""
import contextlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx
from app.agent.llm import ModelSettings, build_llm


def main():
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "checking"}
    secret = ""
    try:
        settings = ModelSettings()
        secret = settings.llm_api_key.get_secret_value()
        base = settings.llm_base_url.rstrip("/")
        if urlsplit(base).scheme != "https" or urlsplit(base).netloc != "api.deepseek.com":
            raise ValueError("This DeepSeek verification script requires https://api.deepseek.com")
        report.update(model=settings.llm_model, base_url=base)
        with httpx.Client(base_url=base + "/", headers={"Authorization": f"Bearer {secret}"},
                          timeout=60, follow_redirects=False) as client:
            response = client.get("models")
            response.raise_for_status()
            available = [item["id"] for item in response.json().get("data", [])]
            report["model_available"] = settings.llm_model in available
            if not report["model_available"]:
                raise ValueError("Configured model not found in this account's model list.")
            print("Model access: passed", flush=True)
            # Suppress library diagnostics; report only a redacted error if the call fails.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                from openhands.sdk import Message, TextContent
                llm = build_llm(num_retries=0, max_output_tokens=128, reasoning_effort=None,
                    litellm_extra_body={"thinking": {"type": "disabled"}})
                answer = llm.completion(messages=[Message(role="user", content=[TextContent(
                    text="This is an API connection test. Reply with exactly OK.")])])
            text = "".join(getattr(part, "text", "") for part in answer.message.content).strip()
            if text != "OK":
                raise ValueError("OpenHands response did not match the expected OK marker.")
            report["openhands_text_completion"] = "passed"
            print("OpenHands text completion: passed", flush=True)
            response = client.post("chat/completions", json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": "Call runtime_ping with status OK."}],
                "thinking": {"type": "disabled"}, "max_tokens": 128,
                "tools": [{"type": "function", "function": {
                    "name": "runtime_ping", "description": "Return a connectivity marker; no side effects.",
                    "parameters": {"type": "object", "properties": {"status": {"type": "string"}},
                                   "required": ["status"], "additionalProperties": False}}}],
                "tool_choice": {"type": "function", "function": {"name": "runtime_ping"}},
            })
            response.raise_for_status()
            calls = response.json()["choices"][0]["message"].get("tool_calls", [])
            if not calls or calls[0]["function"]["name"] != "runtime_ping":
                raise ValueError("API did not return the requested tool-call structure.")
            if json.loads(calls[0]["function"]["arguments"]).get("status") != "OK":
                raise ValueError("API tool-call arguments did not match the expected marker.")
            report["api_tool_call"] = "passed"
            report["status"] = "passed"
            print("API tool-call schema: passed (no actual tool executed)", flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - report unexpected external API failures
        message = str(error)
        if secret:
            message = message.replace(secret, "[REDACTED]")
        report.update(status="failed", error_type=type(error).__name__, error=message[:500])
        if isinstance(error, httpx.HTTPStatusError):
            report["http_status"] = error.response.status_code
            try:
                detail = error.response.json().get("error", {}).get("message", "")
                report["api_error"] = str(detail).replace(secret, "[REDACTED]")[:300]
            except (ValueError, AttributeError):
                pass
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 1
    finally:
        report_path = ROOT / ".runtime" / "checks" / "model-api-check.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
