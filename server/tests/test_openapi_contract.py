"""Файл api/gateway-v1.yaml — выгрузка OpenAPI приложения, общий с агентом."""

import json
from pathlib import Path

import yaml

from barysguard.main import create_app

CONTRACT = Path(__file__).resolve().parents[2] / "api" / "gateway-v1.yaml"

REGENERATE = (
    # Запись из Python, а не перенаправлением: оболочка Windows испортит кириллицу.
    'python -c "import yaml; from barysguard.main import create_app; '
    "open('../api/gateway-v1.yaml', 'w', encoding='utf-8', newline='\\n').write("
    'yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True))"'
)


def test_application_describes_the_events_contract() -> None:
    generated = create_app().openapi()

    assert "EventEnvelope" in generated["components"]["schemas"]
    assert "/gateway/v1/events" in generated["paths"]
    assert "/api/v1/events" in generated["paths"]


def test_checked_in_contract_matches_the_application() -> None:
    generated = json.loads(json.dumps(create_app().openapi()))
    stored = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))

    assert stored == generated, f"api/gateway-v1.yaml разошёлся с приложением: {REGENERATE}"
