from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from reminder.parser import ParseError, describe_repeat, describe_when, next_occurrence, parse

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=MSK)          # среда, 12:00


def at(y, mo, d, h, mi=0, tz=MSK):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


@pytest.mark.parametrize("text, when, what", [
    ("завтра в 15:00 позвонить клиенту", at(2026, 10, 8, 15), "Позвонить клиенту"),
    ("Напомни мне завтра в 15:00 позвонить клиенту", at(2026, 10, 8, 15), "Позвонить клиенту"),
    ("сегодня в 18:30 забрать посылку", at(2026, 10, 7, 18, 30), "Забрать посылку"),
    ("послезавтра купить билеты", at(2026, 10, 9, 9), "Купить билеты"),             # без времени — в 9:00
    ("в пятницу в 9:30 планёрка", at(2026, 10, 9, 9, 30), "Планёрка"),
    ("во вторник в 11 созвон", at(2026, 10, 13, 11), "Созвон"),
    ("в среду в 10 отчёт", at(2026, 10, 14, 10), "Отчёт"),                           # сегодня 10:00 прошло
    ("в среду в 16 отчёт", at(2026, 10, 7, 16), "Отчёт"),                            # а 16:00 — ещё сегодня
    ("15 октября в 18:00 день рождения Лены", at(2026, 10, 15, 18), "День рождения Лены"),
    ("5 марта поздравить маму", at(2027, 3, 5, 9), "Поздравить маму"),               # дата прошла — следующий год
    ("1 мая 2027 г. в 10:00 шашлыки", at(2027, 5, 1, 10), "Шашлыки"),
    ("20.10 в 14:15 сдать отчёт", at(2026, 10, 20, 14, 15), "Сдать отчёт"),
    ("20.10.2026 сдать отчёт", at(2026, 10, 20, 9), "Сдать отчёт"),
    ("в 7 вечера ужин", at(2026, 10, 7, 19), "Ужин"),
    ("в 3 часа дня встреча", at(2026, 10, 7, 15), "Встреча"),
    ("в 9 утра пробежка", at(2026, 10, 8, 9), "Пробежка"),                          # 9 утра уже прошло — завтра
    ("в 12 ночи выключить сервер", at(2026, 10, 8, 0), "Выключить сервер"),
    ("в 14 ч 30 мин созвон", at(2026, 10, 7, 14, 30), "Созвон"),
])
def test_one_time(text, when, what):
    p = parse(text, NOW)
    assert (p.when, p.text, p.repeat) == (when, what, None)


@pytest.mark.parametrize("text, delta, what", [
    ("через 20 минут выключить духовку", timedelta(minutes=20), "Выключить духовку"),
    ("через час проверить почту", timedelta(hours=1), "Проверить почту"),
    ("через полчаса чай", timedelta(minutes=30), "Чай"),
    ("через 2 часа забрать ребёнка", timedelta(hours=2), "Забрать ребёнка"),
    ("через полтора часа выйти", timedelta(hours=1.5), "Выйти"),
    ("через 3 дня продлить домен", timedelta(days=3), "Продлить домен"),
    ("через неделю отзыв клиенту", timedelta(weeks=1), "Отзыв клиенту"),
    ("напомни через пять минут что чайник", timedelta(minutes=5), "Чайник"),
])
def test_relative(text, delta, what):
    p = parse(text, NOW)
    assert (p.when, p.text) == (NOW + delta, what)


@pytest.mark.parametrize("text, first, repeat, what", [
    ("каждый день в 8:00 таблетки", at(2026, 10, 8, 8), "daily", "Таблетки"),
    ("ежедневно в 21:00 полить цветы", at(2026, 10, 7, 21), "daily", "Полить цветы"),
    ("по будням в 8:30 зарядка", at(2026, 10, 8, 8, 30), "weekdays", "Зарядка"),
    ("каждый понедельник в 9 отправить отчёт", at(2026, 10, 12, 9), "weekly:0", "Отправить отчёт"),
    ("каждую пятницу в 17:00 бэкап", at(2026, 10, 9, 17), "weekly:4", "Бэкап"),
    ("по средам в 15 йога", at(2026, 10, 7, 15), "weekly:2", "Йога"),
    ("каждое 1 число оплатить аренду", at(2026, 11, 1, 9), "monthly:1", "Оплатить аренду"),
    ("каждого 25 числа в 10:00 зарплата", at(2026, 10, 25, 10), "monthly:25", "Зарплата"),
])
def test_repeating(text, first, repeat, what):
    p = parse(text, NOW)
    assert (p.when, p.repeat, p.text) == (first, repeat, what)


def test_friday_after_weekend_for_weekdays():
    fri_evening = datetime(2026, 10, 9, 20, 0, tzinfo=MSK)
    assert parse("по будням в 8:30 зарядка", fri_evening).when == at(2026, 10, 12, 8, 30)


@pytest.mark.parametrize("text, hint", [
    ("позвонить маме", "Не понял, когда"),
    ("завтра в 25:00 встреча", "Не бывает времени"),
    ("31.02 оплатить", "Такой даты нет"),
    ("завтра в 15:00", "о чём напомнить"),
    ("", "Пустое"),
])
def test_errors_are_explained(text, hint):
    with pytest.raises(ParseError, match=hint):
        parse(text, NOW)


def test_past_explicit_date_today_is_rejected():
    with pytest.raises(ParseError, match="уже прошло"):
        parse("сегодня в 10:00 созвон", NOW)


def test_numbers_inside_text_are_not_dates():
    p = parse("завтра в 10 позвонить 3 клиентам", NOW)
    assert p.text == "Позвонить 3 клиентам" and p.when == at(2026, 10, 8, 10)


def test_monthly_31_in_short_month_is_last_day():
    after = datetime(2026, 11, 1, 0, 0, tzinfo=MSK)
    assert next_occurrence("monthly:31", time(10), after) == at(2026, 11, 30, 10)


def test_repeat_keeps_local_time_across_dst():
    from datetime import timezone
    berlin = ZoneInfo("Europe/Berlin")
    before = datetime(2026, 10, 24, 8, 0, tzinfo=berlin)       # в ночь на 25 октября Европа переводит часы
    sat = next_occurrence("daily", time(9), before)
    sun = next_occurrence("daily", time(9), sat)
    assert (sat.day, sat.hour, sun.day, sun.hour) == (24, 9, 25, 9)  # по местным часам всё так же 9:00
    utc = timezone.utc
    assert sun.astimezone(utc) - sat.astimezone(utc) == timedelta(hours=25)   # а в реальном времени — 25 часов


def test_descriptions():
    assert describe_when(at(2026, 10, 7, 15), NOW) == "сегодня в 15:00"
    assert describe_when(at(2026, 10, 8, 9), NOW) == "завтра в 09:00"
    assert describe_when(at(2026, 10, 9, 9, 30), NOW) == "в пятницу, 9 октября, в 09:30"
    assert describe_when(at(2027, 3, 5, 9), NOW) == "5 марта 2027 в 09:00"
    assert describe_repeat("weekly:4") == "каждую пятницу"
    assert describe_repeat("weekly:0") == "каждый понедельник"
    assert describe_repeat("monthly:1") == "каждое 1 число"
