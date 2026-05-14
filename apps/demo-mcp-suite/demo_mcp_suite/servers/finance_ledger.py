from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from demo_mcp_suite.runtime import DemoMcpServer, object_schema, text_result, tool

_DEMO_UPDATED_AT = "2026-05-14T00:00:00Z"
_DEFAULT_MONTH = "2026-05"
_DEFAULT_CURRENCY = "USD"

_INITIAL_TRANSACTIONS: list[dict[str, Any]] = [
    {
        "transaction_id": "txn_20260501_payroll",
        "date": "2026-05-01",
        "description": "Demo Payroll",
        "amount_cents": 420000,
        "currency": "USD",
        "category": "Income",
        "account": "Demo Checking",
        "merchant": "CoreMCP Demo Employer",
        "created_at": "2026-05-01T09:00:00Z",
        "updated_at": "2026-05-01T09:00:00Z",
    },
    {
        "transaction_id": "txn_20260502_grocery",
        "date": "2026-05-02",
        "description": "Neighborhood Grocery",
        "amount_cents": -8465,
        "currency": "USD",
        "category": "Groceries",
        "account": "Demo Checking",
        "merchant": "Neighborhood Grocery",
        "created_at": "2026-05-02T18:20:00Z",
        "updated_at": "2026-05-02T18:20:00Z",
    },
    {
        "transaction_id": "txn_20260504_coffee",
        "date": "2026-05-04",
        "description": "Coffee with product notes",
        "amount_cents": -650,
        "currency": "USD",
        "category": "Food",
        "account": "Demo Credit Card",
        "merchant": "Blue Bottle Demo",
        "created_at": "2026-05-04T08:45:00Z",
        "updated_at": "2026-05-04T08:45:00Z",
    },
    {
        "transaction_id": "txn_20260505_hosting",
        "date": "2026-05-05",
        "description": "Local server hosting allocation",
        "amount_cents": -2999,
        "currency": "USD",
        "category": "Software",
        "account": "Demo Credit Card",
        "merchant": "CoreMCP Lab Hosting",
        "created_at": "2026-05-05T10:00:00Z",
        "updated_at": "2026-05-05T10:00:00Z",
    },
    {
        "transaction_id": "txn_20260428_book",
        "date": "2026-04-28",
        "description": "Design systems reference book",
        "amount_cents": -4200,
        "currency": "USD",
        "category": "Education",
        "account": "Demo Credit Card",
        "merchant": "Local Bookstore",
        "created_at": "2026-04-28T16:30:00Z",
        "updated_at": "2026-04-28T16:30:00Z",
    },
]

_transactions: list[dict[str, Any]] = []
_next_transaction_seq = 1


def _reset_state() -> None:
    global _transactions, _next_transaction_seq
    _transactions = deepcopy(_INITIAL_TRANSACTIONS)
    _next_transaction_seq = 1


def _error_result(message: str, *, code: str = "invalid_arguments", details: dict[str, Any] | None = None) -> dict[str, Any]:
    structured: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        structured["error"]["details"] = details
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": structured,
        "isError": True,
    }


def _string_arg(args: dict[str, Any], name: str, default: str = "") -> str:
    value = args.get(name, default)
    if value is None:
        return default
    return str(value).strip()


def _limit_arg(args: dict[str, Any], default: int = 25, maximum: int = 100) -> int:
    value = args.get("limit", default)
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, maximum))


def _int_arg(args: dict[str, Any], name: str) -> tuple[int | None, str | None]:
    value = args.get(name)
    if isinstance(value, bool) or value is None:
        return None, f"{name} is required"
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, f"{name} must be an integer"


def _optional_int_arg(args: dict[str, Any], name: str) -> int | None:
    value = args.get(name)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _month_arg(args: dict[str, Any]) -> str:
    month = _string_arg(args, "month", _DEFAULT_MONTH)
    if len(month) == 7 and month[4] == "-":
        try:
            date.fromisoformat(f"{month}-01")
            return month
        except ValueError:
            pass
    return _DEFAULT_MONTH


def _money(cents: int, currency: str = _DEFAULT_CURRENCY) -> str:
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    return f"{sign}{currency} {absolute // 100:,}.{absolute % 100:02d}"


def _find_transaction(transaction_id: str) -> dict[str, Any] | None:
    return next((txn for txn in _transactions if txn["transaction_id"] == transaction_id), None)


def _transaction_summary(transaction: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(transaction)
    item["type"] = "income" if transaction["amount_cents"] > 0 else "expense"
    item["amount_display"] = _money(transaction["amount_cents"], transaction["currency"])
    return item


def _next_transaction_id() -> str:
    global _next_transaction_seq
    while True:
        transaction_id = f"txn_demo_{_next_transaction_seq:03d}"
        _next_transaction_seq += 1
        if _find_transaction(transaction_id) is None:
            return transaction_id


def _ledger_summary(args: dict[str, Any]) -> dict[str, Any]:
    month = _month_arg(args)
    currency = _string_arg(args, "currency", _DEFAULT_CURRENCY).upper()
    account = _string_arg(args, "account")

    month_transactions = [
        txn
        for txn in _transactions
        if txn["date"].startswith(month)
        and txn["currency"] == currency
        and (not account or txn["account"].lower() == account.lower())
    ]

    income_cents = sum(txn["amount_cents"] for txn in month_transactions if txn["amount_cents"] > 0)
    expense_cents = -sum(txn["amount_cents"] for txn in month_transactions if txn["amount_cents"] < 0)
    net_cents = income_cents - expense_cents

    category_totals: dict[str, dict[str, Any]] = {}
    for txn in month_transactions:
        bucket = category_totals.setdefault(
            txn["category"],
            {"category": txn["category"], "income_cents": 0, "expense_cents": 0, "net_cents": 0, "count": 0},
        )
        amount_cents = txn["amount_cents"]
        if amount_cents > 0:
            bucket["income_cents"] += amount_cents
        else:
            bucket["expense_cents"] += -amount_cents
        bucket["net_cents"] += amount_cents
        bucket["count"] += 1

    by_category = sorted(category_totals.values(), key=lambda item: item["category"].lower())
    for bucket in by_category:
        bucket["income_display"] = _money(bucket["income_cents"], currency)
        bucket["expense_display"] = _money(bucket["expense_cents"], currency)
        bucket["net_display"] = _money(bucket["net_cents"], currency)

    structured = {
        "month": month,
        "currency": currency,
        "account": account or None,
        "transaction_count": len(month_transactions),
        "income_cents": income_cents,
        "expense_cents": expense_cents,
        "net_cents": net_cents,
        "income_display": _money(income_cents, currency),
        "expense_display": _money(expense_cents, currency),
        "net_display": _money(net_cents, currency),
        "by_category": by_category,
    }
    return text_result(
        f"{month} ledger summary: income {_money(income_cents, currency)}, expenses {_money(expense_cents, currency)}, net {_money(net_cents, currency)}.",
        structured,
    )


def _transaction_search(args: dict[str, Any]) -> dict[str, Any]:
    query = _string_arg(args, "query").lower()
    month = _string_arg(args, "month")
    category = _string_arg(args, "category").lower()
    account = _string_arg(args, "account").lower()
    transaction_type = _string_arg(args, "type").lower()
    min_amount_cents = _optional_int_arg(args, "min_amount_cents")
    max_amount_cents = _optional_int_arg(args, "max_amount_cents")
    limit = _limit_arg(args)

    if transaction_type and transaction_type not in {"income", "expense"}:
        return _error_result("type must be one of: income, expense")
    if month and not (len(month) == 7 and month[4] == "-"):
        return _error_result("month must use YYYY-MM format")

    matches = []
    for txn in _transactions:
        amount_cents = txn["amount_cents"]
        absolute_cents = abs(amount_cents)
        if month and not txn["date"].startswith(month):
            continue
        if category and txn["category"].lower() != category:
            continue
        if account and txn["account"].lower() != account:
            continue
        if transaction_type == "income" and amount_cents <= 0:
            continue
        if transaction_type == "expense" and amount_cents >= 0:
            continue
        if min_amount_cents is not None and absolute_cents < min_amount_cents:
            continue
        if max_amount_cents is not None and absolute_cents > max_amount_cents:
            continue
        haystack = " ".join(
            [
                txn["transaction_id"],
                txn["date"],
                txn["description"],
                txn["category"],
                txn["account"],
                txn["merchant"],
            ]
        ).lower()
        if query and query not in haystack:
            continue
        matches.append(_transaction_summary(txn))

    matches = sorted(matches, key=lambda item: (item["date"], item["transaction_id"]), reverse=True)[:limit]
    return text_result(
        f"Found {len(matches)} transaction(s).",
        {
            "query": query,
            "month": month or None,
            "category": category or None,
            "account": account or None,
            "type": transaction_type or None,
            "count": len(matches),
            "items": matches,
        },
    )


def _transaction_create(args: dict[str, Any]) -> dict[str, Any]:
    transaction_date = _string_arg(args, "date")
    description = _string_arg(args, "description")
    amount_cents, amount_error = _int_arg(args, "amount_cents")
    currency = _string_arg(args, "currency", _DEFAULT_CURRENCY).upper()
    category = _string_arg(args, "category", "Uncategorized")
    account = _string_arg(args, "account", "Demo Cash")
    merchant = _string_arg(args, "merchant", description or "Manual Demo Entry")

    if not transaction_date:
        return _error_result("date is required")
    if not _validate_date(transaction_date):
        return _error_result("date must use YYYY-MM-DD format")
    if not description:
        return _error_result("description is required")
    if amount_error or amount_cents is None:
        return _error_result(amount_error or "amount_cents is required")
    if amount_cents == 0:
        return _error_result("amount_cents cannot be zero")
    if len(currency) != 3 or not currency.isalpha():
        return _error_result("currency must be a 3-letter ISO-style code")
    if not category:
        category = "Uncategorized"
    if not account:
        account = "Demo Cash"

    transaction = {
        "transaction_id": _next_transaction_id(),
        "date": transaction_date,
        "description": description,
        "amount_cents": amount_cents,
        "currency": currency,
        "category": category,
        "account": account,
        "merchant": merchant,
        "created_at": _DEMO_UPDATED_AT,
        "updated_at": _DEMO_UPDATED_AT,
    }
    _transactions.append(transaction)
    return text_result(
        f"Created transaction {transaction['transaction_id']} for {_money(amount_cents, currency)}.",
        {
            "transaction": _transaction_summary(transaction),
            "transaction_count": len(_transactions),
        },
    )


def _transaction_categorize(args: dict[str, Any]) -> dict[str, Any]:
    transaction_id = _string_arg(args, "transaction_id")
    category = _string_arg(args, "category")
    if not transaction_id:
        return _error_result("transaction_id is required")
    if not category:
        return _error_result("category is required")

    transaction = _find_transaction(transaction_id)
    if transaction is None:
        return _error_result(
            f"Transaction not found: {transaction_id}",
            code="not_found",
            details={"transaction_id": transaction_id},
        )

    old_category = transaction["category"]
    transaction["category"] = category
    transaction["category_source"] = "manual_demo"
    transaction["updated_at"] = _DEMO_UPDATED_AT
    return text_result(
        f"Categorized transaction {transaction_id} as {category}.",
        {
            "transaction": _transaction_summary(transaction),
            "old_category": old_category,
            "new_category": category,
        },
    )


def _transaction_delete(args: dict[str, Any]) -> dict[str, Any]:
    transaction_id = _string_arg(args, "transaction_id")
    if not transaction_id:
        return _error_result("transaction_id is required")

    for index, transaction in enumerate(_transactions):
        if transaction["transaction_id"] == transaction_id:
            deleted = _transactions.pop(index)
            return text_result(
                f"Deleted transaction {transaction_id}.",
                {
                    "deleted": _transaction_summary(deleted),
                    "transaction_count": len(_transactions),
                },
            )

    return _error_result(
        f"Transaction not found: {transaction_id}",
        code="not_found",
        details={"transaction_id": transaction_id},
    )


_TOOLS = [
    tool(
        name="ledger_summary",
        title="Summarize ledger",
        description="Summarize fixture-backed monthly income, expenses, net, and category totals.",
        input_schema=object_schema(
            {
                "month": {"type": "string", "description": "Month in YYYY-MM format.", "default": _DEFAULT_MONTH},
                "currency": {"type": "string", "description": "3-letter currency code.", "default": _DEFAULT_CURRENCY},
                "account": {"type": "string", "description": "Optional demo account filter."},
            }
        ),
        read_only=True,
    ),
    tool(
        name="transaction_search",
        title="Search transactions",
        description="Search the in-memory fake finance ledger by text and structured filters.",
        input_schema=object_schema(
            {
                "query": {"type": "string", "description": "Case-insensitive text query."},
                "month": {"type": "string", "description": "Optional month in YYYY-MM format."},
                "category": {"type": "string", "description": "Exact category filter."},
                "account": {"type": "string", "description": "Exact account filter."},
                "type": {"type": "string", "enum": ["income", "expense"], "description": "Transaction type filter."},
                "min_amount_cents": {
                    "type": "integer",
                    "description": "Minimum absolute amount in cents.",
                },
                "max_amount_cents": {
                    "type": "integer",
                    "description": "Maximum absolute amount in cents.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            }
        ),
        read_only=True,
    ),
    tool(
        name="transaction_create",
        title="Create transaction",
        description="Create a fake in-memory ledger transaction. Positive amounts are income; negative amounts are expenses.",
        input_schema=object_schema(
            {
                "date": {"type": "string", "description": "Transaction date in YYYY-MM-DD format."},
                "description": {"type": "string", "description": "Human-readable transaction description."},
                "amount_cents": {"type": "integer", "description": "Signed amount in cents; cannot be zero."},
                "currency": {"type": "string", "default": _DEFAULT_CURRENCY},
                "category": {"type": "string", "default": "Uncategorized"},
                "account": {"type": "string", "default": "Demo Cash"},
                "merchant": {"type": "string", "description": "Optional merchant/payee label."},
            },
            required=["date", "description", "amount_cents"],
        ),
        read_only=False,
        idempotent=False,
    ),
    tool(
        name="transaction_categorize",
        title="Categorize transaction",
        description="Update the category for an existing fake ledger transaction.",
        input_schema=object_schema(
            {
                "transaction_id": {"type": "string", "description": "Transaction identifier."},
                "category": {"type": "string", "description": "New category label."},
            },
            required=["transaction_id", "category"],
        ),
        read_only=False,
        idempotent=False,
    ),
    tool(
        name="transaction_delete",
        title="Delete transaction",
        description="Remove a fake ledger transaction from the in-memory demo ledger.",
        input_schema=object_schema(
            {
                "transaction_id": {"type": "string", "description": "Transaction identifier to delete."},
            },
            required=["transaction_id"],
        ),
        read_only=False,
        destructive=True,
        idempotent=False,
    ),
]


SERVER = DemoMcpServer(
    slug="finance-ledger",
    service_slug="demo_finance",
    title="Fake Finance Ledger MCP",
    description="가상의 개인 ledger MCP",
    tools=_TOOLS,
    handlers={
        "ledger_summary": _ledger_summary,
        "transaction_search": _transaction_search,
        "transaction_create": _transaction_create,
        "transaction_categorize": _transaction_categorize,
        "transaction_delete": _transaction_delete,
    },
    reset=_reset_state,
)

_reset_state()
