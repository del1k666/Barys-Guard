"""Извлечение текста и скан байтов: форматы, пределы, отказы."""

import io
import zipfile

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


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
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
