from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

class MetadataError(ValueError):
    pass


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_operators(username: str, password: str, api_base: str = "https://api.worldquantbrain.com") -> Dict[str, Any]:
    from ...api.brain import BrainClient

    client = BrainClient(username=username, password=password, api_base=api_base)
    client.login()
    return client.get_operators()


def fetch_settings_options(username: str, password: str, api_base: str = "https://api.worldquantbrain.com") -> Dict[str, Any]:
    from ...api.brain import BrainClient

    client = BrainClient(username=username, password=password, api_base=api_base)
    client.login()
    return client.get_settings_options()


def fetch_datafields(
    username: str,
    password: str,
    api_base: str = "https://api.worldquantbrain.com",
    instrument_type: str = "EQUITY",
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    dataset_id: str = "",
    data_type: str = "MATRIX",
    search: str = "",
) -> Dict[str, Any]:
    from ...api.brain import BrainClient

    client = BrainClient(username=username, password=password, api_base=api_base)
    client.login()
    return client.get_datafields(
        instrument_type=instrument_type,
        region=region,
        delay=delay,
        universe=universe,
        dataset_id=dataset_id,
        data_type=data_type,
        search=search,
    )
