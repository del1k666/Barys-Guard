"""Извлечение текста и скан байтов: форматы, пределы, отказы."""

import io
import zipfile
from collections.abc import Iterator

import pytest
from pypdf import PdfWriter

from barysguard.services.inspection.detectors import CardDetector, IinBinDetector
from barysguard.services.inspection.engine import ScanOutcome, scan_bytes
from barysguard.services.inspection.extract import Limits

IIN = "900101300017"
CARD = "4111 1111 1111 1111"
LIMITS = Limits(
    max_text=1_000_000, max_unpacked=50_000_000, max_entries=1000, max_ratio=100, timeout=30.0
)
DETECTORS = [IinBinDetector(), CardDetector()]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _scan(name: str, data: bytes, limits: Limits = LIMITS) -> ScanOutcome:
    return scan_bytes(name, data, DETECTORS, limits)


def _zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_pdf(text: str, hex_string: bool = False) -> bytes:
    shown = f"<{text.encode('latin-1').hex()}>" if hex_string else f"({text})"
    stream = f"BT /F1 12 Tf 72 720 Td {shown} Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def test_plain_text_utf8_and_cp1251() -> None:
    utf8 = _scan("C:\\x\\a.txt", f"ИИН {IIN}".encode())
    cp1251 = _scan("E:\\b.csv", f"клиент;{IIN};карта;{CARD}".encode("cp1251"))

    assert utf8.status == "ok" and utf8.findings["iin_bin"]["count"] == 1
    assert cp1251.status == "ok"
    assert cp1251.findings["iin_bin"]["count"] == 1 and cp1251.findings["card"]["count"] == 1


def test_findings_hold_only_nonzero_counts_and_masks() -> None:
    outcome = _scan("a.txt", f"ИИН {IIN}".encode())

    assert set(outcome.findings) == {"iin_bin"}
    assert outcome.findings["iin_bin"]["samples"] == ["**********17"]
    assert IIN not in repr(outcome.findings)


def test_empty_and_blank_files_have_no_text() -> None:
    assert _scan("a.txt", b"").status == "no_text"
    assert _scan("a.txt", b"  \n\t ").status == "no_text"


def test_unknown_extension_is_unsupported() -> None:
    assert _scan("photo.jpg", b"\xff\xd8\xff").status == "unsupported"
    assert _scan("archive.zip", b"PK\x03\x04").status == "unsupported"


def test_docx_text_split_across_runs_is_found() -> None:
    document = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body><w:p>'
        f"<w:r><w:t>ИИН 90010</w:t></w:r><w:r><w:t>1300017</w:t></w:r>"
        f"</w:p></w:body></w:document>"
    )

    outcome = _scan("report.docx", _zip({"word/document.xml": document}))

    assert outcome.status == "ok" and outcome.findings["iin_bin"]["count"] == 1


def test_xlsx_numeric_cell_and_shared_string_are_read() -> None:
    sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData><row>'
        f'<c r="A1"><v>{IIN}</v></c><c r="B1" t="s"><v>0</v></c>'
        f'<c r="C1" t="inlineStr"><is><t>{CARD}</t></is></c>'
        f"</row></sheetData></worksheet>"
    )
    strings = f'<?xml version="1.0"?><sst xmlns="{S}"><si><t>просто слово</t></si></sst>'

    outcome = _scan(
        "pay.xlsx", _zip({"xl/worksheets/sheet1.xml": sheet, "xl/sharedStrings.xml": strings})
    )

    assert outcome.status == "ok"
    assert outcome.findings["iin_bin"]["count"] == 1
    assert outcome.findings["card"]["count"] == 1


def test_pptx_slide_text_is_read() -> None:
    slide = (
        f'<?xml version="1.0"?><p:sld xmlns:p="urn:p" xmlns:a="{A}"><a:p><a:r>'
        f"<a:t>карта {CARD}</a:t></a:r></a:p></p:sld>"
    )

    outcome = _scan("deck.pptx", _zip({"ppt/slides/slide1.xml": slide}))

    assert outcome.findings["card"]["count"] == 1


def test_pdf_text_layer_is_read() -> None:
    outcome = _scan("a.pdf", make_pdf(f"IIN {IIN}"))

    assert outcome.status == "ok" and outcome.findings["iin_bin"]["count"] == 1


def test_pdf_without_text_has_no_text() -> None:
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    buffer = io.BytesIO()
    writer.write(buffer)

    assert _scan("scan.pdf", buffer.getvalue()).status == "no_text"


def test_password_protected_pdf_is_encrypted() -> None:
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)

    assert _scan("secret.pdf", buffer.getvalue()).status == "encrypted"


def test_password_protected_office_file_is_encrypted() -> None:
    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 64

    assert _scan("secret.docx", ole).status == "encrypted"


def test_corrupt_office_and_pdf_files_are_errors_not_crashes() -> None:
    assert _scan("broken.docx", b"not a zip at all").status == "error"
    assert _scan("broken.pdf", b"%PDF-1.4 garbage").status == "error"
    bad_xml = _zip({"word/document.xml": "<w:document"})
    assert _scan("bad.docx", bad_xml).status == "error"


def test_xml_with_a_doctype_is_refused() -> None:
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'

    assert _scan("evil.docx", _zip({"word/document.xml": bomb})).status == "error"


def test_zip_bomb_by_ratio_is_too_large() -> None:
    payload = "<w:document xmlns:w='x'>" + "<w:t>0</w:t>" * 400_000 + "</w:document>"
    bomb = _zip({"word/document.xml": payload})

    assert _scan("bomb.docx", bomb).status == "too_large"


def test_zip_with_too_many_entries_is_too_large() -> None:
    many = _zip({f"f{i}.xml": "x" for i in range(5)})
    limits = Limits(max_text=10**6, max_unpacked=10**9, max_entries=3, max_ratio=100, timeout=30.0)

    assert _scan("many.docx", many, limits).status == "too_large"


def test_unpacked_size_limit_is_enforced() -> None:
    body = "a " * 5000
    document = (
        f'<w:document xmlns:w="{W}"><w:body><w:p><w:t>{body}</w:t></w:p></w:body></w:document>'
    )
    limits = Limits(
        max_text=10**6, max_unpacked=1000, max_entries=10, max_ratio=10**6, timeout=30.0
    )

    assert _scan("big.docx", _zip({"word/document.xml": document}), limits).status == "too_large"


def test_text_is_truncated_at_the_limit() -> None:
    limits = Limits(max_text=100, max_unpacked=10**9, max_entries=10, max_ratio=100, timeout=30.0)
    text = ("x" * 90 + " ") + f"{IIN} " + "y" * 500

    outcome = _scan("a.txt", text.encode(), limits)

    assert outcome.status == "ok" and outcome.truncated is True
    assert "iin_bin" not in outcome.findings  # ИИН лежит за пределом 100 символов


def test_timeout_gives_an_error_status() -> None:
    limits = Limits(max_text=10**6, max_unpacked=10**9, max_entries=10, max_ratio=100, timeout=-1.0)

    assert _scan("a.txt", b"hello", limits).status == "error"


def test_extension_is_taken_from_a_windows_path() -> None:
    assert _scan("E:\\Папка с пробелами\\Файл.TXT", f"{IIN}".encode()).status == "ok"


def _corrupt_member_zip() -> bytes:
    text = "".join(f"строка {i} слово{i % 7} " for i in range(20000))
    document = (
        f'<w:document xmlns:w="{W}"><w:body><w:p><w:t>{text}</w:t></w:p></w:body></w:document>'
    )
    return _zip({"word/document.xml": document})


def test_corrupt_deflate_member_is_an_error() -> None:
    data = bytearray(_corrupt_member_zip())
    info = zipfile.ZipFile(io.BytesIO(bytes(data))).infolist()[0]
    start = info.header_offset + 30 + len(info.filename.encode()) + 100
    for offset in range(start, start + 200):
        data[offset] ^= 0xFF

    assert _scan("bad.docx", bytes(data)).status == "error"


def test_truncated_deflate_member_is_an_error() -> None:
    data = _corrupt_member_zip()
    info = zipfile.ZipFile(io.BytesIO(data)).infolist()[0]
    body = info.header_offset + 30 + len(info.filename.encode())
    # Центральный каталог цел, но поток сжатых данных обрезан: нули вместо хвоста потока.
    cut = body + info.compress_size // 2
    broken = (
        data[:cut] + b"\x00" * (body + info.compress_size - cut) + data[body + info.compress_size :]
    )

    assert _scan("cut.docx", broken).status == "error"


def test_unexpected_parser_failure_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("barysguard.services.inspection.engine.extract", boom)

    assert _scan("a.txt", b"hello").status == "error"


# --- Формат по содержимому, а не по имени ---------------------------------------


def _docx_with_secrets() -> bytes:
    document = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>'
        f"<w:p><w:r><w:t>ИИН {IIN}</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t>карта {CARD}</w:t></w:r></w:p>"
        f"</w:body></w:document>"
    )
    return _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": document})


@pytest.mark.parametrize("name", ["a.txt", "a.docx", "a.bin", "C:\\x\\a", "a.pdf"])
def test_same_docx_bytes_give_the_same_scan_under_any_name(name: str) -> None:
    reference = _scan("a.docx", _docx_with_secrets())

    outcome = _scan(name, _docx_with_secrets())

    assert reference.status == "ok"
    assert reference.findings["iin_bin"]["count"] == 1
    assert reference.findings["card"]["count"] == 1
    assert outcome == reference


def test_xlsx_and_pptx_are_recognised_by_members_not_by_name() -> None:
    sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData><row>'
        f'<c r="A1"><v>{IIN}</v></c></row></sheetData></worksheet>'
    )
    slide = (
        f'<?xml version="1.0"?><p:sld xmlns:p="urn:p" xmlns:a="{A}"><a:p><a:r>'
        f"<a:t>карта {CARD}</a:t></a:r></a:p></p:sld>"
    )

    xlsx = _scan("table.csv", _zip({"xl/worksheets/sheet1.xml": sheet}))
    pptx = _scan("deck.txt", _zip({"ppt/slides/slide1.xml": slide}))

    assert xlsx.status == "ok" and xlsx.findings["iin_bin"]["count"] == 1
    assert pptx.status == "ok" and pptx.findings["card"]["count"] == 1


def test_pdf_renamed_to_txt_is_parsed_as_pdf() -> None:
    # Текст страницы записан шестнадцатеричной строкой: прочитанный как текст, файл ничего не даст.
    data = make_pdf(f"IIN {IIN}", hex_string=True)

    as_pdf = _scan("a.pdf", data)
    as_txt = _scan("a.txt", data)

    assert as_pdf.status == "ok" and as_pdf.findings["iin_bin"]["count"] == 1
    assert as_txt == as_pdf


def test_binary_content_under_a_text_extension_is_unsupported() -> None:
    blob = bytes(range(256)) * 4 + f" {IIN} ".encode()

    assert _scan("a.txt", blob).status == "unsupported"
    assert _scan("a.csv", b"\x00\x00\x00\x00").status == "unsupported"


def test_other_zip_archives_are_unsupported_whatever_the_name() -> None:
    archive = _zip({"notes.txt": f"ИИН {IIN}"})

    assert _scan("a.zip", archive).status == "unsupported"
    assert _scan("a.txt", archive).status == "unsupported"


def test_ole_file_is_encrypted_only_under_an_office_name() -> None:
    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 64

    assert _scan("secret.xlsx", ole).status == "encrypted"
    assert _scan("old.doc", ole).status == "unsupported"
    assert _scan("a.txt", ole).status == "unsupported"


def test_utf16_text_with_a_bom_is_text() -> None:
    for bom, codec in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        outcome = _scan("a.txt", bom + f"ИИН {IIN}".encode(codec))

        assert outcome.status == "ok" and outcome.findings["iin_bin"]["count"] == 1


def test_text_that_only_looks_like_a_container_is_still_scanned_as_text() -> None:
    # Подпись в начале не прячет текстовый файл: как PDF/zip он не разбирается — читаем текст.
    for prefix in (b"%PDF-1.4\n", b"PK\x03\x04\n"):
        outcome = _scan("a.txt", prefix + f"ИИН {IIN}".encode())

        assert outcome.status == "ok" and outcome.findings["iin_bin"]["count"] == 1


def test_a_container_name_with_text_content_is_an_error() -> None:
    assert _scan("a.docx", f"ИИН {IIN}".encode()).status == "error"
    assert _scan("a.pdf", f"ИИН {IIN}".encode()).status == "error"
    assert _scan("a.docx", b"").status == "error"


# --- Переносы и табуляции внутри абзаца -----------------------------------------

IIN_TWO = "850612412340"


@pytest.mark.parametrize("separator", ["<w:br/>", "<w:cr/>", "<w:tab/>"])
def test_docx_line_break_or_tab_inside_a_paragraph_separates_numbers(separator: str) -> None:
    document = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body><w:p><w:r>'
        f"<w:t>{IIN}</w:t>{separator}<w:t>{IIN_TWO}</w:t>"
        f"</w:r></w:p></w:body></w:document>"
    )

    outcome = _scan("list.docx", _zip({"word/document.xml": document}))

    assert outcome.status == "ok" and outcome.findings["iin_bin"]["count"] == 2


def test_pptx_line_break_inside_a_paragraph_separates_numbers() -> None:
    slide = (
        f'<?xml version="1.0"?><p:sld xmlns:p="urn:p" xmlns:a="{A}"><a:p>'
        f"<a:r><a:t>{IIN}</a:t></a:r><a:br/><a:r><a:t>{IIN_TWO}</a:t></a:r>"
        f"</a:p></p:sld>"
    )

    outcome = _scan("deck.pptx", _zip({"ppt/slides/slide1.xml": slide}))

    assert outcome.status == "ok" and outcome.findings["iin_bin"]["count"] == 2


def _many_runs_docx(runs: int) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>строка {index} </w:t></w:r><w:r><w:t>"
        + (f"ИИН {IIN} гриф секретно" if index % 997 == 0 else "текст")
        + "</w:t></w:r></w:p>"
        for index in range(runs)
    )
    document = (
        f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    return _zip({"word/document.xml": document})


def _rich_detectors() -> list:
    from barysguard.services.inspection.detectors import DictionaryDetector
    from barysguard.services.inspection.regex_detector import RegexDetector

    return [
        IinBinDetector(),
        CardDetector(),
        DictionaryDetector("markings", ["секретно"]),
        RegexDetector("line", r"строка \d+0 "),
    ]


def test_docx_with_many_small_runs_gives_the_same_findings_as_one_piece() -> None:
    from barysguard.services.inspection.detectors import ContentScanner
    from barysguard.services.inspection.extract import Deadline, extract

    data = _many_runs_docx(5000)

    outcome = scan_bytes("big.docx", data, _rich_detectors(), LIMITS)

    text = "".join(extract("big.docx", data, LIMITS, Deadline(30.0)))
    scanner = ContentScanner(_rich_detectors())
    scanner.feed(text)
    expected = {
        key: {"count": f.count, "samples": f.samples, "fragments": f.fragments}
        for key, f in scanner.finish().items()
        if f.count
    }
    assert outcome.status == "ok"
    assert outcome.findings == expected
    assert outcome.findings["iin_bin"]["count"] == 6


def test_scanner_is_fed_in_few_large_pieces(monkeypatch: pytest.MonkeyPatch) -> None:
    from barysguard.services.inspection import engine
    from barysguard.services.inspection.detectors import ContentScanner

    pieces = ["ab", "\n"] * 25_000
    feeds: list[int] = []
    original = ContentScanner.feed

    def spy(self: ContentScanner, chunk: str) -> None:
        feeds.append(len(chunk))
        original(self, chunk)

    class _Stream:
        truncated = False

        def __iter__(self) -> Iterator[str]:
            return iter(pieces)

    monkeypatch.setattr(ContentScanner, "feed", spy)
    monkeypatch.setattr(engine, "extract", lambda *args: _Stream())

    outcome = engine.scan_bytes("a.docx", b"", DETECTORS, LIMITS)

    assert outcome.status == "ok"
    assert sum(feeds) == len("".join(pieces))
    assert len(feeds) <= 5, len(feeds)


def test_whitespace_only_pieces_still_mean_no_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from barysguard.services.inspection import engine

    class _Stream:
        truncated = False

        def __iter__(self) -> Iterator[str]:
            return iter([" ", "\n", "\t"] * 20_000)

    monkeypatch.setattr(engine, "extract", lambda *args: _Stream())

    assert engine.scan_bytes("a.docx", b"", DETECTORS, LIMITS).status == "no_text"
