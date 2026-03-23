from __future__ import annotations
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple
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
        self.openai_model = config.get("openai_model", "gpt-5.4")
        self.llm_request_timeout = self._safe_float(config.get("llm_request_timeout", 180), 180.0)
        self.llm_max_retries = max(1, self._safe_int(config.get("llm_max_retries", 3), 3))
        self.llm_retry_backoff = max(0.1, self._safe_float(config.get("llm_retry_backoff", 1.5), 1.5))
        self.llm_use_proxy = self._safe_bool(config.get("llm_use_proxy", config.get("use_proxy", False)), False)
        self.http_session = requests.Session()
        # Default: do not inherit process proxy env vars.
        self.http_session.trust_env = self.llm_use_proxy

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

    def repair_expression(
        self,
        expression: str,
        errors: List[str],
        fields: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
        operators: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        prompt = self._build_repair_prompt(expression, errors, fields or [], context or {}, operators or [])
        text: str = ""
        if self.openai_key:
            url = f"{self.openai_base}/chat/completions"
            headers = {"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"}
            payload = {
                "model": self.openai_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }
            resp = self._post_with_retry(url=url, headers=headers, payload=payload, provider_name="OpenAI-compatible")
            if resp.status_code != 200:
                raise RuntimeError(f"OpenAI repair error: {resp.status_code} {resp.text}")
            text = str(resp.json()["choices"][0]["message"]["content"])
        elif self.gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512},
            }
            resp = self._post_with_retry(url=url, payload=payload, provider_name="Gemini")
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini repair error: {resp.status_code} {resp.text}")
            text = str(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
        else:
            return ""
        return self._parse_repair_response(text)

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
                resp = self.http_session.post(
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

    @staticmethod
    def _safe_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lower = value.strip().lower()
            if lower in {"1", "true", "yes", "on"}:
                return True
            if lower in {"0", "false", "no", "off"}:
                return False
        if value is None:
            return default
        return bool(value)

    def _infer_economic_theme_hints(self, fields: List[Dict[str, Any]], top_k: int = 5) -> List[str]:
        theme_keywords: List[Tuple[str, Tuple[str, ...]]] = [
            ("Value / Mispricing", ("value", "valuation", "pe", "pb", "ps", "ev", "ebitda", "book", "yield", "fcf")),
            ("Quality / Profitability", ("quality", "profit", "margin", "roe", "roa", "accrual", "leverage", "debt", "cashflow")),
            ("Growth / Revisions", ("growth", "delta", "change", "chg", "revision", "estimate", "surprise", "guidance")),
            ("Investment / Balance-Sheet", ("asset", "inventory", "capex", "ppe", "working_capital", "investment")),
            ("Momentum / Trend", ("momentum", "return", "close", "open", "high", "low", "vwap", "price")),
            ("Reversal / Mean-Reversion", ("reversal", "mean", "zscore", "gap", "overnight", "contrarian")),
            ("Volatility / Risk", ("vol", "volatility", "beta", "variance", "std", "drawdown", "ivol")),
            ("Liquidity / Trading Friction", ("volume", "turnover", "spread", "illiquidity", "amihud", "adv", "dollarvol", "tvr")),
            ("Analyst / Expectations", ("analyst", "estimate", "target", "recommend", "rating", "surprise")),
            ("Sentiment / Attention", ("sentiment", "news", "social", "attention", "search", "esg")),
        ]
        per_theme: List[Tuple[int, str, List[str]]] = []
        for theme_name, keywords in theme_keywords:
            matched_ids: List[str] = []
            for item in fields or []:
                if not isinstance(item, dict):
                    continue
                fid = str(item.get("id") or "").strip()
                if not fid:
                    continue
                desc = str(item.get("description") or item.get("desc") or "")
                dataset_id = str(item.get("dataset_id") or item.get("datasetId") or "")
                dataset_name = str(item.get("dataset_name") or item.get("datasetName") or "")
                haystack = " ".join([fid, desc, dataset_id, dataset_name]).lower()
                if any(keyword in haystack for keyword in keywords):
                    matched_ids.append(fid)
            if matched_ids:
                uniq = list(dict.fromkeys(matched_ids))
                per_theme.append((len(uniq), theme_name, uniq[:3]))
        per_theme.sort(key=lambda row: row[0], reverse=True)

        hints: List[str] = []
        for count, theme_name, examples in per_theme[: max(1, top_k)]:
            hints.append(
                f"- {theme_name}: matched_fields={count}, examples={', '.join(examples)}"
            )
        return hints

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

    def _parse_repair_response(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        if "```json" in raw:
            raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in raw:
            raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                expr = data.get("expression")
                if isinstance(expr, str) and expr.strip():
                    return expr.strip()
                alphas = data.get("alphas")
                if isinstance(alphas, list) and alphas:
                    first = alphas[0]
                    if isinstance(first, dict):
                        expr = first.get("expression")
                        if isinstance(expr, str) and expr.strip():
                            return expr.strip()
            elif isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    expr = first.get("expression")
                    if isinstance(expr, str) and expr.strip():
                        return expr.strip()
        except json.JSONDecodeError:
            pass
        # fallback: treat first non-empty line as expression
        for line in raw.splitlines():
            token = line.strip().strip(",")
            if token and not token.startswith("{") and not token.startswith("}"):
                return token
        return ""

    def _build_repair_prompt(
        self,
        expression: str,
        errors: List[str],
        fields: List[Dict[str, Any]],
        context: Dict[str, Any],
        operators: List[Dict[str, Any]],
    ) -> str:
        field_ids = []
        for item in fields[:200]:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("id") or "").strip()
            if fid:
                field_ids.append(fid)
        op_names = []
        for op in operators[:200]:
            if not isinstance(op, dict):
                continue
            name = str(op.get("name") or "").strip()
            if name:
                op_names.append(name)
        error_text = "; ".join(str(err) for err in (errors or [])[:8]) or "unknown validation error"
        region = str(context.get("region") or "USA")
        universe = str(context.get("universe") or "TOP3000")
        delay = str(context.get("delay") if context.get("delay") is not None else 1)
        return f"""You are fixing one WorldQuant Brain FastExpr expression.

Task:
- Repair the expression so it is valid FastExpr and keeps original economic intent.
- Keep the alpha thesis direction unchanged (do not rewrite into a different signal family unless absolutely required by errors).
- Prefer minimal, targeted edits over full rewrites.
- Keep operator calls <= 8.
- Keep referenced fields <= 8.
- Use ONLY provided fields.
- Do not output assignment statements (`a = ...;`), only one final formula.
- Prefer simple robust operators (rank/zscore/winsorize/ts_rank/ts_delta/ts_mean/ts_std_dev/group_rank/group_neutralize).
- Avoid anti-patterns: self-division clones, meaningless constant chains, deep brittle nesting.

Context: region={region}, universe={universe}, delay={delay}

Original expression:
{expression}

Validation errors:
{error_text}

Allowed fields:
{", ".join(field_ids) if field_ids else "-"}

Allowed operators:
{", ".join(op_names) if op_names else "-"}

Return JSON only:
{{"expression":"<fixed_fast_expr>"}}
"""

    def _build_prompt(self, fields: List[Dict[str, Any]], report_text: Optional[str], patterns: Optional[List[Dict[str, Any]]], context: Optional[Dict[str, Any]], operators: Optional[List[Dict[str, Any]]], count: int) -> str:
        def _shorten(text: Any, limit: int = 180) -> str:
            raw = str(text or "").strip()
            if not raw:
                return ""
            compact = " ".join(raw.split())
            return compact if len(compact) <= limit else (compact[: limit - 3] + "...")

        field_lines: List[str] = []
        for idx, f in enumerate(fields or []):
            if not isinstance(f, dict):
                continue
            fid = str(f.get("id") or "").strip()
            if not fid:
                continue
            ftype = str(f.get("type") or "").strip() or "-"
            dataset_id = str(f.get("dataset_id") or f.get("datasetId") or "").strip()
            dataset_name = str(f.get("dataset_name") or f.get("datasetName") or "").strip()
            dataset = dataset_id or dataset_name or "-"
            desc = _shorten(f.get("description") or f.get("desc") or "", limit=220)
            line = f"- [{idx + 1}] id={fid} | type={ftype} | dataset={dataset}"
            if desc:
                line += f" | desc={desc}"
            field_lines.append(line)
        fields_str = "\n".join(field_lines)
        single_dataset_only = bool(context.get("single_dataset_only")) if isinstance(context, dict) else False
        mutation_mode = str(context.get("mutation_mode") or "").lower() if isinstance(context, dict) else ""
        max_operator_calls = int(context.get("max_operator_calls", 8)) if isinstance(context, dict) else 8
        max_expression_fields = int(context.get("max_expression_fields", 8)) if isinstance(context, dict) else 8
        prefer_simple_operators = bool(context.get("prefer_simple_operators")) if isinstance(context, dict) else False
        stage = str(context.get("stage") or "").strip().upper() if isinstance(context, dict) else ""
        reference_expression = str(context.get("reference_expression") or "").strip() if isinstance(context, dict) else ""
        economic_hints = self._infer_economic_theme_hints(fields)
        if economic_hints:
            economics_hint_str = "Detected Economic Signal Spaces (prioritize these):\n" + "\n".join(economic_hints)
        else:
            economics_hint_str = (
                "Detected Economic Signal Spaces (prioritize these):\n"
                "- Ambiguous from field names; enforce explicit economic story in logic."
            )

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
            extra_rules.append("- Mutation Mode MAX: enforce large operator-tree differences between candidates.")
        if mutation_mode == "balanced":
            extra_rules.append("- Mutation Mode BALANCED: prioritize usable variants over aggressive structural novelty.")
        if single_dataset_only:
            extra_rules.append("- Single Dataset ONLY: every expression must stay within one dataset family.")
        if prefer_simple_operators:
            extra_rules.append(
                "- Prefer simple operators: rank/zscore/winsorize/ts_rank/ts_delta/ts_mean/ts_std_dev/ts_zscore/ts_backfill/group_rank/group_zscore/group_neutralize/trade_when."
            )
            extra_rules.append(
                "- Avoid heavy operators unless necessary: regression/vector families and high-order correlation/kurtosis operators."
            )
            extra_rules.append(
                "- Keep nesting shallow and avoid weak structures like field/group_sum(field,group) or pure self-normalization clones."
            )
        extra_rules_str = "\n".join(extra_rules)
        stage_str = f"- Stage: {stage}\n" if stage else ""
        reference_str = f"- Soft reference expression: {reference_expression}\n" if reference_expression else ""

        return f"""You are a senior WorldQuant Brain alpha researcher.
Generate {count} mutually diverse, simulation-ready alphas.

{ctx_str}
{stage_str}{reference_str}

{ops_str}

{report_str}
{patterns_str}
{economics_hint_str}

Available Data Fields:
{fields_str}

INTERNAL WORKFLOW (think silently, do not output this workflow):
A. Build candidate pool across different motifs: momentum/reversal, volatility/dispersion, fundamental quality/value, regime/filtering.
B. Use varied horizons and operator families to reduce structural correlation.
C. Prefer robust transforms for noisy fields: winsorize/zscore/rank/ts_backfill when helpful.
D. Before finalizing, run a strict syntax and argument-count self-check for every expression.
E. Reject template clones; each candidate must differ in both signal source and transform path.
F. Financial thinking checklist before output:
   - Hypothesis: define what inefficiency/risk-premium this captures.
   - Mechanism: why this field and transform should predict future returns.
   - Risk: avoid pure size/beta/industry leak unless intentionally controlled.
   - Robustness: prefer interpretable, stable transforms over brittle overfitting.
G. Economics-first mapping (mandatory, think silently):
   - Choose one primary theme per alpha: Value / Quality / Growth-Revisions / Investment / Momentum / Reversal / Volatility / Liquidity / Sentiment.
   - Convert that thesis into an implementable signal path (transform -> normalize -> optional neutralize/risk control).
   - Prefer monotonic transforms with clear sign intuition.
   - Define implied holding horizon by window choices (short/medium/long) and keep it consistent with thesis.
H. Reject low-quality ideas (mandatory):
   - Pure syntactic novelty without economic mechanism.
   - Circular constructions and self-referential normalization clones.
   - Signals dominated by one unstable point estimate without denoising/risk control.

STRICT REQUIREMENTS (hard constraints):
1. Syntax: MUST use WorldQuant Brain FastExpr syntax (e.g., ts_rank, ts_delta, ts_av, group_rank).
2. Format: Output valid JSON object with key 'alphas'. Each alpha must have 'name', 'expression', and 'logic'.
3. Regional Compliance: Tailor the logic and operators to the current region ({context.get('region') if context else 'Global'}).
4. Data Types: Respect MATRIX vs GROUP data types for fields.
5. Operator Arity: STRICTLY follow each operator Sign/signature input count from the operator list. Never guess argument count.
6. Field Scope: only use fields listed in "Available Data Fields". Do not invent field names.
7. Operator Budget: each expression must use at most {max_operator_calls} operator calls.
7b. Field Budget: each expression can reference at most {max_expression_fields} unique fields.
8. Avoid placeholders or pseudo variables (e.g., x, y, alpha, beta, same_dataset, d).
8b. Expression must be a single FastExpr formula; do not output assignment statements like `a = ...;`.
9. Avoid near-duplicates: candidates must not be simple sign flips or tiny parameter edits of each other.
10. Use field metadata: leverage each field's desc/type/dataset hints to pick valid transforms and avoid misuse.
11. Use stable naming: "name" should be concise and unique.
11b. "logic" must follow this compact format (<= 45 words):
    "Thesis: ...; Mechanism: ...; RiskCtrl: ...".
12. Portfolio realism:
   - Prefer expressions that are robust under industry/common-risk neutralization.
   - Avoid using only one noisy raw field without ranking/zscoring/winsorization/backfill.
13. Ensure economic diversity across batch: cover at least 3 distinct economic themes when count >= 5.
14. Prefer parsimonious trees: target 3~6 operators unless thesis requires otherwise.
15. Pre-output syntax self-check is mandatory (silent, do not print checklist):
   - parentheses are balanced
   - every operator call respects signature arity
   - optional args use named form when required (e.g., k=..., lag=..., rettype=...)
   - no unknown operator/function token
   - no unknown field id
   - expression remains parseable after removing spaces
16. If any candidate fails self-check, repair/regenerate before final JSON output.
17. Never output explanations of checks; output only final JSON alphas.
18. Output exactly {count} items in "alphas".
{extra_rules_str}

OUTPUT CONTRACT:
- Return JSON only. No markdown fences, no extra prose.
- Must parse with `json.loads`.

Example output:
{{
  "alphas": [
    {{
      "name": "Intraday Momentum Filter",
      "expression": "ts_rank(returns, 10) * group_rank(open, industry)",
      "logic": "Thesis: momentum persists; Mechanism: trend ranked within peers; RiskCtrl: industry neutralization reduces sector beta."
    }}
  ]
}}

Now generate exactly {count} alphas:"""
