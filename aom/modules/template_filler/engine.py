from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

class TemplateError(ValueError):
    pass

@dataclass
class Factor:
    factor_id: str
    expression: str
    settings: Dict[str, Any]
    priority: int = 100
    source_template_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)

@dataclass
class Template:
    template_id: str
    template: str
    placeholders: List[str]
    rules: Dict[str, List[str]]
    metadata: Dict[str, Any] = field(default_factory=dict)

class ExpansionOptions:
    def __init__(self, max_combinations: Optional[int] = None):
        self.max_combinations = max_combinations

def load_template_file(path: Path) -> Template:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Template(
        template_id=data["template_id"],
        template=data["template"],
        placeholders=data.get("placeholders", []),
        rules=data.get("rules", {}),
        metadata=data.get("metadata", {}),
    )

def validate_template(template: Template) -> List[str]:
    errors = []
    if not template.template_id: errors.append("template_id is missing")
    if not template.template: errors.append("template expression is missing")
    return errors

def expand_template(
    template: Template,
    base_settings: Dict[str, Any],
    settings_override: Dict[str, Any] | None = None,
    options: ExpansionOptions | None = None,
) -> List[Factor]:
    from itertools import product
    options = options or ExpansionOptions()
    
    # 合并设置
    final_settings = dict(base_settings)
    if template.metadata.get("settings_override"):
        final_settings.update(template.metadata["settings_override"])
    if settings_override:
        final_settings.update(settings_override)

    # 自动探测表达式中的占位符
    found_phs = sorted(list(set(re.findall(r"<([^/]+)/>", template.template))))
    if not found_phs:
        return [Factor(f"{template.template_id}_base", template.template, final_settings, source_template_id=template.template_id)]

    # 检查是否有任何占位符缺少规则
    values_list = []
    for name in found_phs:
        vals = template.rules.get(name, [])
        # 核心修复：如果某个占位符没有任何规则，直接抛出异常，而不是生成 MISSING_
        if not vals:
            raise TemplateError(f"占位符 <{name}/> 缺少替换规则，请在 JSON 模板中配置 rules.{name}")
        values_list.append(vals)
        
    combinations = list(product(*values_list))
    if options.max_combinations:
        combinations = combinations[:options.max_combinations]

    factors = []
    for i, combo in enumerate(combinations):
        mapping = dict(zip(found_phs, combo))
        expr = template.template
        for k, v in mapping.items():
            expr = expr.replace(f"<{k}/>", str(v))
        
        factors.append(Factor(
            factor_id=f"{template.template_id}_{i:04d}",
            expression=expr.strip(),
            settings=final_settings,
            source_template_id=template.template_id
        ))
    return factors

def write_factors(path: Path, factors: Sequence[Factor], append: bool = False) -> None:
    results = []
    for f in factors:
        results.append({
            "factor_id": f.factor_id,
            "expression": f.expression,
            "settings": f.settings,
            "priority": f.priority,
            "source_template_id": f.source_template_id,
            "tags": f.tags
        })
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

def write_factors_bundle(path: Path, factors: List[Factor]) -> None:
    if not factors: return
    bundle = {
        "version": "1.0",
        "type": "bundle",
        "common_settings": factors[0].settings,
        "factors": [{"id": f.factor_id, "expr": f.expression} for f in factors]
    }
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")

def build_template_skeleton(template_id: str, template: str) -> Dict[str, Any]:
    phs = sorted(list(set(re.findall(r"<([^/]+)/>", template))))
    return {
        "template_id": template_id,
        "template": template,
        "placeholders": phs,
        "rules": {p: [] for p in phs},
        "metadata": {"priority": 100, "settings_override": {}}
    }
