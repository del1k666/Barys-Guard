"""RegexDetector (RE2) и регистр/ё в словарном детекторе без глобальной нормализации."""

import pytest

from barysguard.services.inspection.detectors import (
    CardDetector,
    ContentScanner,
    DictionaryDetector,
    IinBinDetector,
    mask_tail,
)
from barysguard.services.inspection.regex_detector import (
    PatternError,
    RegexDetector,
    compile_pattern,
)


def _scan(text: str, *detectors):
    scanner = ContentScanner(list(detectors))
    scanner.feed(text)
    return scanner.finish()


def test_mask_keeps_the_last_two_characters() -> None:
    assert mask_tail("ALFA-123") == "******23"
    assert mask_tail("ab") == "**"
    assert mask_tail("abc") == "*bc"
    assert mask_tail("") == ""


def test_regex_finds_matches_masked() -> None:
    found = _scan("Договор №1234-567 и ещё № 9999-000.", RegexDetector("c", r"№\s?\d{4}-\d{3}"))

    assert found["c"].count == 2
    assert all(set(s[:-2]) == {"*"} for s in found["c"].samples)
    assert "1234-567" not in repr(found["c"].samples)


def test_ignore_case_applies_to_cyrillic() -> None:
    sensitive = RegexDetector("c", r"договор", ignore_case=False)
    insensitive = RegexDetector("c", r"договор", ignore_case=True)

    assert _scan("ДОГОВОР", sensitive)["c"].count == 0
    assert _scan("ДОГОВОР", insensitive)["c"].count == 1


def test_unicode_classes_work_but_w_is_ascii_only() -> None:
    assert _scan("слово", RegexDetector("c", r"\p{L}+"))["c"].count == 1
    assert _scan("слово", RegexDetector("c", r"\w+"))["c"].count == 0


def test_match_longer_than_the_limit_is_ignored() -> None:
    detector = RegexDetector("c", r"x{5,}", max_match=10)

    assert _scan("x" * 30, detector)["c"].count == 0
    assert _scan("xxxxxxx", detector)["c"].count == 1


@pytest.mark.parametrize("size", [1, 2, 5, 7, 11, 13, 25, 100])
def test_overlong_run_is_not_counted_by_its_suffix_at_any_chunking(size: int) -> None:
    detector = RegexDetector("c", r"x{5,}", max_match=10)
    text = "x" * 40 + " end"
    scanner = ContentScanner([detector])
    for start in range(0, len(text), size):
        scanner.feed(text[start : start + size])

    assert scanner.finish()["c"].count == 0


@pytest.mark.parametrize(
    "text",
    ["123456" * 10, "1234567890" * 6, "12 " * 20 + "123456789" * 3],
)
def test_adjacent_matches_are_not_swallowed_at_any_chunking(text: str) -> None:
    def count(size: int) -> int:
        scanner = ContentScanner([RegexDetector("c", r"\d{3}", max_match=5)])
        for start in range(0, len(text), size):
            scanner.feed(text[start : start + size])
        return scanner.finish()["c"].count

    whole = _scan(text, RegexDetector("c", r"\d{3}", max_match=5))["c"].count
    assert whole > 0
    assert [count(size) for size in range(1, 70)] == [whole] * 69


@pytest.mark.parametrize("pattern", [r"x{5,}", r"x{2,}", r"x+", r"a|ab", r"ab|b", r"\d+"])
def test_chunk_sweep_is_invariant_for_overlong_and_short_runs(pattern: str) -> None:
    text = "a ab x" + "x" * 25 + " 12 " + "1" * 30 + " ab abab xxxxxx b " + "x" * 13 + "ab" * 9
    whole = _scan(text, RegexDetector("c", pattern, max_match=10))["c"].count
    for size in range(1, 70):
        scanner = ContentScanner([RegexDetector("c", pattern, max_match=10)])
        for start in range(0, len(text), size):
            scanner.feed(text[start : start + size])
        assert scanner.finish()["c"].count == whole, (pattern, size)


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (r"x{5,}", "x" * 40 + " end"),
        (r"x{12}", "x" * 40 + " end"),
        (r"\d{6,}", "ab " + "7" * 45 + " cd 123456 ef"),
        (r"x{5,}", "x" * 11 + " " + "x" * 3 + " " + "x" * 30),
    ],
)
def test_overlong_run_is_skipped_at_every_chunk_size(pattern: str, text: str) -> None:
    whole = _scan(text, RegexDetector("c", pattern, max_match=10))["c"].count
    for size in range(1, 70):
        scanner = ContentScanner([RegexDetector("c", pattern, max_match=10)])
        for start in range(0, len(text), size):
            scanner.feed(text[start : start + size])
        assert scanner.finish()["c"].count == whole, (pattern, size)


@pytest.mark.parametrize("pattern", [r"(?=a)b", r"(?<=a)b", r"(a)\1", "(", "[a-"])
def test_unsupported_or_broken_patterns_raise_pattern_error(pattern: str) -> None:
    with pytest.raises(PatternError) as caught:
        compile_pattern(pattern, False)

    assert caught.value.message


def test_dictionary_ignores_case_and_yo_without_global_normalisation() -> None:
    detector = DictionaryDetector("m", ["Тёмный список", "секретно"])

    found = _scan("ТЁМНЫЙ СПИСОК, темный   список и СЕКРЕТНО", detector)

    assert found["m"].count == 3
    assert found["m"].samples[0] == "темный список"  # образец нормализован


def _document() -> str:
    return (
        "Договор №1234-567 заключён. Код ALFA-123 и alfa-99999. "
        + "9" * 30
        + " ИИН 900101300017 карта 4111 1111 1111 1111. Строго конфиденциально. "
        + "x" * 150
        + " Договор № 9999-000, ALFA-77 конец. секретно"
    )


@pytest.mark.parametrize("size", [1, 2, 3, 7, 13, 50, 64, 1000])
def test_chunking_does_not_change_the_result_with_regex(size: int) -> None:
    def detectors():
        return [
            IinBinDetector(),
            CardDetector(),
            DictionaryDetector("markings", ["конфиденциально", "секретно"]),
            RegexDetector("contract", r"№\s?\d{4}-\d{3}"),
            RegexDetector("alfa", r"alfa-\d{2,5}", ignore_case=True),
        ]

    whole = _scan(_document(), *detectors())
    scanner = ContentScanner(detectors())
    text = _document()
    for start in range(0, len(text), size):
        scanner.feed(text[start : start + size])
    chunked = scanner.finish()

    assert {k: (f.count, f.samples) for k, f in chunked.items()} == {
        k: (f.count, f.samples) for k, f in whole.items()
    }
    assert (whole["contract"].count, whole["alfa"].count) == (2, 3)
    assert whole["markings"].count == 2
