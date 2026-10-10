"""Фрагменты вокруг находок: маскирование контекста и обрезка краёв."""

import re
from collections.abc import Iterator

from barysguard.services.inspection.detectors import (
    CardDetector,
    DictionaryDetector,
    IinBinDetector,
)
from barysguard.services.inspection.fragments import (
    FRAGMENT_CONTEXT,
    build_fragment,
    mask_text,
)

IIN = "900101300017"
IIN_OTHER = "900101300811"
VISA = "4111111111111111"
DETECTORS = [IinBinDetector(), CardDetector(), DictionaryDetector("markings", ["конфиденциально"])]


def _fragment(text: str, needle: str, masks: bool = True, at_start: bool = True) -> dict[str, str]:
    start = text.index(needle)
    end = start + len(needle)
    sample = "*" * 10 + needle[-2:] if masks else needle.lower()
    return build_fragment(text, start, end, sample, masks, DETECTORS, at_start)


def test_mask_text_replaces_other_values_but_keeps_dictionary_terms() -> None:
    text = f"ИИН {IIN_OTHER}, карта 4111 1111 1111 1111, гриф конфиденциально"

    masked = mask_text(text, DETECTORS)

    assert IIN_OTHER not in masked
    assert "4111 1111" not in masked
    assert "конфиденциально" in masked


def test_hit_is_the_mask_and_context_is_masked() -> None:
    text = f"Сотрудник {IIN} вместе с {IIN_OTHER} и картой {VISA} в списке."

    fragment = _fragment(text, IIN)

    assert fragment["hit"] == "*" * 10 + "17"
    joined = fragment["before"] + fragment["hit"] + fragment["after"]
    assert IIN not in joined and IIN_OTHER not in joined and VISA not in joined
    assert not re.search(r"\d{6,}", joined)
    assert "в списке" in fragment["after"]


def test_dictionary_hit_keeps_original_case() -> None:
    text = "Документ имеет гриф КОНФИДЕНЦИАЛЬНО и не подлежит выдаче."

    fragment = _fragment(text, "КОНФИДЕНЦИАЛЬНО", masks=False)

    assert fragment["hit"] == "КОНФИДЕНЦИАЛЬНО"
    assert fragment["before"].endswith("гриф ")


def test_edges_of_the_document_have_no_ellipsis() -> None:
    text = f"{IIN} в начале и конце {IIN_OTHER}"

    first = _fragment(text, IIN)

    assert first["before"] == ""
    assert not first["after"].endswith("…")


def test_cut_context_drops_the_partial_word_and_marks_the_cut() -> None:
    filler = "слово " * 40
    text = f"{filler}{IIN} {filler}"

    fragment = _fragment(text, IIN)

    assert fragment["before"].startswith("…")
    assert fragment["after"].endswith("…")
    assert len(fragment["before"]) <= FRAGMENT_CONTEXT + 1
    assert len(fragment["after"]) <= FRAGMENT_CONTEXT + 1


def test_number_cut_by_the_window_edge_is_not_shown() -> None:
    # Число внутри окна истории, но через левый край видимых 80 символов.
    for value in (IIN_OTHER, "4111 1111 1111 1111"):
        for gap in range(70, 95):
            text = "а" * 150 + f" {value} " + "б" * gap + f" {IIN} " + "в" * 200

            fragment = _fragment(text, IIN)

            assert not re.search(r"\d{6,}", fragment["before"]), (value, gap)
            assert not re.search(r"\d{4} \d{4}", fragment["before"]), (value, gap)


def test_number_cut_by_the_right_window_edge_is_not_shown() -> None:
    for value in (IIN_OTHER, "4111 1111 1111 1111"):
        for gap in range(70, 95):
            text = "а" * 50 + f" {IIN} " + "б" * gap + f" {value} " + "в" * 200

            fragment = _fragment(text, IIN)

            assert not re.search(r"\d{6,}", fragment["after"]), (value, gap)
            assert not re.search(r"\d{4} \d{4}", fragment["after"]), (value, gap)


class _Span:
    """Заглушка детектора с заданными совпадениями."""

    key = "stub"
    max_length = 100
    masks_hits = True

    def __init__(self, *spans: tuple[str, int, int]) -> None:
        self._spans = spans

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int, int]]:
        yield from self._spans


def test_mask_text_shared_start_keeps_only_the_longer_mask() -> None:
    text = "900101300017 1234 5678 9012 конец"
    short = _Span(("SHORT", 0, 12))
    long = _Span(("LONG", 0, 27))

    for detectors in ([short, long], [long, short]):
        masked = mask_text(text, detectors)  # type: ignore[arg-type]

        assert masked == "LONG конец"


def test_mask_text_partial_overlap_swallows_the_tail() -> None:
    text = "900101300017 1234 5678 9012 конец"
    first = _Span(("AAA", 0, 12))
    second = _Span(("BBB", 5, 27))

    masked = mask_text(text, [first, second])  # type: ignore[arg-type]

    assert masked == "AAA конец"
    assert not re.search(r"\d{4}", masked)


def test_not_at_document_start_marks_the_left_cut() -> None:
    text = f"середина {IIN} дальше"

    fragment = _fragment(text, IIN, at_start=False)

    assert fragment["before"].startswith("…")


class _ShortMask:
    """Детектор с маской короче значения: «S» * 30 → `mask`.

    Маска с пробелами показывает, что значение на краю зоны отброшено целиком, а не
    заменено маской, которую потом срезало бы отбрасывание недорезанного слова.
    """

    key = "short"
    max_length = 30
    masks_hits = True

    def __init__(self, mask: str = "M") -> None:
        self.mask = mask

    def find(self, data: str, start: int, end: int) -> Iterator[tuple[str, int, int]]:
        for match in re.finditer("S{30}", data[start:]):
            if start + match.start() >= end:
                return
            yield self.mask, start + match.start(), start + match.end()


def test_value_crossing_the_visible_zone_edge_is_dropped_whole() -> None:
    # Окно слева — 99 символов, видимая зона — последние 80; «S»*30 начинается до неё.
    window = "начало " + "S" * 30 + " " + "в" * 60 + " "
    text = window + IIN + " конец"
    start = len(window)

    fragment = build_fragment(
        text, start, start + len(IIN), "*" * 10 + "17", True, [_ShortMask("x y z")], True
    )

    assert fragment["before"] == "…" + "в" * 60 + " "
    assert fragment["after"] == " конец"


def test_value_crossing_the_right_zone_edge_is_dropped_whole() -> None:
    text = "начало " + IIN + " " + "в" * 60 + " " + "S" * 30 + " конец"
    start = text.index(IIN)

    fragment = build_fragment(
        text, start, start + len(IIN), "*" * 10 + "17", True, [_ShortMask("x y z")], True
    )

    assert fragment["before"] == "начало "
    assert fragment["after"] == " " + "в" * 60 + "…"


def test_value_inside_the_zone_is_masked_and_sides_stay_bounded() -> None:
    text = "S" * 30 + " " + IIN + " " + "S" * 30 + " " + "я" * 200
    start = text.index(IIN)

    fragment = build_fragment(
        text, start, start + len(IIN), "*" * 10 + "17", True, [_ShortMask()], True
    )

    assert fragment["before"] == "M "
    # Справа видны 80 исходных символов: маска, пробел и 48 «я» — недорезанное слово отброшено.
    assert fragment["after"] == " M…"


def test_masks_are_never_longer_than_their_values() -> None:
    from barysguard.services.inspection.regex_detector import RegexDetector

    text = f"{IIN} 4111 1111 1111 1111 {VISA} 3782 822463 10005 №12345 " + " ".join(
        "2200000000000004"
    )
    detectors = [*DETECTORS, RegexDetector("c", r"№\d+")]
    for detector in detectors:
        if detector.masks_hits:
            for sample, start, end in detector.find(text, 0, len(text)):
                assert len(sample) <= end - start, (detector.key, sample)
