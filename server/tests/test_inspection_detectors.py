"""Детекторы ИИН/БИН, карт и словаря; потоковое сканирование."""

import re

import pytest

from barysguard.services.inspection.detectors import (
    CardDetector,
    ContentScanner,
    DictionaryDetector,
    IinBinDetector,
    is_bin,
    is_iin,
    luhn_ok,
    normalize,
)
from barysguard.services.inspection.regex_detector import RegexDetector

# Контрольный разряд посчитан вручную по стандарту (веса 1..11, затем 3..11,1,2).
IIN_FIRST_PASS = "900101300017"  # остаток 7 с первого прохода
IIN_ZERO = "850612412340"  # остаток 0 с первого прохода
IIN_SECOND_PASS = "900101300811"  # первый проход даёт 10, второй — 1
IIN_BOTH_TEN = "900101300800"  # оба прохода дают 10 → номер невалиден
BIN_VALID = "120340000014"  # 5-я цифра 4, месяц 03

VISA = "4111111111111111"
MASTERCARD = "5555555555554444"
AMEX = "378282246310005"
MIR = "2200000000000004"


@pytest.mark.parametrize("number", [IIN_FIRST_PASS, IIN_ZERO, IIN_SECOND_PASS])
def test_valid_iin(number: str) -> None:
    assert is_iin(number) is True


@pytest.mark.parametrize(
    "number",
    [
        "900101300018",  # неверный контрольный разряд
        IIN_BOTH_TEN,  # оба прохода дают 10
        "901301300007",  # месяц 13, контрольный разряд верен
        "900230300009",  # 30 февраля, контрольный разряд верен
        "900101000008",  # 7-я цифра 0, контрольный разряд верен
        "900101700002",  # 7-я цифра 7, контрольный разряд верен
    ],
)
def test_invalid_iin(number: str) -> None:
    assert is_iin(number) is False


def test_valid_and_invalid_bin() -> None:
    assert is_bin(BIN_VALID) is True
    assert is_bin("120340000015") is False  # контрольный разряд
    assert is_bin("121340000007") is False  # месяц 13, контрольный разряд верен
    assert is_bin("120310000000") is False  # 5-я цифра 1, контрольный разряд верен


@pytest.mark.parametrize("number", [VISA, MASTERCARD, AMEX, MIR])
def test_luhn_accepts_known_numbers(number: str) -> None:
    assert luhn_ok(number) is True


def test_luhn_rejects_a_changed_digit() -> None:
    assert luhn_ok("4111111111111112") is False


def _scan(text: str, *detectors) -> dict:
    scanner = ContentScanner(list(detectors))
    scanner.feed(text)
    return scanner.finish()


def test_iin_is_found_masked_and_counted_once() -> None:
    found = _scan(f"ИИН клиента {IIN_FIRST_PASS}; БИН {BIN_VALID}", IinBinDetector())

    assert found["iin_bin"].count == 2
    assert found["iin_bin"].samples == ["**********17", "**********14"]


def test_digits_inside_a_longer_number_are_ignored() -> None:
    found = _scan(f"1{IIN_FIRST_PASS} {IIN_FIRST_PASS}9 {IIN_FIRST_PASS}", IinBinDetector())

    assert found["iin_bin"].count == 1


def test_random_twelve_digits_are_ignored() -> None:
    assert _scan("номер 123456789012", IinBinDetector())["iin_bin"].count == 0


@pytest.mark.parametrize(
    "text",
    [
        VISA,
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        MASTERCARD,
        AMEX,
        MIR,
    ],
)
def test_card_formats_are_found(text: str) -> None:
    found = _scan(f"оплата: {text}.", CardDetector())

    assert found["card"].count == 1
    assert found["card"].samples[0].endswith(text[-4:])
    assert set(found["card"].samples[0][:-4]) == {"*"}


def test_card_rejects_bad_luhn_unknown_prefix_and_long_runs() -> None:
    text = "4111111111111112 9111111111111111 41111111111111111111"
    assert _scan(text, CardDetector())["card"].count == 0


@pytest.mark.parametrize(
    "text",
    [
        "2024-01-15 4111111111111111",
        "Сумма 15000 4111 1111 1111 1111",
        "880101300124 4111 1111 1111 1111",
        "2024 01 15 4111 1111 1111 1111",
    ],
)
def test_card_right_after_another_number_is_found(text: str) -> None:
    found = _scan(text, CardDetector())

    assert found["card"].count == 1
    assert found["card"].samples == ["************1111"]


def test_card_followed_by_an_iin_gives_one_of_each() -> None:
    found = _scan(f"{VISA} {IIN_FIRST_PASS}", IinBinDetector(), CardDetector())

    assert found["card"].count == 1 and found["card"].samples == ["************1111"]
    assert found["iin_bin"].count == 1 and found["iin_bin"].samples == ["**********17"]


def test_card_found_after_a_number_is_counted_once() -> None:
    # Подходящий номер не должен засчитываться повторно хвостом при следующем поиске.
    found = _scan(f"15 {VISA} 2024-01-15 {MASTERCARD}", CardDetector())

    assert found["card"].count == 2
    assert found["card"].samples == ["************1111", "************4444"]


def test_dictionary_ignores_case_yo_and_word_boundaries() -> None:
    detector = DictionaryDetector("markings", ["Конфиденциально", "для служебного пользования"])
    text = "КОНФИДЕНЦИАЛЬНО. Для   служебного\nпользования. неконфиденциально"

    found = _scan(text, detector)

    assert found["markings"].count == 2
    assert found["markings"].samples == ["конфиденциально", "для служебного пользования"]


def test_dictionary_treats_yo_like_ye() -> None:
    assert normalize("Ёлка") == "елка"
    found = _scan("Тёмный список", DictionaryDetector("m", ["темный"]))
    assert found["m"].count == 1


def test_empty_dictionary_finds_nothing() -> None:
    assert _scan("что угодно", DictionaryDetector("m", []))["m"].count == 0


def test_samples_are_capped_at_five() -> None:
    found = _scan(" ".join([IIN_FIRST_PASS] * 9), IinBinDetector())

    assert found["iin_bin"].count == 9
    assert len(found["iin_bin"].samples) == 5


def _document() -> str:
    return (
        "Ведомость. ИИН "
        + IIN_FIRST_PASS
        + ", карта "
        + "4111 1111 1111 1111"
        + ". "
        + "9" * 30
        + " гриф: СТРОГО конфиденциально. "
        + "x" * 200
        + f" БИН {BIN_VALID}; ещё карта {MIR}; ИИН {IIN_ZERO}. "
        + "1" * 40
        + " конфиденциально"
        # Карты сразу после другого числа (дата, сумма, ИИН из таблицы PDF) и ИИН сразу после карты.
        + " 2024-01-15 4111111111111111; Сумма 15000 4111 1111 1111 1111;"
        + " 880101300124 4111 1111 1111 1111; "
        + f"{MASTERCARD} {IIN_SECOND_PASS}; 2024 01 15 {MIR}"
    )


@pytest.mark.parametrize("size", [1, 2, 3, 7, 13, 50, 64, 1000])
def test_chunking_does_not_change_the_result(size: int) -> None:
    detectors = [
        IinBinDetector(),
        CardDetector(),
        DictionaryDetector("markings", ["конфиденциально"]),
    ]
    whole = _scan(_document(), *detectors)

    scanner = ContentScanner(detectors)
    text = _document()
    for start in range(0, len(text), size):
        scanner.feed(text[start : start + size])
    chunked = scanner.finish()

    assert {key: (f.count, f.samples) for key, f in chunked.items()} == {
        key: (f.count, f.samples) for key, f in whole.items()
    }
    assert whole["iin_bin"].count == 4
    assert whole["card"].count == 7
    assert whole["markings"].count == 2


def test_detectors_report_the_span_of_each_match() -> None:
    iin_text = f"номер {IIN_FIRST_PASS} конец"
    [(sample, start, end)] = list(IinBinDetector().find(iin_text, 0, len(iin_text)))
    assert iin_text[start:end] == IIN_FIRST_PASS
    assert sample == "*" * 10 + IIN_FIRST_PASS[-2:]

    card_text = f"карта {VISA[:4]} {VISA[4:8]} {VISA[8:12]} {VISA[12:]} ок"
    [(_, start, end)] = list(CardDetector().find(card_text, 0, len(card_text)))
    assert card_text[start:end].replace(" ", "") == VISA

    word_text = "Это КОНФИДЕНЦИАЛЬНО!"
    [(sample, start, end)] = list(
        DictionaryDetector("m", ["конфиденциально"]).find(word_text, 0, len(word_text))
    )
    assert word_text[start:end] == "КОНФИДЕНЦИАЛЬНО"
    assert sample == "конфиденциально"

    regex_text = "Договор №1234 и №5678"
    spans = [
        regex_text[s:e]
        for _, s, e in RegexDetector("c", r"№\d+").find(regex_text, 0, len(regex_text))
    ]
    assert spans == ["№1234", "№5678"]


def test_only_value_detectors_mask_their_hits() -> None:
    assert IinBinDetector().masks_hits is True
    assert CardDetector().masks_hits is True
    assert RegexDetector("c", r"\d+").masks_hits is True
    assert DictionaryDetector("m", ["гриф"]).masks_hits is False


def _scan_chunks(text: str, size: int, *detectors):
    scanner = ContentScanner(list(detectors))
    for index in range(0, len(text), size):
        scanner.feed(text[index : index + size])
    return scanner.finish()


FILLER = "обычный текст без значений " * 12


def test_fragments_are_collected_with_context_and_capped() -> None:
    text = (FILLER + f"сотрудник {IIN_FIRST_PASS} в списке. ") * 7

    found = ContentScanner([IinBinDetector()])
    found.feed(text)
    result = found.finish()["iin_bin"]

    assert result.count == 7
    assert len(result.fragments) == 5
    first = result.fragments[0]
    assert first["hit"] == "*" * 10 + IIN_FIRST_PASS[-2:]
    assert "сотрудник" in first["before"] and "в списке" in first["after"]
    assert IIN_FIRST_PASS not in repr(result.fragments)


def _fragment_document() -> str:
    return (
        FILLER
        + f"номер {IIN_FIRST_PASS} дальше "
        + FILLER
        + "гриф КОНФИДЕНЦИАЛЬНО тут "
        + FILLER
        + "Договор №1234 подписан "
        + FILLER
        # Соседние значения вплотную: окна фрагментов режут их на разных местах.
        + f"{IIN_SECOND_PASS} {VISA} №77 конфиденциально {IIN_ZERO} "
        + "x" * 97
        + f" {MIR} конец"
    )


def _fragment_detectors() -> tuple:
    return (
        IinBinDetector(),
        CardDetector(),
        DictionaryDetector("markings", ["конфиденциально"]),
        # Малый max_match: запас на стыке задают короткие детекторы, а не шаблон в 200 символов,
        # иначе нехватка правого контекста была бы незаметна.
        RegexDetector("contract", r"№\d+", max_match=20),
    )


@pytest.mark.parametrize("size", [1, 2, 3, 7, 50, 119, 120, 121, 200, 1000])
def test_fragments_do_not_depend_on_chunk_size(size: int) -> None:
    text = _fragment_document()

    whole = _scan_chunks(text, len(text), *_fragment_detectors())
    chunked = _scan_chunks(text, size, *_fragment_detectors())

    assert {k: f.fragments for k, f in chunked.items()} == {
        k: f.fragments for k, f in whole.items()
    }
    assert [len(f.fragments) for f in whole.values()] == [3, 2, 2, 2]


def _long_key_detector() -> RegexDetector:
    # Значение в 153 символа: длиннее прежнего запаса маскирования (40).
    return RegexDetector("k", r"KEY(?: tok\d\d){25}")


@pytest.mark.parametrize("with_long_regex", [False, True])
def test_scanner_gives_full_left_history_to_every_fragment(monkeypatch, with_long_regex) -> None:
    """Контракт build_fragment: не в начале документа — слева не меньше `history` символов.

    `history` сканера считается по его детекторам. Совпадения ставятся на разных
    расстояниях от начала и после вступлений разной длины (фрагментов собирается только
    пять), чтобы при любом размере порции какие-то из них оказались сразу за стыком.
    """
    from barysguard.services.inspection import detectors as module
    from barysguard.services.inspection.fragments import HISTORY, history_for

    def make() -> list:
        return [IinBinDetector(), *([_long_key_detector()] if with_long_regex else [])]

    expected = history_for(make())
    assert expected == (280 if with_long_regex else HISTORY)
    original = module.build_fragment
    calls: list[tuple[int, bool]] = []

    def checked(data, start, end, sample, masks_hits, detectors, at_doc_start, *, history):
        calls.append((start, at_doc_start))
        assert history == expected
        assert at_doc_start or start >= history, (start, at_doc_start)
        return original(
            data, start, end, sample, masks_hits, detectors, at_doc_start, history=history
        )

    monkeypatch.setattr(module, "build_fragment", checked)
    spread = "".join(f"{'ж' * gap} {IIN_FIRST_PASS} " for gap in range(0, 300, 23))
    for size in (1, 2, 3, 5, 13, 64, 119, 120, 121, 133, 279, 280, 281):
        mid_stream = 0
        for lead in (0, 150, 400, 900):
            text = "ж" * lead + " " + spread
            whole = _scan_chunks(text, len(text), *make())
            calls.clear()
            chunked = _scan_chunks(text, size, *make())
            assert chunked["iin_bin"].fragments == whole["iin_bin"].fragments, (size, lead)
            mid_stream += sum(not at_start for _, at_start in calls)
        assert mid_stream > 0, size


@pytest.mark.parametrize("size", [None, 1, 7, 50, 200, 1000])
def test_long_masked_value_does_not_leak_into_a_neighbour_fragment(size: int | None) -> None:
    tokens = [f"tok{index:02d}" for index in range(25)]
    text = "intro " * 30 + "KEY " + " ".join(tokens) + " " + IIN_FIRST_PASS + " end"

    found = _scan_chunks(text, size or len(text), _long_key_detector(), IinBinDetector())

    [iin] = found["iin_bin"].fragments
    [key] = found["k"].fragments
    shown = repr([iin, key])
    assert not any(token in shown for token in tokens), shown
    # Маска длиннее видимых 80 символов и обрезана краем окна — от неё остаётся «…».
    assert iin["before"] == "…"
    assert key["after"] == " " + "*" * 10 + IIN_FIRST_PASS[-2:] + " end"


def test_neighbour_values_stay_masked_across_chunk_boundaries() -> None:
    text = FILLER + f"{IIN_FIRST_PASS} рядом {IIN_SECOND_PASS} и {VISA}. " + FILLER

    for size in (3, 11, 64):
        found = _scan_chunks(text, size, IinBinDetector(), CardDetector())
        shown = repr([f.fragments for f in found.values()])
        assert IIN_FIRST_PASS not in shown
        assert IIN_SECOND_PASS not in shown
        assert VISA not in shown


def test_match_at_the_edges_of_the_document_has_no_false_ellipsis() -> None:
    result = _scan_chunks(f"{IIN_FIRST_PASS} конец", 4, IinBinDetector())["iin_bin"]

    assert result.fragments[0]["before"] == ""
    assert not result.fragments[0]["after"].endswith("…")


@pytest.mark.parametrize("size", [1, 7, 50, 200, 279, 280, 281, 1000])
def test_fragments_with_long_masked_values_do_not_depend_on_chunk_size(size: int) -> None:
    key = "KEY " + " ".join(f"tok{index:02d}" for index in range(25))
    text = (
        FILLER
        + f"{key} {IIN_FIRST_PASS} "
        + FILLER
        + f"{IIN_SECOND_PASS} затем {key} "
        + FILLER
        + f"{IIN_ZERO} "
        + FILLER
        # Второе длинное значение близко справа: оно задевает видимую часть окна первого
        # и кончается далеко за ней — правого запаса должно хватить на него целиком.
        + f"{key} {'ж' * 60} {key} "
        + FILLER
    )

    whole = _scan_chunks(text, len(text), _long_key_detector(), IinBinDetector())
    chunked = _scan_chunks(text, size, _long_key_detector(), IinBinDetector())

    assert {k: f.fragments for k, f in chunked.items()} == {
        k: f.fragments for k, f in whole.items()
    }
    assert "tok" not in repr([f.fragments for f in whole.values()])


SPACED_CARD = "4111 1111 1111 1111"


@pytest.mark.parametrize("size", [None, 1, 7, 50, 200, 1000])
def test_whitespace_heavy_window_does_not_show_the_tail_of_a_value(size: int | None) -> None:
    """Видимая зона считается в исходных символах, а не после схлопывания пробелов.

    Окно маскирования (120 символов) захватывает только хвост карты «1111 1111 1111»
    (12 цифр — уже не карта), остальное — пробелы и переводы строк. После схлопывания
    всё окно уместилось бы в 80 видимых символов, и хвост карты был бы показан.
    """
    gap = " \n" * 53  # 106 пробельных символов: окно = 14 символов карты + gap
    text = f"начало {SPACED_CARD}{gap}{IIN_FIRST_PASS}{gap}{SPACED_CARD} конец"

    found = _scan_chunks(text, size or len(text), IinBinDetector(), CardDetector())

    [iin] = found["iin_bin"].fragments
    assert iin == {"before": "…", "hit": "*" * 10 + IIN_FIRST_PASS[-2:], "after": "…"}
    for finding in found.values():
        for fragment in finding.fragments:
            for side in (fragment["before"], fragment["after"]):
                assert re.search(r"\d{4}", side) is None, fragment


IIN_DRIFT = "900101300007"
NEAR_CARD = " ".join(VISA)  # 31 исходный символ → маска из 16: маска короче значения
STRADDLING_CARD = " ".join(MASTERCARD)


def _drift_text(side: str, gap: int) -> str:
    if side == "left":
        return (
            "ж" * 200
            + f" ж {STRADDLING_CARD} ж "
            + "ж" * gap
            + f" ж {NEAR_CARD} ж {NEAR_CARD} ж {IIN_DRIFT} "
            + "ж" * 200
        )
    return (
        "ж" * 200
        + f" {IIN_DRIFT} ж {NEAR_CARD} ж {NEAR_CARD} ж "
        + "ж" * gap
        + f" ж {STRADDLING_CARD} ж "
        + "ж" * 200
    )


def _assert_only_masks_show_digits(fragment: dict[str, str]) -> None:
    from barysguard.services.inspection.fragments import FRAGMENT_CONTEXT

    for side in (fragment["before"], fragment["after"]):
        assert len(side) <= FRAGMENT_CONTEXT + 1, fragment
        # Законные маски: звёздочки и не больше четырёх открытых цифр в конце.
        assert not re.search(r"\d", re.sub(r"\*+\d{1,4}(?!\d)", "", side)), fragment


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("size", [None, 1, 7, 50, 200, 1000])
def test_short_masks_do_not_pull_the_visible_zone_into_the_reserve(side: str, size) -> None:
    """Видимая зона меряется в исходных символах окна, а не в символах после маскирования.

    Две карты, записанные через пробел, дают маски вдвое короче себя; если отсчитывать
    80 символов по замаскированному тексту, зона уезжает в запас и показывает цифры
    карты, разрезанной краем окна.
    """
    assert is_iin(IIN_DRIFT)
    text = _drift_text(side, 16)

    found = _scan_chunks(text, size or len(text), IinBinDetector(), CardDetector())

    [iin] = found["iin_bin"].fragments
    _assert_only_masks_show_digits(iin)


@pytest.mark.parametrize("side", ["left", "right"])
def test_short_masks_never_leak_at_any_gap(side: str) -> None:
    for gap in range(0, 120):
        found = _scan_chunks(_drift_text(side, gap), 10**6, IinBinDetector(), CardDetector())
        for finding in found.values():
            for fragment in finding.fragments:
                _assert_only_masks_show_digits(fragment)


# Разделители, которые склеивают цифры в одно число для проверки фрагментов.
_JOINER = (
    r"["
    r"\s\-\u2010-\u2015\u2212\ufe58\ufe63\uff0d\u00ad\u180e\u200b-\u200f\u2060-\u2064\ufeff"
    r".,/\\_()']"
)
_RUN = re.compile(rf"\d(?:{_JOINER}*\d)*")


def _assert_no_open_numbers(fragment: dict[str, str]) -> None:
    """Инвариант фрагмента, не зависящий от детекторов.

    В before/after не бывает числа из пяти и больше цифр (цифры могут быть разделены
    пробелами, дефисами и т. п.). Короткое число допустимо, только если оно не касается
    края: ни совпадения (конец before, начало after), ни обреза «…» — у края число могло
    продолжаться. Хвосты наших масок (`*` и до четырёх цифр) — такие же короткие числа.
    """
    from barysguard.services.inspection.fragments import FRAGMENT_CONTEXT

    edge = re.compile(rf"{_JOINER}*")
    for name in ("before", "after"):
        side = fragment[name]
        assert len(side) <= FRAGMENT_CONTEXT + 1, fragment
        for match in _RUN.finditer(side):
            digits = len(re.findall(r"\d", match.group()))
            assert digits < 5, (match.group(), fragment)
            head, tail = side[: match.start()], side[match.end() :]
            if name == "before":
                touches_hit = edge.fullmatch(tail)
                touches_cut = head.startswith("…") and edge.fullmatch(head[1:])
            else:
                touches_hit = edge.fullmatch(head)
                touches_cut = tail.endswith("…") and edge.fullmatch(tail[:-1])
            assert not touches_hit and not touches_cut, (match.group(), fragment)


# Повторы ревью: карта, найденная в полном тексте, не находится в окне маскирования,
# потому что совпадения карт сцепляются (ложная карта у края окна съедает первую цифру
# настоящей), и её цифры видны в контексте ИИН.
CHAINED_CARDS = [
    "x" * 200
    + " 22 213 251232-4316 4  2-2-2 1 1-7-2 9 26 5 9-0-2-47 516279183-573832 6 4 0-84 924"
    + " 812092  0532-35 "
    + "x" * 48
    + f" {IIN_DRIFT} "
    + "x" * 200,
    "x" * 200
    + " 4 40 22 2 0 7 5 1-7 5-87 62 8 2 2 0 0 3-7 032 6 8038 6 4 07845-8627 2 2 2 1 3 9 5 8"
    + " 5 1-1 6 2 3-1-6 "
    + "x" * 54
    + f" {IIN_DRIFT} "
    + "x" * 200,
]


@pytest.mark.parametrize("case", [0, 1])
@pytest.mark.parametrize("size", [None, 1, 7, 50, 200, 1000])
def test_values_missed_in_the_window_are_not_shown(case: int, size: int | None) -> None:
    text = CHAINED_CARDS[case]

    found = _scan_chunks(text, size or len(text), IinBinDetector(), CardDetector())

    assert found["iin_bin"].fragments
    for finding in found.values():
        for fragment in finding.fragments:
            _assert_no_open_numbers(fragment)


@pytest.mark.parametrize(
    "value",
    [
        # ИИН (или БИН) внутри карты: в окне карта не находится, её голова касается совпадения.
        " ж ж 22-0-090746363219 0-04172 ж ",
        # 19-значная Visa, кончающаяся ИИН: семь первых цифр стоят вплотную к ИИН.
        " ж 4 0 0 0 0 0 2-" + IIN_DRIFT + " ж ",
    ],
)
@pytest.mark.parametrize("size", [None, 1, 7, 50])
def test_head_of_a_value_overlapping_the_hit_is_not_shown(value: str, size: int | None) -> None:
    text = "x" * 200 + value + "x" * 200

    found = _scan_chunks(text, size or len(text), IinBinDetector(), CardDetector())

    assert found["iin_bin"].fragments
    for finding in found.values():
        for fragment in finding.fragments:
            _assert_no_open_numbers(fragment)


@pytest.mark.parametrize("separator", ["\u00a0", "\t", "\u2028", "  ", "\u2009", "\u200b"])
@pytest.mark.parametrize("size", [None, 7])
def test_card_with_unusual_separators_is_not_shown(separator: str, size: int | None) -> None:
    card = separator.join(VISA[index : index + 4] for index in range(0, 16, 4))
    text = "x" * 200 + f" ж {card} ж {IIN_DRIFT} ж {card} ж " + "x" * 200

    found = _scan_chunks(text, size or len(text), IinBinDetector(), CardDetector())

    [iin] = found["iin_bin"].fragments
    _assert_no_open_numbers(iin)
    assert "1111" not in iin["before"] + iin["after"]


_FUZZ_SEPARATORS = ["", "", "", " ", " ", "-", "  ", "\u00a0", "\t", "\n", "\u2013", "\u200b"]


def _fuzz_card(rng) -> str:
    prefix = rng.choice(["4", "51", "55", "2200", "2221", "37"])
    length = 15 if prefix == "37" else rng.choice([13, 16, 19]) if prefix == "4" else 16
    body = prefix + "".join(rng.choice("0123456789") for _ in range(length - len(prefix) - 1))
    return next(body + check for check in "0123456789" if luhn_ok(body + check))


def _fuzz_number(rng, digits: str) -> str:
    weight = rng.random()
    out = digits[0]
    for char in digits[1:]:
        out += rng.choice(_FUZZ_SEPARATORS) if rng.random() < weight else ""
        out += char
    return out


def _fuzz_region(rng) -> str:
    parts = []
    for _ in range(rng.randint(2, 7)):
        if rng.random() < 0.6:
            digits = _fuzz_card(rng)
        else:
            digits = "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 13)))
        parts.append(_fuzz_number(rng, digits))
        parts.append(rng.choice([" ", "-", " ", "", "  ", " ж ", "\u00a0"]))
    return "".join(parts)


def test_random_numbers_around_a_hit_never_show_open_digits() -> None:
    """Свойство на случайных данных: группы цифр с разными разделителями вокруг ИИН."""
    import random

    rng = random.Random(20261010)  # noqa: S311 - воспроизводимые данные теста, не криптография
    for _ in range(2000):
        left, right = _fuzz_region(rng), _fuzz_region(rng)
        text = (
            "x" * rng.randint(0, 200)
            + f" {left}{'x' * rng.randint(0, 90)} {IIN_DRIFT} {'x' * rng.randint(0, 90)}{right} "
            + "x" * rng.randint(0, 200)
        )
        for size in (len(text), 7):
            found = _scan_chunks(text, size, IinBinDetector(), CardDetector())
            assert found["iin_bin"].fragments, text
            for finding in found.values():
                for fragment in finding.fragments:
                    _assert_no_open_numbers(fragment)
