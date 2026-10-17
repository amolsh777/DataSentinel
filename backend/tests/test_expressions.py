import numpy as np
import pandas as pd
import pytest

from app.analytics.expressions import ExpressionError, compile_expression, evaluate


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "qty": [1, 2, 3, np.nan],
            "price": [10.0, 5.0, 2.0, 4.0],
            "revenue": [10.0, 10.0, 7.0, 4.0],
            "status": ["paid", "paid", "refund", "paid"],
            "unit price": [1, 2, 3, 4],
        }
    )


def run(expr, df):
    res = evaluate(compile_expression(expr), df)
    return res["passed"].tolist(), res["evaluable"].tolist()


def test_arithmetic_rule_and_null_rows_not_evaluable(df):
    passed, evaluable = run("abs(revenue - qty * price) <= 0.01", df)
    assert passed == [True, True, False, False]
    assert evaluable == [True, True, True, False]


def test_boolean_logic_in_and_strings(df):
    passed, _ = run("status in ['paid'] and price > 3", df)
    assert passed == [True, True, False, True]
    passed, _ = run("not (status == 'refund') or revenue > 100", df)
    assert passed == [True, True, False, True]


def test_chained_comparison(df):
    assert run("2 <= price <= 5", df)[0] == [False, True, True, True]


def test_backtick_columns(df):
    assert run("`unit price` >= 2", df)[0] == [False, True, True, True]


def test_isnull_makes_all_rows_evaluable(df):
    passed, evaluable = run("isnull(qty) or qty > 1", df)
    assert passed == [False, True, True, True] and all(evaluable)


def test_numeric_strings_are_coerced():
    # 9 of 10 values parse (>= 90%), so the column is numeric and 'unknown' is not evaluable.
    frame = pd.DataFrame({"q": [str(i) for i in range(1, 10)] + ["unknown"], "p": [1.0] * 10})
    passed, evaluable = run("q * p >= 1", frame)
    assert passed == [True] * 9 + [False] and evaluable == [True] * 9 + [False]


def test_arithmetic_on_text_column_raises_clean_error():
    frame = pd.DataFrame({"q": ["1", "2", "unknown"], "p": [1.0, 1.0, 1.0]})
    with pytest.raises(ExpressionError):
        evaluate(compile_expression("q * p >= 1"), frame)


def test_columns_reported(df):
    assert compile_expression("abs(revenue - qty * price) < 1").columns == ("revenue", "qty", "price")


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo hi')",
        "price.__class__",
        "open('x')",
        "[x for x in price]",
        "lambda: 1",
        "price ** 1000",
        "price[0]",
        "status in price",
        "exec('1')",
        "price if qty else revenue",
        "",
        "1 + 2",
        "price >",
        "x" * 600,
    ],
)
def test_disallowed_constructs_are_rejected(expr):
    with pytest.raises(ExpressionError):
        compile_expression(expr)


def test_unknown_column_raises_on_evaluate(df):
    with pytest.raises(ExpressionError):
        evaluate(compile_expression("missing_col > 1"), df)


def test_string_number_comparison_raises_clean_error(df):
    with pytest.raises(ExpressionError):
        evaluate(compile_expression("status > 5"), df)
