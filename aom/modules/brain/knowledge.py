from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict

@dataclass
class AlphaPattern:
    id: str
    name: str
    description: str
    template: str
    tags: List[str]

# 经典 Alpha 灵感库
PATTERNS: List[AlphaPattern] = [
    AlphaPattern(
        id="reversal_std",
        name="均值回归 (Mean Reversal)",
        description="基于价格或因子的短期过度反应，预期未来会发生反转。",
        template="-ts_rank(<field/>, <d1/>)",
        tags=["reversal", "price-action"]
    ),
    AlphaPattern(
        id="momentum_trend",
        name="趋势跟随 (Momentum)",
        description="捕捉资产价格或基本面指标的持续性趋势。",
        template="ts_rank(ts_returns(<field/>, <d1/>), <d2/>)",
        tags=["momentum", "trend"]
    ),
    AlphaPattern(
        id="relative_value",
        name="相对价值 (Relative Value)",
        description="比较当前值与其历史平均水平的偏离程度。",
        template="<field/> / ts_av(<field/>, <d1/>) - 1",
        tags=["value", "stats"]
    ),
    AlphaPattern(
        id="volatility_adjusted",
        name="波动率调节 (Volatility Adjusted)",
        description="使用标准差对信号进行归一化，降低高波动时期的风险曝露。",
        template="<field/> / ts_std_dev(<field/>, <d1/>)",
        tags=["risk-control", "volatility"]
    ),
    AlphaPattern(
        id="growth_acceleration",
        name="增长加速度 (Growth Acceleration)",
        description="寻找指标增长速度在加快的标的。",
        template="ts_delta(<field/>, <d1/>) - ts_delta(<field/>, <d2/>)",
        tags=["growth", "fundamental"]
    ),
    AlphaPattern(
        id="divergence_signal",
        name="分歧信号 (Divergence)",
        description="观察两个相关指标之间的背离。",
        template="ts_rank(<field1/>, <d1/>) - ts_rank(<field2/>, <d1/>)",
        tags=["cross-sectional", "divergence"]
    ),
    AlphaPattern(
        id="financial_backfill_group_ts",
        name="财务回填-分组-时序模板",
        description="先回填财务字段，再做分组变换，最后做时序算子，结构稳定且便于扩展。",
        template="<ts_operator/>(<group_operator/>(ts_backfill(<mixdata/>, 90), industry), <window/>)",
        tags=["template", "fundamental", "group", "timeseries"]
    ),
    AlphaPattern(
        id="financial_delta_group_rank",
        name="财务变化率分组排序模板",
        description="对回填后的财务字段取变化率，再行业分组排序，常用于低频基本面信号。",
        template="group_rank(ts_delta(ts_backfill(<mixdata/>, 90), <d1/>), industry)",
        tags=["template", "fundamental", "simple-op"]
    )
]

def get_all_patterns() -> List[Dict]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "template": p.template,
            "tags": p.tags
        } for p in PATTERNS
    ]

# 常用操作符分类参考 (用于强化 AI 的语法准确度)
OPERATORS_REFERENCE = {
    "Time-Series": ["ts_rank(x, d)", "ts_delta(x, d)", "ts_av(x, d)", "ts_std_dev(x, d)", "ts_min(x, d)", "ts_max(x, d)", "ts_returns(x, d)", "ts_correlation(x, y, d)", "ts_covariance(x, y, d)"],
    "Cross-Sectional": ["rank(x)", "zscore(x)", "normalize(x)", "scale(x)"],
    "Group/Neutralization": ["group_rank(x, group)", "group_neutralize(x, group)", "group_zscore(x, group)", "group_av(x, group)"],
    "Arithmetic/Logical": ["add(x, y)", "sub(x, y)", "mul(x, y)", "div(x, y)", "pow(x, y)", "abs(x)", "log(x)", "sign(x)", "if_else(cond, x, y)"],
    "Technical": ["vwap", "open", "close", "high", "low", "volume", "cap", "returns"]
}
