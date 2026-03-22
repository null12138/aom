from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .schema import SCHEMA_VERSION


@dataclass
class Template:
    template_id: str
    template: str
    fill_rules: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "template_id": self.template_id,
            "template": self.template,
            "fill_rules": self.fill_rules,
            "metadata": self.metadata,
        }


@dataclass
class Factor:
    factor_id: str
    expression: str
    settings: Dict[str, Any]
    priority: int = 100
    source_template_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "factor_id": self.factor_id,
            "expression": self.expression,
            "settings": self.settings,
            "priority": self.priority,
            "source_template_id": self.source_template_id,
            "tags": self.tags,
        }


@dataclass
class RunState:
    run_id: str
    created_at: str
    config: Dict[str, Any]
    queue: List[Dict[str, Any]] = field(default_factory=list)
    in_flight: List[Dict[str, Any]] = field(default_factory=list)
    completed: List[Dict[str, Any]] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "config": self.config,
            "queue": self.queue,
            "in_flight": self.in_flight,
            "completed": self.completed,
            "failed": self.failed,
            "stats": self.stats,
        }
