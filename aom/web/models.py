from __future__ import annotations

FILE_KIND_CONFIG = {
    "template": {"dir": "templates", "ext": [".json"], "default": "demo.json"},
    "factors": {"dir": "generated", "ext": [".json"], "default": "factors_demo.json"},
    "state": {"dir": "runs", "ext": [".json"], "default": "submit_state_demo.json"},
    "template_library": {"dir": "templates", "ext": [".json"], "default": "template_library.json"},
    "factor_library": {"dir": "db", "ext": [".db"], "default": "factor_library.db"},
    "settings": {"dir": "metadata", "ext": [".json"], "default": "settings_options.json"},
    "datafields": {"dir": "metadata", "ext": [".json"], "default": "datafields.json"},
    "operators": {"dir": "metadata", "ext": [".json"], "default": "operators.json"},
}
