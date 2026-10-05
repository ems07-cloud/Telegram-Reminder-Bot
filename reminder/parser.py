"""Разбор напоминаний на обычном русском языке.

«завтра в 15:00 позвонить клиенту», «через 20 минут выключить духовку»,
«в пятницу в 9:30 планёрка», «15 октября в 18:00 день рождения Лены»,
«каждый понедельник в 9 отчёт», «по будням в 8:30 зарядка», «каждое 1 число оплатить аренду».

parse() получает текст и текущее время в часовом поясе пользователя и возвращает,
когда напомнить, о чём и как повторять — или ParseError с подсказкой.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = {  # формы после «в/во/каждый/каждую/каждое» → номер дня (0 — понедельник)
    "понедельник": 0, "вторник": 1, "среду": 2, "среда": 2, "четверг": 3,
    "пятницу": 4, "пятница": 4, "субботу": 5, "суббота": 5, "воскресенье": 6,
}
WEEKDAY_NAMES = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]
DEFAULT_TIME = time(9, 0)   # дата без времени — напоминаем утром

UNITS = {"мин": "minutes", "минут": "minutes", "минуту": "minutes", "минуты": "minutes",
         "час": "hours", "часа": "hours", "часов": "hours", "ч": "hours",
         "день": "days", "дня": "days", "дней": "days", "сутки": "days",
         "недел": "weeks", "неделю": "weeks", "недели": "weeks", "недель": "weeks"}
WORD_NUMBERS = {"одну": 1, "один": 1, "одна": 1, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5,
                "десять": 10, "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40, "полтора": 1.5}


class ParseError(ValueError):
    """Не удалось понять, когда напомнить. Текст ошибки можно показать пользователю."""


@dataclass
class Parsed:
    when: datetime          # первое срабатывание, в часовом поясе пользователя
    text: str               # о чём напомнить
    repeat: str | None      # None, "daily", "weekdays", "weekly:<0-6>", "monthly:<1-31>"


# ---------- время суток ----------

_TIME = re.compile(
    r"\bв\s+(?P<h>\d{1,2})(?:[:.](?P<m>\d{2})|\s*ч(?:ас(?:а|ов)?)?(?:\s*(?P<m2>\d{1,2})\s*мин(?:ут[ыа]?)?)?)?"
    r"(?:\s+(?P<part>утра|дня|вечера|ночи))?(?=\s|$|[,.!?])",
    re.I)
_BARE_TIME = re.compile(r"(?<![\d.:])(?P<h>\d{1,2}):(?P<m>\d{2})(?![\d:])")


def _apply_part(hour: int, part: str | None) -> int:
    if not part:
        return hour
    part = part.lower()
    if part in ("дня", "вечера") and hour < 12:
        return hour + 12
    if part == "ночи" and hour == 12:
        return 0
    if part == "утра" and hour == 12:
        return 0
    return hour


def _take_time(text: str) -> tuple[time | None, str]:
    m = _TIME.search(text) or _BARE_TIME.search(text)
    if not m:
        return None, text
    hour = int(m.group("h"))
    minute = int(m.groupdict().get("m") or m.groupdict().get("m2") or 0)
    hour = _apply_part(hour, m.groupdict().get("part"))
    if hour > 23 or minute > 59:
        raise ParseError(f"Не бывает времени {m.group(0).strip()}. Напишите, например, «в 15:30».")
    return time(hour, minute), (text[:m.start()] + " " + text[m.end():])


# ---------- даты ----------

def _next_weekday(today: date, wd: int, t: time, now: datetime) -> date:
    days = (wd - today.weekday()) % 7
    d = today + timedelta(days=days)
    if days == 0 and datetime.combine(d, t, now.tzinfo) <= now:
        d += timedelta(days=7)
    return d


def _month_from_word(word: str) -> int | None:
    word = word.lower()
    for stem, num in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        if word.startswith(stem) and (stem != "ма" or word in ("мая", "май")):
            return num
    return None


def _clean_text(text: str) -> str:
    text = re.sub(r"^\s*(?:пожалуйста\s+)?напомн\w*(?:\s+мне)?\s*", "", text, flags=re.I)
    text = re.sub(r"^\s*(?:о\s+том,?\s+)?что\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,.:—-")
    return text[:1].upper() + text[1:] if text else text


def _number(token: str) -> float:
    token = token.lower()
    if token in WORD_NUMBERS:
        return WORD_NUMBERS[token]
    return float(token.replace(",", "."))


_IN = re.compile(r"\bчерез\s+(?:(?P<n>\d+(?:[.,]\d+)?|одну|один|одна|две|два|три|четыре|пять|десять|пятнадцать|"
                 r"двадцать|тридцать|сорок|полтора)\s+)?(?P<unit>минут[уы]?|мин|час(?:а|ов)?|ч|дн(?:я|ей)|день|сутки|"
                 r"недел[юиь]\w*)\b|\bчерез\s+(?P<half>полчаса)\b", re.I)
_REL_DAY = re.compile(r"\b(?P<w>сегодня|завтра|послезавтра)\b", re.I)
_WEEKDAY = re.compile(r"\bв(?:о)?\s+(?P<w>понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b", re.I)
_DMY = re.compile(r"(?<![\d:])(?P<d>\d{1,2})\.(?P<m>\d{1,2})(?:\.(?P<y>\d{2,4}))?(?![\d:])")
_D_MONTH = re.compile(r"\b(?P<d>\d{1,2})\s+(?P<mon>[а-я]+)(?:\s+(?P<y>\d{4})(?:\s*г(?:ода|\.)?)?)?", re.I)
_EVERY_DAY = re.compile(r"\b(?:каждый\s+день|ежедневно)\b", re.I)
_WEEKDAYS_ONLY = re.compile(r"\b(?:по\s+будням|каждый\s+будний\s+день|в\s+будни)\b", re.I)
_EVERY_WEEKDAY = re.compile(r"\b(?:кажд(?:ый|ую|ое)|по)\s+(?P<w>понедельник|вторник|сред[уа]|четверг|пятниц[уа]|"
                            r"суббот[уа]|воскресенье)(?:ам|м)?\b", re.I)
_EVERY_MONTH = re.compile(r"\bкаждое\s+(?P<d>\d{1,2})(?:-?е)?\s+число\b|\bкаждого\s+(?P<d2>\d{1,2})(?:-?го)?\s+числа\b",
                          re.I)


def _weekday_index(word: str) -> int:
    word = word.lower()
    for name, idx in WEEKDAYS.items():
        if word.startswith(name[:4]):
            return idx
    raise ParseError(f"Не понял день недели «{word}»")


def parse(text: str, now: datetime) -> Parsed:
    """Разбирает «когда и о чём». now — текущее время в часовом поясе пользователя (aware)."""
    if not text or not text.strip():
        raise ParseError("Пустое сообщение.")
    rest = " " + text.strip() + " "
    today = now.date()

    # 1) «через …» — без времени суток, сразу точный момент
    m = _IN.search(rest)
    if m:
        if m.group("half"):
            delta = timedelta(minutes=30)
        else:
            n = _number(m.group("n")) if m.group("n") else 1
            unit_word = m.group("unit").lower()
            unit = next(v for k, v in sorted(UNITS.items(), key=lambda kv: -len(kv[0])) if unit_word.startswith(k))
            delta = timedelta(**{unit: n})
        if delta <= timedelta(0):
            raise ParseError("Интервал должен быть больше нуля.")
        rest = rest[:m.start()] + " " + rest[m.end():]
        return _done(now + delta, rest, None)

    # 2) повторы
    repeat = None
    for rx, kind in ((_WEEKDAYS_ONLY, "weekdays"), (_EVERY_DAY, "daily")):
        m = rx.search(rest)
        if m:
            repeat = kind
            rest = rest[:m.start()] + " " + rest[m.end():]
            break
    if not repeat:
        m = _EVERY_WEEKDAY.search(rest)
        if m:
            repeat = f"weekly:{_weekday_index(m.group('w'))}"
            rest = rest[:m.start()] + " " + rest[m.end():]
    if not repeat:
        m = _EVERY_MONTH.search(rest)
        if m:
            day = int(m.group("d") or m.group("d2"))
            if not 1 <= day <= 31:
                raise ParseError("В месяце не бывает такого числа.")
            repeat = f"monthly:{day}"
            rest = rest[:m.start()] + " " + rest[m.end():]

    t, rest = _take_time(rest)

    if repeat:
        first = next_occurrence(repeat, t or DEFAULT_TIME, now, inclusive=True)
        return _done(first, rest, repeat)

    # 3) разовые даты
    day = None
    m = _REL_DAY.search(rest)
    if m:
        day = today + timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[m.group("w").lower()])
        rest = rest[:m.start()] + " " + rest[m.end():]
    if day is None:
        m = _WEEKDAY.search(rest)
        if m:
            day = _next_weekday(today, _weekday_index(m.group("w")), t or DEFAULT_TIME, now)
            rest = rest[:m.start()] + " " + rest[m.end():]
    if day is None:
        m = _DMY.search(rest)
        if m:
            day = _make_date(int(m.group("d")), int(m.group("m")), m.group("y"), today)
            rest = rest[:m.start()] + " " + rest[m.end():]
    if day is None:
        for m in _D_MONTH.finditer(rest):
            month = _month_from_word(m.group("mon"))
            if month:
                day = _make_date(int(m.group("d")), month, m.group("y"), today)
                rest = rest[:m.start()] + " " + rest[m.end():]
                break

    if day is None and t is None:
        raise ParseError("Не понял, когда напомнить. Например: «завтра в 15:00 позвонить клиенту», "
                         "«через 20 минут выключить духовку», «каждый понедельник в 9 планёрка».")
    if day is None:                     # только время: сегодня, а если уже прошло — завтра
        when = datetime.combine(today, t, now.tzinfo)
        if when <= now:
            when += timedelta(days=1)
        return _done(when, rest, None)

    when = datetime.combine(day, t or DEFAULT_TIME, now.tzinfo)
    if when <= now:
        raise ParseError("Это время уже прошло. Укажите время в будущем.")
    return _done(when, rest, None)


def _make_date(d: int, m: int, y: str | None, today: date) -> date:
    year = int(y) if y else today.year
    if y and len(y) == 2:
        year += 2000
    try:
        result = date(year, m, d)
    except ValueError:
        raise ParseError(f"Такой даты нет: {d:02d}.{m:02d}.") from None
    if not y and result < today:      # «5 марта» в октябре — значит, следующего года
        result = date(year + 1, m, d)
    return result


def _done(when: datetime, rest: str, repeat: str | None) -> Parsed:
    text = _clean_text(rest)
    if not text:
        raise ParseError("А о чём напомнить? Например: «завтра в 15:00 позвонить клиенту».")
    return Parsed(when=when, text=text, repeat=repeat)


# ---------- повторы ----------

def next_occurrence(repeat: str, at: time, after: datetime, inclusive: bool = False) -> datetime:
    """Следующий момент повтора после after (в его часовом поясе). Время суток сохраняется,
    в том числе при переходе на летнее/зимнее время."""
    tz = after.tzinfo
    d = after.date()
    for _ in range(400):
        candidate = datetime.combine(d, at, tz)
        if (candidate > after or (inclusive and candidate == after)) and _matches(repeat, d):
            return candidate
        d += timedelta(days=1)
    raise ParseError("Не нашёл подходящую дату повтора.")


def _matches(repeat: str, d: date) -> bool:
    if repeat == "daily":
        return True
    if repeat == "weekdays":
        return d.weekday() < 5
    kind, _, arg = repeat.partition(":")
    if kind == "weekly":
        return d.weekday() == int(arg)
    if kind == "monthly":
        # «каждое 31 число» в коротком месяце — последний день месяца
        last = (date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)).day
        return d.day == min(int(arg), last)
    raise ValueError(f"Неизвестный повтор: {repeat}")


def describe_repeat(repeat: str | None) -> str:
    if not repeat:
        return ""
    if repeat == "daily":
        return "каждый день"
    if repeat == "weekdays":
        return "по будням"
    kind, _, arg = repeat.partition(":")
    if kind == "weekly":
        n = int(arg)
        prefix = "каждую" if n in (2, 4, 5) else ("каждое" if n == 6 else "каждый")
        return f"{prefix} {WEEKDAY_NAMES[n]}"
    return f"каждое {arg} число"


def describe_when(when: datetime, now: datetime) -> str:
    """«сегодня в 15:00», «завтра в 09:00», «в пятницу, 9 октября, в 09:30», «15 марта 2027 в 18:00»."""
    days = (when.date() - now.date()).days
    hm = when.strftime("%H:%M")
    if days == 0:
        return f"сегодня в {hm}"
    if days == 1:
        return f"завтра в {hm}"
    day = f"{when.day} {MONTHS_GEN[when.month - 1]}"
    if when.year != now.year:
        day += f" {when.year}"
    if days < 7:
        return f"в {WEEKDAY_NAMES[when.weekday()]}, {day}, в {hm}"
    return f"{day} в {hm}"
