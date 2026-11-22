"""Поиск оборудования в тексте заявки и сопоставление со справочником.

Эту часть делает обычный код, а не языковая модель. Найти «Н-204» в тексте
можно надёжно и бесплатно, а модель могла бы ошибиться в символе. Модель
помогает только с неочевидными упоминаниями: её список equipment_mentions
пропускается через ту же нормализацию.

Главная сложность — один и тот же код пишут по-разному:
    "КМ-07", "км7", "КМ 07", "KM-07" (латиницей!)
Нормализация приводит все варианты к одному виду, и только потом мы ищем
код в справочнике.
"""

import re

from core.catalog import equipment_by_id
from core.schema import EquipmentRef

# Латинские буквы, которые выглядят как русские. Сотрудник может набрать
# "H-204" на английской раскладке — для человека это то же самое, что "Н-204".
LATIN_TO_CYRILLIC = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")

# Шаблон кода оборудования: 1–3 буквы, необязательные пробел или дефис, 1–3 цифры.
#   (?<![А-ЯЁA-Z])  — перед кодом не должно быть буквы (чтобы не резать слова)
#   (?!\d)          — после цифр не должно быть ещё цифр
# Флаг IGNORECASE позволяет находить и "км7".
CODE_PATTERN = re.compile(
    r"(?<![А-ЯЁA-Z])([А-ЯЁA-Z]{1,3})(\s?-\s?|\s)?(\d{1,3})(?!\d)",
    re.IGNORECASE,
)


def _split_code(prefix: str, number: str) -> tuple[str, int]:
    """("км", "7") -> ("КМ", 7). Число — int, чтобы "07" и "7" совпадали."""
    return prefix.upper().translate(LATIN_TO_CYRILLIC), int(number)


def _catalog_index() -> dict[tuple[str, int], str]:
    """Индекс справочника: ("КМ", 7) -> "КМ-07"."""
    index = {}
    for code in equipment_by_id():
        prefix, number = code.split("-")
        index[_split_code(prefix, number)] = code
    return index


def _known_prefixes() -> set[str]:
    return {prefix for prefix, _ in _catalog_index()}


def find_codes(text: str) -> list[str]:
    """Находит в тексте коды оборудования и возвращает их в нормализованном виде.

    Код из справочника возвращается в каноническом виде ("км7" -> "КМ-07").
    Код, которого нет в справочнике, тоже возвращается ("Н-999"): программа
    должна заметить незнакомое оборудование, а не промолчать.

    Защита от ложных срабатываний: фраза "до 30 минут" тоже подходит под
    шаблон «буквы + цифры». Поэтому незнакомый код принимаем, только если
    его префикс встречается в справочнике (Н, КЛ, КМ...) или код записан
    заглавными буквами через дефис ("ЩР-3").
    """
    index = _catalog_index()
    known_prefixes = _known_prefixes()
    found: list[str] = []

    for match in CODE_PATTERN.finditer(text):
        raw_prefix, separator, number = match.groups()
        key = _split_code(raw_prefix, number)

        if key in index:
            code = index[key]
        elif key[0] in known_prefixes or (
            raw_prefix.isupper() and separator and "-" in separator
        ):
            code = f"{key[0]}-{number}"
        else:
            continue  # скорее всего, это не код оборудования

        if code not in found:  # без повторов, порядок появления сохраняем
            found.append(code)
    return found


def resolve_equipment(text: str, mentions: list[str] | None = None) -> list[EquipmentRef]:
    """Собирает оборудование из текста заявки и упоминаний, найденных моделью."""
    codes = find_codes(text)
    for mention in mentions or []:
        for code in find_codes(mention):
            if code not in codes:
                codes.append(code)

    catalog = equipment_by_id()
    result = []
    for code in codes:
        item = catalog.get(code)
        if item:
            result.append(
                EquipmentRef(
                    code=code,
                    in_catalog=True,
                    name=item["name"],
                    site=item["site"],
                    criticality=item["criticality"],
                )
            )
        else:
            result.append(EquipmentRef(code=code, in_catalog=False))
    return result


# --- Поиск оборудования по месту установки --------------------------------
# Нужен для заявок вроде «на насосной станции падает давление»: оборудование
# не названо, но мы можем подсказать, какие единицы там числятся.


def _site_tokens(text: str) -> set[str]:
    """Превращает название места в набор «основ» слов.

    Разные формы слова сравниваем по первым 4 буквам: «насосной станции» и
    «Насосная станция №1» дают общие основы «насо» и «стан», а «дробильный»
    и «дробления» — «дроб». Это грубая замена настоящей лемматизации,
    но для коротких названий мест её достаточно.
    Номера («№5») сравниваются как числа.
    """
    tokens = set()
    for word in re.findall(r"[а-яёa-z]+|\d+", text.lower()):
        if word.isdigit():
            tokens.add(word)
        elif len(word) >= 3:  # короткие слова («на», «в») пропускаем
            tokens.add(word[:4])
    return tokens


def find_by_location(location: str | None) -> list[dict]:
    """Оборудование, чьё место установки соответствует указанному в заявке."""
    if not location:
        return []
    wanted = _site_tokens(location)
    if not wanted:
        return []
    return [
        item
        for item in equipment_by_id().values()
        if wanted <= _site_tokens(item["site"])  # все слова из заявки есть в названии места
    ]
