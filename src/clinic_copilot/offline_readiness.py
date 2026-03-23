from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


def evaluate_offline_readiness(workspace: Path, prepull: bool = False) -> dict[str, Any]:
    env_file_values = _load_env_file(workspace)

    allow_remote_downloads = _resolve_setting("ALLOW_REMOTE_MODEL_DOWNLOADS", env_file_values, "false").lower()
    openai_base_url = _resolve_setting("OPENAI_BASE_URL", env_file_values, "")
    database_url = _resolve_setting("DATABASE_URL", env_file_values, "sqlite:///./clinic_copilot.db")

    model_keys = [
        "OLLAMA_MODEL",
        "OPENAI_MODEL",
        "LLM_STANDARD_MODEL",
        "LLM_CLINICAL_REASONING_MODEL",
        "LLM_VISION_MODEL",
    ]
    requested_models = sorted(
        {
            value
            for key in model_keys
            for value in [_resolve_setting(key, env_file_values, "")]
            if value
        }
    )

    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "remote_downloads_disabled",
            "ok": allow_remote_downloads in {"0", "false", "no"},
            "detail": f"ALLOW_REMOTE_MODEL_DOWNLOADS={allow_remote_downloads or 'unset'}",
        }
    )
    checks.append(
        {
            "name": "local_llm_base_url",
            "ok": (not openai_base_url)
            or ("127.0.0.1" in openai_base_url)
            or ("localhost" in openai_base_url)
            or ("ollama" in openai_base_url),
            "detail": f"OPENAI_BASE_URL={openai_base_url or 'unset'}",
        }
    )

    db_is_local = database_url.startswith("sqlite:///") or "localhost" in database_url or "127.0.0.1" in database_url
    db_is_internal = any(token in database_url for token in ("postgres:", "@postgres", "@db", "postgresql://clinic:"))
    db_mode = "sqlite-local" if database_url.startswith("sqlite:///") else "postgres"
    checks.append(
        {
            "name": "local_or_internal_database",
            "ok": db_is_local or db_is_internal,
            "detail": f"DATABASE_URL={database_url}",
        }
    )

    code, output = _run(["ollama", "list"])
    if code != 0:
        checks.append({"name": "ollama_available", "ok": False, "detail": output or "ollama list failed"})
        checks.append({"name": "required_models_cached", "ok": False, "detail": "ollama unavailable"})
    else:
        checks.append({"name": "ollama_available", "ok": True, "detail": "ollama list succeeded"})
        available = set()
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        for line in lines[1:]:
            model_name = line.split()[0]
            if model_name:
                available.add(model_name)

        missing = [model for model in requested_models if model not in available]
        checks.append(
            {
                "name": "required_models_cached",
                "ok": not missing,
                "detail": ("missing: " + ", ".join(missing)) if missing else "all requested models cached",
            }
        )

        if prepull and missing:
            pull_failures: list[str] = []
            for model in missing:
                pull_code, pull_out = _run(["ollama", "pull", model])
                if pull_code != 0:
                    pull_failures.append(f"{model}: {pull_out[:180]}")
            checks.append(
                {
                    "name": "prepull_models",
                    "ok": not pull_failures,
                    "detail": "pulled all missing models" if not pull_failures else "; ".join(pull_failures),
                }
            )

    ready = all(bool(item.get("ok", False)) for item in checks)
    return {
        "workspace": str(workspace),
        "requested_models": requested_models,
        "database_mode": db_mode,
        "checks": checks,
        "ready": ready,
    }


def _run(command: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return (127, "command not found")
    output = (completed.stdout or "") + (completed.stderr or "")
    return (completed.returncode, output.strip())


def _load_env_file(workspace: Path) -> dict[str, str]:
    env_path = workspace / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_setting(key: str, env_file: dict[str, str], default: str = "") -> str:
    return os.environ.get(key, env_file.get(key, default))
