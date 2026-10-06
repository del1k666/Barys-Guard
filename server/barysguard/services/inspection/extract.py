"""Извлечение текста из файлов с защитными пределами.

Формат выбирается по расширению имени файла (из пути события), а не по типу,
заявленному агентом. Контейнеры Office разбираются стандартной библиотекой;
файл читается из байтов в памяти и на диск не пишется.
"""

import io
import re
import time
import xml.etree.ElementTree as ET  # noqa: S405 - XML Office без DTD, DOCTYPE отсекается вручную
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PureWindowsPath

CHUNK = 64 * 1024
_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
# Степень сжатия проверяется только у крупных элементов: у мелких она ни о чём не говорит.
_RATIO_FLOOR = 1 << 20
_MAX_PDF_PAGES = 2000

TEXT_EXTENSIONS = frozenset(
    {
        ".txt", ".csv", ".tsv", ".json", ".log", ".md", ".xml", ".html", ".htm", ".ini",
        ".cfg", ".conf", ".yaml", ".yml", ".py", ".js", ".ts", ".java", ".go", ".c", ".h",
        ".cpp", ".cs", ".sql", ".sh", ".ps1", ".bat",
    }
)  # fmt: skip
OOXML_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})


class ExtractFailure(Exception):  # noqa: N818 - имя отражает смысл, статус скана в .status
    """Файл нельзя разобрать; `status` — итоговый статус скана."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class Limits:
    max_text: int
    max_unpacked: int
    max_entries: int
    max_ratio: int
    timeout: float


class Deadline:
    """Кооперативный таймаут: проверяется между порциями и страницами."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def check(self) -> None:
        if time.monotonic() > self._end:
            raise ExtractFailure("error")


class TextStream:
    """Текст порциями; `truncated` становится истинным, если сработал предел."""

    def __init__(self, source: Iterator[str], max_chars: int, deadline: Deadline) -> None:
        self._source = source
        self._remaining = max_chars
        self._deadline = deadline
        self.truncated = False

    def __iter__(self) -> Iterator[str]:
        for piece in self._source:
            self._deadline.check()
            for start in range(0, len(piece), CHUNK):
                part = piece[start : start + CHUNK]
                if len(part) > self._remaining:
                    if self._remaining > 0:
                        yield part[: self._remaining]
                    self.truncated = True
                    return
                self._remaining -= len(part)
                yield part


def extract(name: str, data: bytes, limits: Limits, deadline: Deadline) -> TextStream:
    extension = PureWindowsPath(name).suffix.lower()
    if extension in OOXML_EXTENSIONS:
        source = _ooxml(extension, data, limits)
    elif extension == ".pdf":
        source = _pdf(data, deadline)
    elif extension in TEXT_EXTENSIONS:
        source = iter([_decode(data)])
    else:
        raise ExtractFailure("unsupported")
    return TextStream(source, limits.max_text, deadline)


def _decode(data: bytes) -> str:
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


# --- Office -----------------------------------------------------------------


class _Budget:
    def __init__(self, left: int) -> None:
        self.left = left


def _open_archive(data: bytes, limits: Limits) -> zipfile.ZipFile:
    if data[:8] == _OLE_MAGIC:
        # Документ Office с паролем хранится как составной файл OLE, а не как zip.
        raise ExtractFailure("encrypted")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExtractFailure("error") from None
    infos = archive.infolist()
    if len(infos) > limits.max_entries:
        raise ExtractFailure("too_large")
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ExtractFailure("encrypted")
        total += info.file_size
        if (
            info.file_size > _RATIO_FLOOR
            and info.compress_size
            and info.file_size / info.compress_size > limits.max_ratio
        ):
            raise ExtractFailure("too_large")
    if total > limits.max_unpacked:
        raise ExtractFailure("too_large")
    return archive


def _read_member(archive: zipfile.ZipFile, name: str, budget: _Budget) -> bytes:
    # Заголовок архива может врать о размере: читаем не больше оставшегося бюджета.
    try:
        with archive.open(name) as member:
            raw = member.read(budget.left + 1)
    except (
        zipfile.BadZipFile,
        KeyError,
        NotImplementedError,
        RuntimeError,
        zlib.error,
        EOFError,
        OSError,
    ):
        raise ExtractFailure("error") from None
    if len(raw) > budget.left:
        raise ExtractFailure("too_large")
    budget.left -= len(raw)
    return raw


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_events(raw: bytes) -> Iterator[ET.Element]:
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ExtractFailure("error")
    try:
        # noqa ниже: DOCTYPE и ENTITY отсечены выше
        for _, element in ET.iterparse(io.BytesIO(raw), events=("end",)):  # noqa: S314
            yield element
    except ET.ParseError:
        raise ExtractFailure("error") from None


def _text_runs(raw: bytes, text_tag: str, break_tag: str) -> Iterator[str]:
    for element in _xml_events(raw):
        name = _local(element.tag)
        if name == text_tag and element.text:
            yield element.text
        elif name == break_tag:
            yield "\n"
            element.clear()


def _xlsx_sheet(raw: bytes) -> Iterator[str]:
    for element in _xml_events(raw):
        name = _local(element.tag)
        if name == "c":
            kind = element.get("t")
            if kind == "inlineStr":
                yield "".join(node.text or "" for node in element.iter() if _local(node.tag) == "t")
            elif kind != "s":
                value = element.find("{*}v")
                if value is not None and value.text:
                    yield value.text
            yield "\n"
            element.clear()


def _natural(names: list[str]) -> list[str]:
    return sorted(
        names, key=lambda n: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", n)]
    )


def _ooxml(extension: str, data: bytes, limits: Limits) -> Iterator[str]:
    archive = _open_archive(data, limits)
    budget = _Budget(limits.max_unpacked)
    names = archive.namelist()
    if extension == ".docx":
        pattern = r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml"
        for part in _natural([n for n in names if re.fullmatch(pattern, n)]):
            yield from _text_runs(_read_member(archive, part, budget), "t", "p")
    elif extension == ".pptx":
        for part in _natural([n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]):
            yield from _text_runs(_read_member(archive, part, budget), "t", "p")
    else:
        if "xl/sharedStrings.xml" in names:
            yield from _text_runs(_read_member(archive, "xl/sharedStrings.xml", budget), "t", "si")
        for part in _natural([n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]):
            yield from _xlsx_sheet(_read_member(archive, part, budget))


# --- PDF --------------------------------------------------------------------


def _pdf(data: bytes, deadline: Deadline) -> Iterator[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                unlocked = bool(reader.decrypt(""))
            except Exception:  # noqa: BLE001 - любой сбой расшифровки означает «закрыт паролем»
                unlocked = False
            if not unlocked:
                raise ExtractFailure("encrypted")
        for index, page in enumerate(reader.pages):
            if index >= _MAX_PDF_PAGES:
                return
            deadline.check()
            text = page.extract_text() or ""
            if text:
                yield text + "\n"
    except ExtractFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf бросает много разных исключений на битых файлах
        raise ExtractFailure("error") from exc
