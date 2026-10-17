from __future__ import annotations

import pandas as pd
import pytest

from app.core.errors import IngestionError
from app.services.ingestion import parse_file, safe_display_filename
from tests.conftest import csv_bytes, xlsx_bytes


def _code(exc_info: pytest.ExceptionInfo) -> str:
    return exc_info.value.code


def test_valid_csv(small_df):
    parsed = parse_file(csv_bytes(small_df), "orders.csv")
    assert parsed.df.shape == (40, 3)
    assert list(parsed.df.columns) == ["order_id", "amount", "city"]
    assert parsed.file_format == "csv" and parsed.delimiter == ","


def test_semicolon_delimiter():
    parsed = parse_file(b"a;b\n1;2\n3;4\n", "x.csv")
    assert list(parsed.df.columns) == ["a", "b"] and parsed.delimiter == ";"


def test_header_whitespace_trimmed():
    parsed = parse_file(b" a , b \n1,2\n", "x.csv")
    assert list(parsed.df.columns) == ["a", "b"]


@pytest.mark.parametrize("content", [b"", b"   \n\n"])
def test_empty_file(content):
    with pytest.raises(IngestionError) as e:
        parse_file(content, "x.csv")
    assert _code(e) == "EMPTY_FILE"


def test_header_only_is_empty_dataset():
    with pytest.raises(IngestionError) as e:
        parse_file(b"a,b,c\n", "x.csv")
    assert _code(e) == "EMPTY_DATASET"


def test_duplicate_column_names():
    with pytest.raises(IngestionError) as e:
        parse_file(b"a,b,a\n1,2,3\n", "x.csv")
    assert _code(e) == "DUPLICATE_COLUMNS"
    assert e.value.details["duplicate_columns"] == ["a"]


def test_missing_header_with_data():
    with pytest.raises(IngestionError) as e:
        parse_file(b"a,,c\n1,2,3\n", "x.csv")
    assert _code(e) == "MISSING_HEADER"
    assert e.value.details["column_positions"] == [2]


def test_trailing_empty_column_dropped_with_warning():
    parsed = parse_file(b"a,b,\n1,2,\n3,4,\n", "x.csv")
    assert list(parsed.df.columns) == ["a", "b"]
    assert any("Dropped empty unnamed column" in w for w in parsed.warnings)


def test_numeric_header_warning():
    parsed = parse_file(b"1,2\n3,4\n", "x.csv")
    assert any("missing a header" in w for w in parsed.warnings)


def test_case_insensitive_duplicate_warning():
    parsed = parse_file(b"Name,name\nx,y\n", "x.csv")
    assert any("differ only by case" in w for w in parsed.warnings)


@pytest.mark.parametrize("name", ["data.json", "data.xls", "script.py", "noext"])
def test_unsupported_extension(name):
    with pytest.raises(IngestionError) as e:
        parse_file(b"a,b\n1,2\n", name)
    assert _code(e) == "UNSUPPORTED_FORMAT"


def test_binary_content_rejected():
    with pytest.raises(IngestionError) as e:
        parse_file(b"\x00\x01\x02binary", "x.csv")
    assert _code(e) == "INVALID_CONTENT"


def test_xlsx_renamed_as_csv_rejected(small_df):
    with pytest.raises(IngestionError) as e:
        parse_file(xlsx_bytes({"S": small_df}), "x.csv")
    assert _code(e) == "INVALID_CONTENT"


def test_malformed_rows():
    with pytest.raises(IngestionError) as e:
        parse_file(b"a,b\n1,2\n3,4,5\n", "x.csv")
    assert _code(e) == "MALFORMED_FILE"


def test_row_limit():
    content = b"a\n" + b"1\n" * 11
    with pytest.raises(IngestionError) as e:
        parse_file(content, "x.csv", max_rows=10)
    assert _code(e) == "ROW_LIMIT_EXCEEDED"


def test_column_limit():
    with pytest.raises(IngestionError) as e:
        parse_file(b"a,b,c\n1,2,3\n", "x.csv", max_columns=2)
    assert _code(e) == "COLUMN_LIMIT_EXCEEDED"


def test_latin1_fallback():
    content = "name,city\nJos\xe9,M\xfcnchen\n".encode("latin-1")
    parsed = parse_file(content, "x.csv")
    assert parsed.df.loc[0, "city"] == "München"
    assert parsed.encoding in ("cp1252", "latin-1") and parsed.warnings


def test_utf8_bom_handled():
    parsed = parse_file("\ufeffa,b\n1,2\n".encode("utf-8"), "x.csv")
    assert list(parsed.df.columns) == ["a", "b"] and parsed.encoding == "utf-8-sig"


def test_excel_default_first_sheet_with_warning(small_df):
    content = xlsx_bytes({"First": small_df, "Second": small_df.head(5)})
    parsed = parse_file(content, "book.xlsx")
    assert parsed.sheet_name == "First" and parsed.sheet_names == ["First", "Second"]
    assert len(parsed.df) == 40 and any("2 sheets" in w for w in parsed.warnings)


def test_excel_sheet_selection(small_df):
    content = xlsx_bytes({"First": small_df, "Second": small_df.head(5)})
    parsed = parse_file(content, "book.xlsx", sheet_name="Second")
    assert len(parsed.df) == 5 and parsed.df["amount"].dtype.kind == "f"


def test_excel_missing_sheet(small_df):
    with pytest.raises(IngestionError) as e:
        parse_file(xlsx_bytes({"Only": small_df}), "book.xlsx", sheet_name="Nope")
    assert _code(e) == "SHEET_NOT_FOUND"
    assert e.value.details["available_sheets"] == ["Only"]


def test_corrupted_excel():
    with pytest.raises(IngestionError) as e:
        parse_file(b"PK\x03\x04garbage-not-a-real-zip", "book.xlsx")
    assert _code(e) == "CORRUPTED_FILE"


def test_non_zip_excel():
    with pytest.raises(IngestionError) as e:
        parse_file(b"a,b\n1,2\n", "book.xlsx")
    assert _code(e) == "CORRUPTED_FILE"


def test_empty_excel_sheet():
    content = xlsx_bytes({"Empty": pd.DataFrame()})
    with pytest.raises(IngestionError) as e:
        parse_file(content, "book.xlsx")
    assert _code(e) == "EMPTY_DATASET"


def test_safe_display_filename_strips_paths():
    assert safe_display_filename("../../etc/passwd.csv") == "passwd.csv"
    assert safe_display_filename("C:\\Users\\me\\data.csv") == "data.csv"
