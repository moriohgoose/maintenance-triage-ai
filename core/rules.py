"""Движок правил: по фактам из заявки вычисляет решения.

На вход приходит Extraction (факты, которые модель извлекла из текста).
На выходе получается TriageResult (приоритет, служба, уточняющие вопросы,
действия) с объяснением, какие правила сработали.

В этом модуле нет обращений к языковой модели. Поэтому он:
  * детерминирован — одинаковые факты всегда дают одинаковый результат;
  * проверяется обычными тестами (tests/test_rules.py) без ключа и интернета;
  * управляется справочником data/rules.yaml: чтобы поменять логику,
    обычно достаточно поменять YAML, а не код.
"""

from core.catalog import equipment_by_id, load_actions, load_departments, load_equipment, load_rules
from core.equipment import find_by_location, resolve_equipment
from core.schema import Decision, EquipmentRef, Extraction, RequestType, TriageResult

# --- Вспомогательные функции для работы с приоритетами ----------------------


def _order() -> list[str]:
    return load_rules()["priority_order"]  # ["low", "medium", "high", "critical"]


def _rank(priority: str) -> int:
    """Номер приоритета: low=0 ... critical=3. Нужен, чтобы сравнивать приоритеты."""
    return _order().index(priority)


def _label(priority: str) -> str:
    return load_rules()["priorities"][priority]["label"]


# --- Факты и проверка условий -----------------------------------------------


def build_facts(extraction: Extraction, equipment: list[EquipmentRef]) -> dict:
    """Собирает в один словарь все признаки, на которые могут ссылаться правила.

    Часть признаков берётся из ответа модели (состояние, категория...),
    часть вычисляется по справочнику (известно ли оборудование, чья это техника).
    """
    facts = extraction.model_dump()  # превращаем Pydantic-модель в обычный dict

    known = [e for e in equipment if e.in_catalog]
    facts["equipment_known"] = bool(known)

    # Служба, закреплённая за оборудованием (берём первую известную единицу).
    catalog = equipment_by_id()
    facts["equipment_department"] = catalog[known[0].code]["department"] if known else None

    # Критичность: если единиц несколько, берём самую высокую (A важнее B).
    # Если хоть одна единица неизвестна или оборудование не названо,
    # действуем консервативно (см. unknown_equipment_criticality в rules.yaml).
    unknown_criticality = load_rules()["unknown_equipment_criticality"]
    levels = [e.criticality for e in known]
    if not known or len(known) < len(equipment):
        levels.append(unknown_criticality)
    facts["criticality"] = min(levels)  # "A" < "B" < "C" по алфавиту
    facts["criticality_is_assumed"] = not known or len(known) < len(equipment)
    return facts


def matches(when: dict, facts: dict) -> bool:
    """Проверяет условие правила: все поля из when должны совпасть с фактами.

    Пример: when = {"fault_category": ["mechanical", "hydraulic"], "equipment_known": True}
    Если значение в правиле — список, подходит любое значение из него.
    """
    for field, expected in when.items():
        allowed = expected if isinstance(expected, list) else [expected]
        if facts.get(field) not in allowed:
            return False
    return True


# --- Приоритет --------------------------------------------------------------


def compute_priority(facts: dict) -> Decision:
    rules = load_rules()
    state_labels = rules["equipment_states"]
    state = facts["equipment_state"]
    criticality = facts["criticality"]

    # 1. Базовый приоритет по матрице «состояние × критичность».
    priority = rules["base_priority"][state][criticality]
    criticality_note = " (предположена, т.к. оборудование не определено)" if facts["criticality_is_assumed"] else ""
    reasons = [
        f"Базовый приоритет «{_label(priority)}»: состояние «{state_labels[state]}», "
        f"критичность оборудования {criticality}{criticality_note}"
    ]

    # 2. Корректирующие правила — по очереди, в порядке из rules.yaml.
    for rule in rules["priority_rules"]:
        if not matches(rule["when"], facts):
            continue
        before = priority

        if "set_priority" in rule:
            priority = rule["set_priority"]
        if "min_priority" in rule and _rank(priority) < _rank(rule["min_priority"]):
            priority = rule["min_priority"]
        if "raise_by" in rule:
            raised = min(_rank(priority) + rule["raise_by"], len(_order()) - 1)
            cap = _rank(rule.get("max_priority", "critical"))
            # Повышаем, но не выше потолка; если уже выше потолка — не понижаем.
            priority = _order()[max(_rank(priority), min(raised, cap))]

        if priority != before:
            reasons.append(f"{rule['description']} → «{_label(priority)}» (правило {rule['id']})")

    return Decision(value=priority, reasons=reasons)


# --- Маршрутизация ----------------------------------------------------------


def route(facts: dict) -> Decision:
    """Выбирает службу: первое подходящее правило сверху вниз."""
    rules = load_rules()
    names = load_departments()

    for rule in rules["routing_rules"]:
        if matches(rule["when"], facts):
            department = rule["department"]
            if department == "equipment_default":
                department = facts["equipment_department"]
            return Decision(
                value=department,
                reasons=[f"{rule['description']} → {names[department]['name']} (правило {rule['id']})"],
            )

    fallback = rules["routing_fallback"]
    return Decision(
        value=fallback,
        reasons=[f"Ни одно правило не подошло, нужен ручной разбор → {names[fallback]['name']}"],
    )


# --- Уточняющие вопросы -----------------------------------------------------


def find_missing_info(extraction: Extraction, equipment: list[EquipmentRef]) -> list[str]:
    """Определяет, каких данных не хватает, и возвращает вопросы к заявителю."""
    templates = load_rules()["clarification"]["questions"]
    questions = []

    if extraction.request_type == RequestType.OTHER:
        return []

    # Оборудование: не названо / названо, но не найдено в справочнике.
    if not equipment:
        candidates = find_by_location(extraction.location)
        if len(candidates) >= 2:
            questions.append(
                templates["equipment_ambiguous"].format(
                    site=extraction.location,
                    candidates=", ".join(c["name"] for c in candidates),
                )
            )
        else:
            questions.append(templates["equipment_missing"])
    for e in equipment:
        if not e.in_catalog:
            questions.append(templates["equipment_not_in_catalog"].format(code=e.code))

    # Симптомы и состояние нужны только для неисправностей:
    # у предложения по улучшению симптомов нет.
    if extraction.request_type == RequestType.MALFUNCTION:
        if not extraction.symptoms:
            questions.append(templates["symptoms_missing"])
        if extraction.equipment_state == "unknown":
            questions.append(templates["state_unknown"])

    return questions


# --- Рекомендуемые действия ---------------------------------------------------


def collect_actions(extraction: Extraction, equipment: list[EquipmentRef], facts: dict, priority: str) -> list[str]:
    """Собирает типовые действия из справочника.

    Это «кандидаты»: позже языковая модель выберет из них подходящие к симптомам
    и сформулирует связную рекомендацию. Пока возвращаем весь список.
    """
    actions_ref = load_actions()
    actions = list(actions_ref["by_priority"][priority])

    # Особые признаки заявки -> дополнительные действия.
    flags = {
        "safety_risk": extraction.safety_risk,
        "line_stopped": extraction.production_impact == "line_stopped",
        "after_maintenance": extraction.after_maintenance,
        "repeated_request": extraction.repeated_request,
        "equipment_stopped_by_operator": extraction.stopped_by_operator,
    }
    for flag, is_set in flags.items():
        if is_set:
            actions += actions_ref["by_flag"][flag]

    actions += actions_ref["by_request_type"].get(extraction.request_type, [])
    if extraction.fault_category:
        actions += actions_ref["by_fault_category"].get(extraction.fault_category, [])

    # Типовые проверки по типу оборудования (только для неисправностей).
    if extraction.request_type == RequestType.MALFUNCTION:
        types = load_equipment()["equipment_types"]
        catalog = equipment_by_id()
        for e in equipment:
            if e.in_catalog:
                eq_type = catalog[e.code]["type"]
                actions += [f"{e.code}: {check}" for check in types[eq_type]["typical_checks"]]

    return list(dict.fromkeys(actions))  # убираем повторы, сохраняя порядок


# --- Всё вместе -------------------------------------------------------------


def decide(request_text: str, extraction: Extraction) -> TriageResult:
    """Главная функция модуля: факты из заявки -> итоговая карточка заявки."""
    equipment = resolve_equipment(request_text, extraction.equipment_mentions)
    facts = build_facts(extraction, equipment)

    priority = compute_priority(facts)
    department = route(facts)
    questions = find_missing_info(extraction, equipment)
    actions = collect_actions(extraction, equipment, facts, priority.value)

    # Для критических заявок не задерживаем обработку вопросами:
    # заявка уходит сразу, а вопросы передаются исполнителю как «уточнить на месте».
    clarification_rules = load_rules()["clarification"]
    if questions and priority.value in clarification_rules["skip_for_priorities"]:
        actions += [f"Уточнить на месте: {q}" for q in questions]
        questions = []

    # Когда программа не уверена — просим человека проверить результат.
    review_reasons = []
    if any(not e.in_catalog for e in equipment):
        review_reasons.append("Оборудование не найдено в справочнике")
    if extraction.request_type == RequestType.MALFUNCTION and extraction.fault_category in (None, "unknown"):
        review_reasons.append("Не удалось определить характер неисправности")
    if department.value == load_rules()["routing_fallback"] and extraction.request_type != RequestType.OTHER:
        review_reasons.append("Служба не определена автоматически — заявка передана диспетчеру")

    return TriageResult(
        request_text=request_text,
        extraction=extraction,
        equipment=equipment,
        priority=priority,
        department=department,
        needs_clarification=bool(questions),
        clarification_questions=questions,
        recommended_actions=actions,
        needs_review=bool(review_reasons),
        review_reasons=review_reasons,
    )
