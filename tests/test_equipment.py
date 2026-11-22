"""Тесты поиска оборудования в тексте."""

import pytest

from core.equipment import find_by_location, find_codes, resolve_equipment


@pytest.mark.parametrize(
    "text, expected",
    [
        ("насос Н-204 сильно вибрирует", ["Н-204"]),
        ("кампрессор км7 опять не заводится", ["КМ-07"]),       # строчные, без дефиса и нуля
        ("компрессор КМ 07", ["КМ-07"]),                         # пробел вместо дефиса
        ("компрессор KM-07", ["КМ-07"]),                         # латиница вместо кириллицы
        ("остановились конвейер КЛ-03 и дробилка ДР-06", ["КЛ-03", "ДР-06"]),
        ("экскаватор ЭКГ-10", ["ЭКГ-10"]),
        ("у насоса Н-999 течёт сальник", ["Н-999"]),             # нет в справочнике, но код найден
        ("проблема повторяется через 20–30 минут", []),          # числа без букв
        ("приходим до 30 минут", []),                            # «до 30» — не код оборудования
        ("На участке №5 снова проблемы", []),
        ("показывает 120 °C", []),
        ("Н-204 и снова Н-204", ["Н-204"]),                      # без повторов
    ],
)
def test_find_codes(text, expected):
    assert find_codes(text) == expected


def test_resolve_equipment_marks_unknown():
    result = resolve_equipment("насос Н-204 и насос Н-999")
    assert [(e.code, e.in_catalog) for e in result] == [("Н-204", True), ("Н-999", False)]
    assert result[0].criticality == "A"


def test_resolve_equipment_uses_model_mentions():
    # Модель нашла упоминание, которое шаблон в тексте не поймал бы.
    result = resolve_equipment("какой-то текст", mentions=["КМ-12"])
    assert [e.code for e in result] == ["КМ-12"]


@pytest.mark.parametrize(
    "location, expected",
    [
        ("Участок №5", {"КЛ-14", "Н-210"}),
        ("насосной станции", {"Н-201", "Н-202", "Н-203"}),       # другой падеж
        ("дробильном участке", {"ДР-02", "ДР-06", "КЛ-03"}),     # другой порядок слов
        ("Участок №7", set()),
        (None, set()),
    ],
)
def test_find_by_location(location, expected):
    assert {item["id"] for item in find_by_location(location)} == expected
