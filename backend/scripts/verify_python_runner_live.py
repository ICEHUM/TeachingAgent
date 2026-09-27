"""Verify Python Runner through the local DEMO business API."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import uuid
from urllib.parse import urlparse

import httpx
import verify_python_basics_live as acceptance
from verify_python_basics_live import (
    DEMO_IDS,
    AttemptWorkspaceManager,
    local_urls,
    request,
    run_sample,
    seed_acceptance_attempts,
)

from app.agent.runner import DEFAULT_PYTHON_RUNNER_IMAGE

logging.disable(logging.CRITICAL)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-stage02b-config", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--suite", choices=("temporary", "official"), default="temporary")
    args = parser.parse_args()
    if not args.local_stage02b_config:
        raise RuntimeError("Explicit --local-stage02b-config is required")
    parsed = urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 8001:
        raise RuntimeError("Runner acceptance must target the loopback port 8001")
    acceptance.NAMESPACE = uuid.UUID(
        "c3677719-712b-42b1-911d-20633958c458" if args.suite == "official"
        else "7892041e-7499-4259-81e5-b803339863fd"
    )
    acceptance.SAMPLE_EMAIL_PREFIX = (
        "pyb01-runner-official-acceptance" if args.suite == "official"
        else "pyb01-runner-acceptance"
    )
    acceptance.SAMPLES = {
        key: ("Runner 验收｜" + name.split("｜", 1)[-1], source)
        for key, (name, source) in acceptance.SAMPLES.items()
    }
    identities = seed_acceptance_attempts(local_urls()[0])
    with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
        results = {}
        for key, (user_id, attempt_id) in identities.items():
            results[key] = run_sample(client, key, user_id, attempt_id)
            detail = request(client, "GET", f"/api/product/teacher/attempts/{attempt_id}", DEMO_IDS["teacher"])
            execution = [item for item in detail["requirements"] if item["key"].startswith("addition_")]
            assert execution and all(item["evaluator"].startswith("docker_runner:") for item in execution)
            assert any(item["label"] == "Python Runner 运行证据" for item in detail["agent_trace"])
            if args.suite == "official":
                evidence_dir = AttemptWorkspaceManager().evidence_directory(attempt_id)
                for item in execution:
                    digest = hashlib.sha256(item["operation_id"].encode()).hexdigest()
                    manifest = json.loads((evidence_dir / f"{digest}.artifact.json").read_text(encoding="utf-8"))
                    assert manifest["details"]["container_security"]["image_ref"] == DEFAULT_PYTHON_RUNNER_IMAGE
            results[key]["executor"] = "docker_runner"
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
