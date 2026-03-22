from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Tuple

try:
    import tomllib as toml
except ModuleNotFoundError:  # pragma: no cover - py310 fallback
    import tomli as toml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "aom.toml"


class ConfigError(RuntimeError):
    pass


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"1", "true", "yes", "on"}:
            return True
        if lower in {"0", "false", "no", "off"}:
            return False
    return None


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    brain = cfg.setdefault("brain", {})
    username = os.getenv("AOM_BRAIN_USERNAME")
    password = os.getenv("AOM_BRAIN_PASSWORD")
    api_base = os.getenv("AOM_BRAIN_API_BASE")
    use_proxy = os.getenv("AOM_BRAIN_USE_PROXY")
    if use_proxy is None:
        use_proxy = os.getenv("AOM_USE_PROXY")
    gemini_key = os.getenv("AOM_GEMINI_API_KEY")
    openai_key = os.getenv("AOM_OPENAI_API_KEY")
    openai_base = os.getenv("AOM_OPENAI_API_BASE")
    openai_model = os.getenv("AOM_OPENAI_MODEL")
    llm_use_proxy = os.getenv("AOM_LLM_USE_PROXY")
    llm_request_timeout = os.getenv("AOM_LLM_REQUEST_TIMEOUT")
    llm_max_retries = os.getenv("AOM_LLM_MAX_RETRIES")
    llm_retry_backoff = os.getenv("AOM_LLM_RETRY_BACKOFF")

    if username:
        brain["username"] = username
    if password:
        brain["password"] = password
    if api_base:
        brain["api_base"] = api_base
    parsed_use_proxy = _parse_bool(use_proxy)
    if parsed_use_proxy is not None:
        brain["use_proxy"] = parsed_use_proxy
    if gemini_key:
        brain["gemini_api_key"] = gemini_key
    if openai_key:
        brain["openai_api_key"] = openai_key
    if openai_base:
        brain["openai_api_base"] = openai_base
    if openai_model:
        brain["openai_model"] = openai_model
    parsed_llm_use_proxy = _parse_bool(llm_use_proxy)
    if parsed_llm_use_proxy is not None:
        brain["llm_use_proxy"] = parsed_llm_use_proxy
    if llm_request_timeout:
        brain["llm_request_timeout"] = llm_request_timeout
    if llm_max_retries:
        brain["llm_max_retries"] = llm_max_retries
    if llm_retry_backoff:
        brain["llm_retry_backoff"] = llm_retry_backoff

    return cfg


def load_config(config_path: Path | None = None) -> Tuple[dict[str, Any], Path]:
    env_path = os.getenv("AOM_CONFIG")
    path = Path(env_path) if env_path else (config_path or DEFAULT_CONFIG_PATH)

    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    data = toml.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError("Config file must contain a TOML table")

    return _apply_env_overrides(data), path


def mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
    masked = {**cfg}
    brain = dict(masked.get("brain", {}))
    if "password" in brain and brain["password"]:
        brain["password"] = "***"
    masked["brain"] = brain
    return masked
