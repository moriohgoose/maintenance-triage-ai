"""Тесты движка правил.

Главный тест здесь — test_rules_match_golden_set. Для каждой заявки из
эталонного набора вручную записаны «идеальные» факты, которые должна
извлечь языковая модель. Тест проверяет, что по этим фактам правила дают
ожидаемые приоритет, службу и решение об уточнении.

Смысл: если этот тест проходит, то на реальных прогонах все ошибки будут
ошибками извлечения (модели), а не ошибками правил. Так мы разделяем
источники ошибок — это пригодится при анализе качества.
"""

import pytest

from core.catalog import load_golden_set
from core.rules import compute_priority, decide, matches
from core.schema import Extraction

# Идеальные извлечения для каждой заявки эталонного набора (ключ — id заявки).
# Указываем только поля, отличающиеся от значений по умолчанию.
IDEAL_EXTRACTIONS: dict[int, dict] = {
    1: dict(request_type="malfunction", location="Участок №3",
            symptoms=["сильная вибрация", "шум"], fault_category="mechanical",
            equipment_state="degraded", production_impact="none"),
    2: dict(request_type="malfunction", symptoms=["периодические остановки"],
            fault_category="electrical", equipment_state="degraded", production_impact="reduced"),
    3: dict(request_type="malfunction", symptoms=["перегрев гидросистемы при нагрузке"],
            fault_category="hydraulic", equipment_state="degraded", production_impact="none"),
    4: dict(request_type="malfunction", symptoms=["не запускается", "щелчок при включении"],
            fault_category="electrical", equipment_state="stopped", production_impact="unit_stopped"),
    5: dict(request_type="malfunction", symptoms=["снижение производительности"],
            fault_category="mechanical", equipment_state="degraded", production_impact="reduced"),
    6: dict(request_type="malfunction", location="Участок дробления",
            symptoms=["металлический стук"], fault_category="mechanical",
            equipment_state="stopped", production_impact="unit_stopped", stopped_by_operator=True),
    7: dict(request_type="malfunction", symptoms=["потеря мощности при подъёме груза"],
            fault_category="hydraulic", equipment_state="degraded", production_impact="reduced"),
    8: dict(request_type="malfunction", location="насосная станция",
            symptoms=["периодическое падение давления"], fault_category="hydraulic",
            equipment_state="degraded", production_impact="reduced"),
    9: dict(request_type="malfunction", symptoms=["датчик показывает 120 °C"],
            fault_category="instrumentation", equipment_state="normal", production_impact="none"),
    10: dict(request_type="malfunction", symptoms=["плавают обороты", "двигатель глохнет"],
             fault_category="mechanical", equipment_state="degraded", after_maintenance=True),
    11: dict(request_type="malfunction", location="Участок №5", symptoms=[],
             fault_category="unknown", equipment_state="degraded", repeated_request=True),
    12: dict(request_type="malfunction", symptoms=["утечка охлаждающей жидкости"],
             fault_category="mechanical", equipment_state="degraded", production_impact="none"),
    13: dict(request_type="malfunction", symptoms=["пропадает связь с системой управления"],
             fault_category="control_system", equipment_state="degraded", production_impact="reduced"),
    14: dict(request_type="improvement", fault_category=None, equipment_state="normal",
             production_impact="none"),
    15: dict(request_type="malfunction", location="Дробильный участок",
             symptoms=["одновременная остановка", "ошибки на панели управления"],
             fault_category="control_system", equipment_state="stopped",
             production_impact="line_stopped"),
    16: dict(request_type="malfunction", location="Участок №7", symptoms=["течёт сальник"],
             fault_category="mechanical", equipment_state="degraded"),
    17: dict(request_type="malfunction", symptoms=["искрение", "запах гари"],
             fault_category="electrical", equipment_state="degraded",
             safety_risk=True, safety_signals=["искрит", "пахнет гарью"]),
    18: dict(request_type="malfunction", symptoms=["не заводится", "щелчок"],
             fault_category="electrical", equipment_state="stopped", repeated_request=True),
    19: dict(request_type="malfunction", fault_category="unknown", equipment_state="unknown"),
    20: dict(request_type="malfunction", fault_category="unknown", equipment_state="unknown"),
    21: dict(request_type="other", fault_category=None),
}

GOLDEN = load_golden_set()


def _ok(actual, expected) -> bool:
    """Совпадает ли значение с эталоном (None — не проверяем, список — любое из)."""
    if expected is None:
        return True
    allowed = expected if isinstance(expected, list) else [expected]
    return actual in allowed


def test_every_golden_case_has_ideal_extraction():
    assert {c["id"] for c in GOLDEN} == set(IDEAL_EXTRACTIONS)


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: f"case_{c['id']}")
def test_rules_match_golden_set(case):
    extraction = Extraction(**IDEAL_EXTRACTIONS[case["id"]])
    result = decide(case["text"], extraction)
    expected = case["expected"]

    assert sorted(e.code for e in result.equipment) == sorted(expected["equipment_ids"])
    assert _ok(result.priority.value, expected["priority"]), result.priority.reasons
    assert _ok(result.department.value, expected["department"]), result.department.reasons
    assert result.needs_clarification == expected["needs_clarification"], result.clarification_questions


# --- Точечные проверки отдельных механизмов ---


def test_matches_with_list_means_any_of():
    when = {"fault_category": ["mechanical", "hydraulic"], "equipment_known": True}
    assert matches(when, {"fault_category": "hydraulic", "equipment_known": True})
    assert not matches(when, {"fault_category": "electrical", "equipment_known": True})
    assert not matches(when, {"fault_category": "mechanical", "equipment_known": False})


def test_repeated_request_does_not_lower_critical():
    facts = {"equipment_state": "stopped", "criticality": "A", "criticality_is_assumed": False,
             "repeated_request": True, "request_type": "malfunction"}
    assert compute_priority(facts).value == "critical"


def test_priority_reasons_explain_decision():
    extraction = Extraction(**IDEAL_EXTRACTIONS[17])
    result = decide("В шкафу управления конвейера КЛ-12 искрит", extraction)
    assert any("safety_risk" in r for r in result.priority.reasons)


def test_ambiguous_location_lists_candidates():
    case = next(c for c in GOLDEN if c["id"] == 8)
    result = decide(case["text"], Extraction(**IDEAL_EXTRACTIONS[8]))
    assert "Н-201" in result.clarification_questions[0]


def test_critical_request_moves_questions_to_onsite_actions():
    # Простой линии, но симптомы не описаны: вопрос не задаём, а передаём бригаде.
    extraction = Extraction(request_type="malfunction", equipment_state="stopped",
                            production_impact="line_stopped", fault_category="unknown")
    result = decide("Стоит дробилка ДР-02, линия простаивает", extraction)
    assert result.priority.value == "critical"
    assert not result.needs_clarification
    assert any(a.startswith("Уточнить на месте") for a in result.recommended_actions)


def test_unknown_equipment_needs_review():
    result = decide("У насоса Н-999 течёт сальник", Extraction(**IDEAL_EXTRACTIONS[16]))
    assert result.needs_review
    assert "не найдено в справочнике" in result.review_reasons[0]
