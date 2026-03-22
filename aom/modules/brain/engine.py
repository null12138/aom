from __future__ import annotations
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional
import requests
from .knowledge import OPERATORS_REFERENCE

logger = logging.getLogger("AlphaGenerator")

class AlphaGenerator:
    """通用 Alpha 生成器，支持 Gemini 和 OpenAI 兼容接口"""
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.gemini_key = config.get("gemini_api_key")
        self.openai_key = config.get("openai_api_key")
        self.openai_base = config.get("openai_api_base", "https://api.openai.com/v1")
        self.openai_model = config.get("openai_model", "gpt-4-turbo")
        self.llm_request_timeout = self._safe_float(config.get("llm_request_timeout", 180), 180.0)
        self.llm_max_retries = max(1, self._safe_int(config.get("llm_max_retries", 3), 3))
        self.llm_retry_backoff = max(0.1, self._safe_float(config.get("llm_retry_backoff", 1.5), 1.5))

    def generate_alphas(
        self, 
        fields: List[Dict[str, Any]], 
        report_text: Optional[str] = None, 
        patterns: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
        operators: Optional[List[Dict[str, Any]]] = None,
        count: int = 5
    ) -> List[Dict[str, Any]]:
        
        prompt = self._build_prompt(fields, report_text, patterns, context, operators, count)
        
        # 优先尝试 OpenAI (如果配置了)
        if self.openai_key:
            try:
                return self._call_openai(prompt)
            except Exception as exc:
                if self.gemini_key:
                    logger.warning("OpenAI-compatible API failed, fallback to Gemini: %s", exc)
                    return self._call_gemini(prompt)
                raise
        # 否则尝试 Gemini
        elif self.gemini_key:
            return self._call_gemini(prompt)
        else:
            raise ValueError("No LLM API keys (Gemini or OpenAI) configured.")

    def _call_gemini(self, prompt: str) -> List[Dict[str, Any]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}
        }
        resp = self._post_with_retry(url=url, payload=payload, provider_name="Gemini")
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API error: {resp.status_code} {resp.text}")
        return self._parse_json_response(resp.json()["candidates"][0]["content"]["parts"][0]["text"])

    def _call_openai(self, prompt: str) -> List[Dict[str, Any]]:
        url = f"{self.openai_base}/chat/completions"
        headers = {"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.openai_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "response_format": { "type": "json_object" }
        }
        resp = self._post_with_retry(url=url, headers=headers, payload=payload, provider_name="OpenAI-compatible")
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI API error: {resp.status_code} {resp.text}")
        return self._parse_json_response(resp.json()["choices"][0]["message"]["content"])

    def _post_with_retry(
        self,
        url: str,
        payload: Dict[str, Any],
        provider_name: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        transient_codes = {408, 409, 425, 429, 500, 502, 503, 504}
        last_error: Optional[Exception] = None
        for attempt in range(1, self.llm_max_retries + 1):
            try:
                # 分离 connect/read timeout，避免连接阶段拖太久
                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=(10, self.llm_request_timeout),
                )
                if resp.status_code in transient_codes and attempt < self.llm_max_retries:
                    sleep_seconds = self._retry_delay(attempt, resp.headers.get("Retry-After"))
                    logger.warning(
                        "%s transient status=%s (attempt %d/%d), retrying in %.2fs",
                        provider_name,
                        resp.status_code,
                        attempt,
                        self.llm_max_retries,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                    continue
                return resp
            except requests.Timeout as exc:
                last_error = exc
                if attempt >= self.llm_max_retries:
                    break
                sleep_seconds = self._retry_delay(attempt)
                logger.warning(
                    "%s timeout on attempt %d/%d, retrying in %.2fs: %s",
                    provider_name,
                    attempt,
                    self.llm_max_retries,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.llm_max_retries:
                    break
                sleep_seconds = self._retry_delay(attempt)
                logger.warning(
                    "%s request error on attempt %d/%d, retrying in %.2fs: %s",
                    provider_name,
                    attempt,
                    self.llm_max_retries,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)

        raise RuntimeError(
            f"{provider_name} request failed after {self.llm_max_retries} attempts "
            f"(read timeout={self.llm_request_timeout}s): {last_error}"
        )

    def _retry_delay(self, attempt: int, retry_after: Optional[str] = None) -> float:
        if retry_after:
            try:
                return max(0.1, float(retry_after))
            except ValueError:
                pass
        base = self.llm_retry_backoff * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, 0.3)
        return base + jitter

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _parse_json_response(self, text: str) -> List[Dict[str, Any]]:
        try:
            # 清理 Markdown 块
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].strip()
            
            result = json.loads(text)
            return result.get("alphas", []) if isinstance(result, dict) else result
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error(f"Response parsing failed: {e}\nText: {text}")
            raise RuntimeError(f"AI response parsing failed: {e}")

    def _build_prompt(self, fields: List[Dict[str, Any]], report_text: Optional[str], patterns: Optional[List[Dict[str, Any]]], context: Optional[Dict[str, Any]], operators: Optional[List[Dict[str, Any]]], count: int) -> str:
        fields_str = "\n".join([f"- {f['id']}: {f.get('description', '')} (Type: {f.get('type', '')})" for f in fields])
        single_dataset_only = bool(context.get("single_dataset_only")) if isinstance(context, dict) else False
        mutation_mode = str(context.get("mutation_mode") or "").lower() if isinstance(context, dict) else ""
        max_operator_calls = int(context.get("max_operator_calls", 8)) if isinstance(context, dict) else 8
        
        if operators:
            ops_str = "Available FastExpr Operators (Dynamic from API):\n"
            # Keep enough operators/signatures for arity guidance while controlling prompt size.
            op_limit = 180
            for op in operators[:op_limit]:
                ops_str += f"- {op['name']}: {op.get('description', '')} (Sign: {op.get('definition', '')})\n"
        else:
            ops_str = "Available Core Operators (FastExpr Reference):\n"
            for category, ops in OPERATORS_REFERENCE.items():
                ops_str += f"- {category}: {', '.join(ops)}\n"

        ctx_str = ""
        if context:
            ctx_str = "Current Market Context:\n" + "\n".join([f"- {k}: {v}" for k, v in context.items()])

        patterns_str = ""
        if patterns:
            patterns_str = "Reference Patterns:\n" + "\n".join([f"- {p['name']}: {p['template']}" for p in patterns])

        report_str = f"Research Highlights:\n{report_text}\n" if report_text else ""

        extra_rules = []
        if mutation_mode == "max":
            extra_rules.append("8. Mutation Mode MAX: force large operator-tree differences between candidates.")
        if single_dataset_only:
            extra_rules.append("9. Single Dataset ONLY: every expression must stay within one dataset family.")
        extra_rules_str = "\n".join(extra_rules)

        return f"""You are a Quantitative Analyst for WorldQuant Brain. 
Generate {count} Alphas for the following market and datasets:

{ctx_str}

{ops_str}

{report_str}
{patterns_str}

Available Data Fields:
{fields_str}

STRICT REQUIREMENTS:
1. Syntax: MUST use WorldQuant Brain FastExpr syntax (e.g., ts_rank, ts_delta, ts_av, group_rank).
2. Format: Output valid JSON object with key 'alphas'. Each alpha must have 'name', 'expression', and 'logic'.
3. Regional Compliance: Tailor the logic and operators to the current region ({context.get('region') if context else 'Global'}).
4. Data Types: Respect MATRIX vs GROUP data types for fields.
5. Operator Arity: STRICTLY follow each operator Sign/signature input count from the operator list. Never guess argument count.
6. Mutation Diversity: maximize structural mutation between alphas (different operator families/lookbacks/transforms), avoid near-duplicates.
7. Dataset Constraint: each expression may only use fields from one dataset family; do not mix fields across different datasets.
8. Field Scope: only use fields listed in "Available Data Fields".
9. Operator Budget: each expression must use at most {max_operator_calls} operator calls.
{extra_rules_str}

Example:
{{
  "alphas": [
    {{
      "name": "Intraday Momentum Filter",
      "expression": "ts_rank(returns, 10) * group_rank(open, industry)",
      "logic": "Combines cross-sectional momentum with industry relative value."
    }}
  ]
}}

NOW, generate {count} alphas:"""
