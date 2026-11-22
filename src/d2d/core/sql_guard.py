"""AST-based read-only SQL validation with a default-deny function policy."""
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class SQLValidationError(ValueError):
    """Raised when a statement is not permitted by the read-only query policy."""


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    sql: str
    dialect: str


_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Create", "Drop", "Alter", "Merge", "Command",
    "Transaction", "Commit", "Rollback", "Grant", "Revoke", "Use", "Set", "Copy",
    "Into", "Lock", "Pragma", "Attach", "Detach", "Execute", "Call", "ReadCSV",
    "ReadParquet", "ReadJSON", "ReadJSONL", "ExternalLocation",
)
_FORBIDDEN_FUNCTIONS = {
    "LOAD_EXTENSION", "INSTALL", "READ_CSV", "READ_CSV_AUTO", "READ_PARQUET",
    "READ_JSON", "READ_JSON_AUTO", "HTTP_GET", "HTTP_POST", "WRITEFILE",
    "PG_READ_FILE", "PG_WRITE_FILE", "PG_SLEEP", "SLEEP",
}
_ALLOWED_FUNCTIONS = {
    "ABS", "AVG", "BOOL_AND", "BOOL_OR", "CEIL", "CEILING", "COALESCE",
    "CONCAT", "CONCAT_WS", "COUNT", "COUNT_IF", "DATE_PART", "DATE_TRUNC",
    "DENSE_RANK", "FLOOR", "FIRST_VALUE", "GREATEST", "LEAD", "LEAST",
    "LENGTH", "LOWER", "LAG", "LAST_VALUE", "MAX", "MIN", "NULLIF",
    "RANGE", "RANK", "REPEAT", "REPLACE", "ROUND", "ROW_NUMBER", "SPLIT_PART", "STDDEV",
    "STDDEV_POP", "STDDEV_SAMP", "STRFTIME", "SUBSTRING", "SUM", "TRIM",
    "UPPER", "VARIANCE", "VAR_POP", "VAR_SAMP",
}


def validate_read_only_sql(sql: str, dialect: str = "") -> ValidatedQuery:
    """Accept exactly one SELECT/CTE query and explicitly allowed functions."""
    if not sql or not sql.strip():
        raise SQLValidationError("SQL must not be empty")
    try:
        statements = sqlglot.parse(sql, read=dialect or None)  # pyright: ignore[reportUnknownMemberType]
    except (ParseError, ValueError) as exc:
        raise SQLValidationError(f"SQL could not be parsed: {exc}") from exc
    if len(statements) != 1 or statements[0] is None:
        raise SQLValidationError("exactly one SQL statement is allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLValidationError("only SELECT queries, including CTEs and set operations, are allowed")
    forbidden_types = tuple(
        node_type for name in _FORBIDDEN_NODE_NAMES
        if (node_type := getattr(exp, name, None)) is not None
    )
    for node in tree.walk():
        if forbidden_types and isinstance(node, forbidden_types):
            raise SQLValidationError(f"forbidden SQL operation: {type(node).__name__}")
        if isinstance(node, exp.Func):
            function_name = (
                node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            ).upper()
            if function_name in _FORBIDDEN_FUNCTIONS:
                raise SQLValidationError(f"forbidden SQL function: {function_name}")
            if function_name not in _ALLOWED_FUNCTIONS:
                raise SQLValidationError(f"SQL function is not allowlisted: {function_name}")
    return ValidatedQuery(
        sql=tree.sql(dialect=dialect or None),  # pyright: ignore[reportUnknownMemberType]
        dialect=dialect or "generic",
    )
