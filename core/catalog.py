"""Загрузка справочников из папки data/.

Справочники хранятся в YAML: этот формат легко читать и править руками,
не трогая код. Чтобы добавить оборудование, достаточно дописать его
в data/equipment.yaml.
"""

from functools import cache
from pathlib import Path

import yaml

# Путь к папке data/ вычисляем относительно этого файла, а не текущей папки.
# Так код работает, откуда бы его ни запустили.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
EVAL_DIR = PROJECT_ROOT / "eval"


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# @cache: файл читается с диска один раз, дальше результат берётся из памяти.
@cache
def load_equipment() -> dict:
    return _load_yaml(DATA_DIR / "equipment.yaml")


@cache
def load_departments() -> dict:
    return _load_yaml(DATA_DIR / "departments.yaml")["departments"]


@cache
def load_rules() -> dict:
    return _load_yaml(DATA_DIR / "rules.yaml")


@cache
def load_actions() -> dict:
    return _load_yaml(DATA_DIR / "actions.yaml")


def load_golden_set() -> list[dict]:
    return _load_yaml(EVAL_DIR / "golden_set.yaml")["cases"]


def equipment_by_id() -> dict[str, dict]:
    """Словарь «код → карточка оборудования» для быстрого поиска по коду."""
    return {item["id"]: item for item in load_equipment()["equipment"]}
