"""Схема данных заявки.
Описание данных на каждом этапе обработки. 
Для проверки данных на соответствие описанию (типы полей, допустимые значения) используется Pydantic.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


# --- Перечисления (enum) ---------------------------------------------------
# Дубли подписей на русском языке к этим кодам хранятся в data/rules.yaml


class RequestType(StrEnum):
    MALFUNCTION = "malfunction"  # неисправность
    IMPROVEMENT = "improvement"  # предложение по улучшению
    OTHER = "other"              # обращение не про ремонт


class FaultCategory(StrEnum):
    MECHANICAL = "mechanical"
    HYDRAULIC = "hydraulic"
    ELECTRICAL = "electrical"
    INSTRUMENTATION = "instrumentation"  # КИПиА — датчики и приборы
    CONTROL_SYSTEM = "control_system"    # АСУ ТП — управление и связь
    UNKNOWN = "unknown"


class EquipmentState(StrEnum):
    STOPPED = "stopped"
    DEGRADED = "degraded"
    NORMAL = "normal"
    UNKNOWN = "unknown"


class ProductionImpact(StrEnum):
    LINE_STOPPED = "line_stopped"
    UNIT_STOPPED = "unit_stopped"
    REDUCED = "reduced"
    NONE = "none"
    UNKNOWN = "unknown"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# --- Этап 1. Что модель извлекает из текста ---------------------------------
# Только факты из текста, без решений. Приоритет и службу модель здесь
# НЕ определяет — это сделают правила (см. data/rules.yaml).
# description у каждого поля попадёт в JSON-схему и будет подсказкой для модели.


class Extraction(BaseModel):
    request_type: RequestType = Field(
        description="Тип обращения: неисправность, предложение по улучшению или иное"
    )
    equipment_mentions: list[str] = Field(
        default_factory=list,
        description="Коды или названия оборудования так, как они написаны в тексте (например, 'км7', 'Н-204')",
    )
    location: str | None = Field(
        default=None,
        description="Участок, цех или объект, если он упомянут (например, 'Участок №3')",
    )
    symptoms: list[str] = Field(
        default_factory=list,
        description="Наблюдаемые признаки неисправности короткими фразами (например, 'сильная вибрация')",
    )
    fault_summary: str | None = Field(
        default=None,
        description="Суть проблемы одним предложением",
    )
    fault_category: FaultCategory | None = Field(
        default=None,
        description="Категория неисправности; null, если это не неисправность",
    )
    equipment_state: EquipmentState = Field(
        default=EquipmentState.UNKNOWN,
        description="Текущее состояние оборудования по тексту заявки",
    )
    production_impact: ProductionImpact = Field(
        default=ProductionImpact.UNKNOWN,
        description="Влияние на производство",
    )
    safety_risk: bool = Field(
        default=False,
        description="Есть ли угроза безопасности людей или риск аварии: дым, искрение, запах гари, возгорание, утечка горючих веществ",
    )
    safety_signals: list[str] = Field(
        default_factory=list,
        description="Фразы из текста, указывающие на угрозу безопасности",
    )
    repeated_request: bool = Field(
        default=False,
        description="Заявитель указывает, что проблема повторяется или уже обращались ранее",
    )
    after_maintenance: bool = Field(
        default=False,
        description="Проблема появилась после ремонта или технического обслуживания",
    )
    stopped_by_operator: bool = Field(
        default=False,
        description="Оборудование остановлено персоналом намеренно",
    )


# --- Этап 2. Итог обработки заявки ------------------------------------------


class EquipmentRef(BaseModel):
    """Оборудование, найденное в тексте и сопоставленное со справочником."""

    code: str                    # нормализованный код, например "КМ-07"
    in_catalog: bool             # найдено ли в справочнике
    name: str | None = None
    site: str | None = None
    criticality: str | None = None  # A / B / C


class Decision(BaseModel):
    """Решение, принятое правилом, вместе с объяснением.

    Хранить объяснение важно: в интерфейсе мы покажем не только «приоритет
    высокий», но и почему — какие правила сработали.
    """

    value: str
    reasons: list[str] = Field(default_factory=list)


class TriageResult(BaseModel):
    """Итоговая структурированная заявка — то, что программа выдаёт на выходе."""

    request_text: str
    extraction: Extraction
    equipment: list[EquipmentRef] = Field(default_factory=list)
    priority: Decision
    department: Decision
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    # Флаг «нужна проверка человеком», если программа в чём-то не уверена.
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


# --- Эталонный набор для проверки качества ---------------------------------
# Описывает формат файла eval/golden_set.yaml. Тесты проверяют, что
# разметка в нём не содержит опечаток (например, priority: hihg).

# Большинство полей эталона может быть:
#   одним значением, списком допустимых значений или None (поле не проверяем).


class ExpectedResult(BaseModel):
    request_type: RequestType | list[RequestType] | None
    equipment_ids: list[str]
    equipment_state: EquipmentState | list[EquipmentState] | None
    fault_category: FaultCategory | list[FaultCategory] | None
    safety_risk: bool | None
    priority: Priority | list[Priority] | None
    department: str | list[str] | None
    needs_clarification: bool


class GoldenCase(BaseModel):
    id: int
    source: str  # "case" — из задания, "extra" — дополнительный
    text: str
    expected: ExpectedResult
    notes: str | None = None
