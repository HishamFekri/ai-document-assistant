"""Bounded preflight, reused before persistence and before worker extraction."""

import codecs
from contextlib import contextmanager
from pathlib import PurePosixPath
import re
import stat
import struct
from xml.parsers import expat
import zipfile

from pypdf import PdfReader
from pypdf.errors import LimitReachedError
from pypdf.generic import ArrayObject

from app.services.content_budget import ContentBudget
from app.services.document_resource_errors import DocumentResourceError
from app.services.resource_limits import SUPPORTED_UPLOAD_TYPES, upload_limits


@contextmanager
def source_stream(source):
    if hasattr(source, "read"):
        source.seek(0)
        try:
            yield source
        finally:
            source.seek(0)
    else:
        with open(source, "rb") as stream:
            yield stream


def validate_source_size(stream):
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        raise DocumentResourceError("empty_file", 400)
    if size > upload_limits().file_bytes:
        raise DocumentResourceError("file_size")


def check_pdf_pages(reader):
    limit = upload_limits().pdf_pages
    # Reject a declared oversized page tree before flattening it; verify actual
    # pages too, since a small declared count is not a trustworthy admission.
    declared = reader.trailer["/Root"]["/Pages"].get("/Count", 0)
    if int(declared) > limit or len(reader.pages) > limit:
        raise DocumentResourceError("pdf_pages")
    if not reader.pages:
        raise DocumentResourceError("invalid_pdf", 400)
    return len(reader.pages)


def validate_pdf_source(stream):
    try:
        if stream.read(5) != b"%PDF-":
            raise DocumentResourceError("invalid_pdf", 400)
        stream.seek(0)
        reader = PdfReader(stream)
        try:
            return check_pdf_pages(reader)
        finally:
            reader.close()
    except DocumentResourceError:
        raise
    except Exception:
        raise DocumentResourceError("invalid_pdf", 400) from None


class PDFTextBudget:
    def __init__(self):
        self.limits = upload_limits()
        self.stream_bytes = 0
        self.text = ContentBudget()

    def extract(self, page):
        page_bytes = 0
        try:
            contents = page.get("/Contents")
            if contents is not None:
                contents = contents.get_object()
                for item in contents if isinstance(contents, ArrayObject) else [contents]:
                    decoded = item.get_object().get_data()
                    page_bytes += len(decoded)
                    self.stream_bytes += len(decoded)
                    if (page_bytes > self.limits.pdf_page_content_bytes
                            or self.stream_bytes > self.limits.pdf_content_bytes):
                        raise DocumentResourceError("content_limit")
            text = page.extract_text() or ""
        except LimitReachedError:
            raise DocumentResourceError("content_limit") from None
        self.text.text(text)
        return text.strip()


def validate_text_source(stream):
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    budget = ContentBudget()
    try:
        while data := stream.read(64 * 1024):
            if b"\x00" in data:
                raise DocumentResourceError("invalid_text", 400)
            budget.text(decoder.decode(data, final=False))
        budget.text(decoder.decode(b"", final=True))
    except UnicodeDecodeError:
        raise DocumentResourceError("invalid_text", 400) from None


class OfficeStructure:
    def __init__(self, extension):
        self.limits = upload_limits()
        self.extension = extension
        self.nodes = self.characters = self.paragraphs = self.tables = 0
        self.sheets = self.rows = self.cells = self.grid_cells = 0

    def inspect_xml(self, stream, name):
        limits = self.limits
        worksheet = name.startswith("xl/worksheets/") and name.endswith(".xml")
        if worksheet:
            self.sheets += 1
            if self.sheets > limits.worksheets:
                raise DocumentResourceError("spreadsheet_limit")
        depth = row_count = max_row = max_column = row_column = 0
        parser = expat.ParserCreate(namespace_separator="}")

        def reject_dtd(*args):
            raise DocumentResourceError("invalid_office", 400)

        def column_index(value):
            if not re.fullmatch(r"[A-Za-z]{1,3}[1-9][0-9]{0,7}", value):
                raise DocumentResourceError("spreadsheet_limit")
            letters = re.match(r"[A-Za-z]+", value)[0]
            column = 0
            for letter in letters.upper():
                column = column * 26 + ord(letter) - ord("A") + 1
            row = int(value[len(letters):])
            if column > limits.columns or row > limits.sheet_rows:
                raise DocumentResourceError("spreadsheet_limit")
            return column, row

        def start(tag, attrs):
            nonlocal depth, row_count, max_row, max_column, row_column
            depth += 1
            self.nodes += 1
            if depth > limits.xml_depth or self.nodes > limits.xml_nodes:
                raise DocumentResourceError("office_expansion")
            local = tag.rsplit("}", 1)[-1]
            if self.extension == ".docx" and name.startswith("word/"):
                self.paragraphs += local == "p"
                self.tables += local == "tbl"
                if self.paragraphs > limits.docx_paragraphs or self.tables > limits.docx_tables:
                    raise DocumentResourceError("docx_limit")
            if not worksheet:
                if name == "xl/workbook.xml" and local == "sheet":
                    # Also count declared sheets, including chart sheets.
                    row_count += 1
                    if row_count > limits.worksheets:
                        raise DocumentResourceError("spreadsheet_limit")
                return
            if local == "dimension":
                for point in attrs.get("ref", "A1").split(":"):
                    column_index(point)
            elif local == "row":
                row_count += 1
                row_column = 0
                row = int(attrs.get("r", row_count))
                max_row = max(max_row, row)
                if row <= 0 or row_count > limits.sheet_rows or max_row > limits.sheet_rows:
                    raise DocumentResourceError("spreadsheet_limit")
            elif local == "c":
                self.cells += 1
                if self.cells > limits.cells:
                    raise DocumentResourceError("spreadsheet_limit")
                # Missing coordinates are legal; conservatively bound the row.
                column, row = column_index(attrs["r"]) if "r" in attrs else (row_column + 1, max(1, max_row))
                row_column = column
                max_column, max_row = max(max_column, column), max(max_row, row)
                if max_column > limits.columns or max_row > limits.sheet_rows:
                    raise DocumentResourceError("spreadsheet_limit")
                if self.grid_cells + max_row * max_column > limits.cells:
                    raise DocumentResourceError("spreadsheet_limit")

        def end(tag):
            nonlocal depth
            depth -= 1

        def text(value):
            self.characters += len(value)
            if self.characters > limits.text_chars:
                raise DocumentResourceError("content_limit")

        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = text
        parser.StartDoctypeDeclHandler = reject_dtd
        parser.ExternalEntityRefHandler = reject_dtd
        while data := stream.read(64 * 1024):
            parser.Parse(data, False)
        parser.Parse(b"", True)
        if worksheet:
            self.rows += max(row_count, max_row)
            self.grid_cells += max_row * max_column
            if self.rows > limits.total_rows or self.grid_cells > limits.cells:
                raise DocumentResourceError("spreadsheet_limit")


def validate_office_source(stream, extension):
    limits = upload_limits()
    try:
        # EOCD count prevents building a huge ZipInfo list for an obvious bomb.
        stream.seek(0, 2)
        length = stream.tell()
        stream.seek(max(0, length - 65557))
        tail = stream.read(65557)
        offset = tail.rfind(b"PK\x05\x06")
        if offset < 0 or len(tail) - offset < 22:
            raise DocumentResourceError("invalid_office", 400)
        if struct.unpack_from("<H", tail, offset + 10)[0] > limits.zip_entries:
            raise DocumentResourceError("office_expansion")
        directory_size = struct.unpack_from("<L", tail, offset + 12)[0]
        end_record = max(0, length - 65557) + offset
        directory_start = end_record - directory_size
        if directory_start < 0:
            raise DocumentResourceError("invalid_office", 400)
        stream.seek(directory_start)
        entries_seen = 0
        # Count actual central-directory records before ZipFile builds ZipInfo
        # objects. EOCD counts may be forged. Each record has a fixed 46-byte
        # header followed by its name, extra fields and comment.
        while stream.tell() < end_record:
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise DocumentResourceError("invalid_office", 400)
            entries_seen += 1
            if entries_seen > limits.zip_entries:
                raise DocumentResourceError("office_expansion")
            namesize, extrasize, commentsize = struct.unpack_from("<3H", header, 28)
            stream.seek(namesize + extrasize + commentsize, 1)
        if stream.tell() != end_record:
            raise DocumentResourceError("invalid_office", 400)
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > limits.zip_entries:
                raise DocumentResourceError("office_expansion")
            total = 0
            names = set()
            for entry in entries:
                name = entry.orig_filename
                parts = PurePosixPath(name).parts
                if (name in names or "\\" in name or ":" in name or "\x00" in name
                        or name.startswith("/") or ".." in parts
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                    raise DocumentResourceError("invalid_office", 400)
                names.add(name)
                total += entry.file_size
                if (total > limits.zip_expanded_bytes or entry.file_size > limits.zip_entry_bytes
                        or entry.file_size > max(1, entry.compress_size) * limits.zip_ratio):
                    raise DocumentResourceError("office_expansion")
            required = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
            if "[Content_Types].xml" not in names or required not in names:
                raise DocumentResourceError("invalid_office", 400)
            structure = OfficeStructure(extension)
            # Stream XML for structural checks; never extract archive paths.
            for entry in entries:
                if entry.filename.lower().endswith((".xml", ".rels")):
                    with archive.open(entry) as xml:
                        structure.inspect_xml(xml, entry.filename)
    except DocumentResourceError:
        raise
    except Exception:
        raise DocumentResourceError("invalid_office", 400) from None


def validate_document_source(source, extension):
    if extension not in SUPPORTED_UPLOAD_TYPES:
        raise DocumentResourceError("unsupported_file", 400)
    with source_stream(source) as stream:
        validate_source_size(stream)
        if extension == ".pdf":
            return validate_pdf_source(stream)
        if extension in {".docx", ".xlsx"}:
            return validate_office_source(stream, extension)
        validate_text_source(stream)
