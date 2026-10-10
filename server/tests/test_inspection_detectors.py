"""Детекторы ИИН/БИН, карт и словаря; потоковое сканирование."""

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


def test_scanner_gives_full_left_history_to_every_fragment(monkeypatch) -> None:
    """Контракт build_fragment: не в начале документа — слева не меньше HISTORY символов.

    Совпадения ставятся на разных расстояниях от начала, чтобы при любом размере
    порции какое-то из них оказалось сразу за стыком.
    """
    from barysguard.services.inspection import detectors as module
    from barysguard.services.inspection.fragments import HISTORY

    original = module.build_fragment
    calls: list[tuple[int, bool]] = []

    def checked(data, start, end, sample, masks_hits, detectors, at_doc_start):
        calls.append((start, at_doc_start))
        assert at_doc_start or start >= HISTORY, (start, at_doc_start)
        return original(data, start, end, sample, masks_hits, detectors, at_doc_start)

    monkeypatch.setattr(module, "build_fragment", checked)
    text = "".join(f"{'ж' * gap} {IIN_FIRST_PASS} " for gap in range(0, 300, 23))
    whole = _scan_chunks(text, len(text), IinBinDetector())
    for size in (1, 2, 3, 5, 13, 64, 119, 120, 121, 133):
        calls.clear()
        chunked = _scan_chunks(text, size, IinBinDetector())
        assert chunked["iin_bin"].fragments == whole["iin_bin"].fragments, size
        assert any(not at_start for _, at_start in calls), size


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
