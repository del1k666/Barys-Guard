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
    assert whole["iin_bin"].count == 3
    assert whole["card"].count == 2
    assert whole["markings"].count == 2
