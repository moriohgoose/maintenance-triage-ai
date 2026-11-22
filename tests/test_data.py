"""Проверки целостности справочников и эталонной разметки.

Эти тесты не вызывают языковую модель и работают за доли секунды.
Они ловят ошибки в YAML-файлах: опечатку в названии службы, оборудование
с несуществующим типом, «дыру» в матрице приоритетов и т.п.

Запуск:  pytest -v
"""

import pytest

from core.catalog import (
    equipment_by_id,
    load_actions,
    load_departments,
    load_equipment,
    load_golden_set,
    load_rules,
)
from core.schema import EquipmentState, FaultCategory, GoldenCase, Priority, RequestType

CRITICALITY_LEVELS = {"A", "B", "C"}

# Значения, которые правила могут использовать в условиях when.
# Ключ — имя поля, значение — множество допустимых значений.
ALLOWED_WHEN_VALUES = {
    "request_type": {v.value for v in RequestType},
    "fault_category": {v.value for v in FaultCategory},
    "equipment_state": {v.value for v in EquipmentState},
    "production_impact": {"line_stopped", "unit_stopped", "reduced", "none", "unknown"},
    "safety_risk": {True, False},
    "repeated_request": {True, False},
    "equipment_known": {True, False},
    "equipment_department": set(load_departments()),
}


def _as_list(value):
    return value if isinstance(value, list) else [value]


# --- Справочник оборудования ---


def test_equipment_ids_are_unique():
    ids = [item["id"] for item in load_equipment()["equipment"]]
    assert len(ids) == len(set(ids)), "В справочнике есть повторяющиеся коды"


def test_equipment_references_are_valid():
    types = load_equipment()["equipment_types"]
    departments = load_departments()
    for item in load_equipment()["equipment"]:
        assert item["type"] in types, f"{item['id']}: неизвестный тип {item['type']}"
        assert item["department"] in departments, f"{item['id']}: неизвестная служба"
        assert item["criticality"] in CRITICALITY_LEVELS, f"{item['id']}: неверная критичность"


# --- Правила ---


def test_rules_labels_match_enums():
    """Русские подписи в rules.yaml есть для каждого значения из schema.py."""
    rules = load_rules()
    assert set(rules["request_types"]) == {v.value for v in RequestType}
    assert set(rules["fault_categories"]) == {v.value for v in FaultCategory}
    assert set(rules["equipment_states"]) == {v.value for v in EquipmentState}
    assert set(rules["priorities"]) == {v.value for v in Priority}
    assert set(rules["priority_order"]) == {v.value for v in Priority}


def test_base_priority_matrix_is_complete():
    """Для каждого состояния и каждой критичности задан приоритет."""
    matrix = load_rules()["base_priority"]
    priorities = {v.value for v in Priority}
    for state in EquipmentState:
        assert state.value in matrix, f"Нет строки для состояния {state}"
        row = matrix[state.value]
        assert set(row) == CRITICALITY_LEVELS, f"Неполная строка {state}"
        assert set(row.values()) <= priorities


@pytest.mark.parametrize("section", ["priority_rules", "routing_rules"])
def test_rule_conditions_are_valid(section):
    for rule in load_rules()[section]:
        for field, value in rule["when"].items():
            assert field in ALLOWED_WHEN_VALUES, f"{rule['id']}: неизвестное поле {field}"
            for v in _as_list(value):
                assert v in ALLOWED_WHEN_VALUES[field], f"{rule['id']}: недопустимое значение {v}"


@pytest.mark.parametrize("section", ["priority_rules", "routing_rules"])
def test_rules_have_descriptions(section):
    """Описание правила показывается пользователю как объяснение решения."""
    for rule in load_rules()[section]:
        assert rule.get("description"), f"{rule['id']}: нет описания"


def test_priority_rules_effects_are_valid():
    priorities = {v.value for v in Priority}
    for rule in load_rules()["priority_rules"]:
        for key in ("set_priority", "min_priority", "max_priority"):
            if key in rule:
                assert rule[key] in priorities, f"{rule['id']}: неверный {key}"


def test_routing_targets_exist():
    departments = set(load_departments()) | {"equipment_default"}
    rules = load_rules()
    for rule in rules["routing_rules"]:
        assert rule["department"] in departments, f"{rule['id']}: неизвестная служба"
    assert rules["routing_fallback"] in departments


def test_actions_keys_are_valid():
    actions = load_actions()
    assert set(actions["by_priority"]) == {v.value for v in Priority}
    assert set(actions["by_fault_category"]) <= {v.value for v in FaultCategory}
    assert set(actions["by_request_type"]) <= {v.value for v in RequestType}


# --- Эталонная разметка ---

GOLDEN = load_golden_set()


def test_golden_ids_are_unique():
    ids = [case["id"] for case in GOLDEN]
    assert len(ids) == len(set(ids))


# parametrize: один тест на каждую заявку — в отчёте видно, какая именно сломана.
@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: f"case_{c['id']}")
def test_golden_case_is_valid(case):
    parsed = GoldenCase.model_validate(case)  # проверка формата и значений enum

    departments = set(load_departments())
    for dep in _as_list(parsed.expected.department):
        assert dep is None or dep in departments, f"неизвестная служба {dep}"

    # Все коды в эталоне есть в справочнике — кроме намеренно «чужих»
    # (заявка №16 проверяет реакцию на оборудование не из справочника).
    known = equipment_by_id()
    for code in parsed.expected.equipment_ids:
        if code not in known:
            assert parsed.source == "extra", f"{code} нет в справочнике"
