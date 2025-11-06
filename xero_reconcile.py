"""Rule-based bank transaction coding helper for Xero imports.

This module provides a small rule engine that can express arbitrarily nested
``and``/``or`` logic for matching bank feed transactions and producing the
fields required by Xero's *cash coding* CSV import.  It is intentionally
dependency free so it can run in environments where only the Python standard
library is available.

Typical workflow
----------------

1. Export or prepare a CSV that mirrors the columns used by Xero's bank
   transaction import (Date, Amount, Description, Reference, Contact, Account
   Code, Tax Rate, Tracking, Bank Account).  Only ``Date``, ``Amount``, and
   ``Bank Account`` are strictly required at load time; everything else can be
   populated by rules.
2. Describe your coding rules in JSON or YAML.  Rules are evaluated in
   priority order, support nested ``all``/``any`` (AND/OR) expressions, and can
   populate or override any of the transaction fields.
3. Run the CLI: ``python xero_reconcile.py rules.yaml bank_feed.csv coded.csv``
   and upload the resulting ``coded.csv`` file in Xero's *Import Statement*
   screen.

See ``--help`` for the available flags and the documentation in the repository
for worked examples.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, MutableMapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Utility helpers


STANDARD_FIELDS = (
    ("date", "Date"),
    ("amount", "Amount"),
    ("description", "Description"),
    ("reference", "Reference"),
    ("contact", "Contact"),
    ("account_code", "Account Code"),
    ("tax_rate", "Tax Rate"),
    ("tracking", "Tracking"),
    ("bank_account", "Bank Account"),
)

STANDARD_CANONICAL = [field for field, _ in STANDARD_FIELDS]
EXPORT_HEADER_NAMES = {field: label for field, label in STANDARD_FIELDS}
EXPORT_HEADER_NAMES.setdefault("matched_rules", "Matched Rules")


def _export_key(key: str) -> str:
    return EXPORT_HEADER_NAMES.get(key, key)


def _normalise_header(header: str) -> str:
    """Normalise CSV headers to a canonical form.

    Xero exports often use a mix of spaces or different cases.  We lower-case
    the string, strip surrounding whitespace, replace spaces with underscores
    and remove any non-alphanumeric/underscore characters.
    """

    cleaned = re.sub(r"[^0-9a-z_]+", "", header.strip().lower().replace(" ", "_"))
    return cleaned


def _guess_numeric(value: str) -> Optional[float]:
    """Attempt to coerce a CSV value to a float.

    Returns ``None`` if the value is blank or cannot be parsed.  Xero amounts
    typically use ``-`` for outgoings, so we keep the sign as-is.
    """

    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, (list, tuple)):
        return value
    return (value,)


# ---------------------------------------------------------------------------
# Transaction model


@dataclass
class Transaction:
    """Container representing an individual bank transaction.

    The original CSV row is preserved so the caller can re-export the data with
    any added coding fields.
    """

    source_row: MutableMapping[str, str]
    row_number: int
    matched_rules: List[str] = field(default_factory=list)

    def get(self, field: str, default: Any = "") -> Any:
        canon = _normalise_header(field)
        for candidate in (canon, field, field.replace(" ", "")):
            if candidate in self.source_row:
                return self.source_row[candidate]
        return default

    def amount(self) -> Optional[float]:
        return _guess_numeric(self.get("amount"))

    def update(self, **fields: str) -> None:
        for key, value in fields.items():
            canon = _normalise_header(key)
            self.source_row[canon] = value

    def mark_rule(self, name: str) -> None:
        if name not in self.matched_rules:
            self.matched_rules.append(name)


# ---------------------------------------------------------------------------
# Matchers and conditions


class Matcher:
    def evaluate(self, txn: Transaction) -> bool:
        raise NotImplementedError


@dataclass
class Condition(Matcher):
    field: str
    operator: str
    value: Any
    negate: bool = False

    def evaluate(self, txn: Transaction) -> bool:
        actual_raw = txn.get(self.field)
        actual = actual_raw
        operator = self.operator.lower()
        expected_values = _as_sequence(self.value)

        if operator in {"gt", "gte", "lt", "lte", "between"}:
            actual_num = _guess_numeric(actual_raw)
            if actual_num is None:
                result = False
            else:
                if operator == "gt":
                    result = actual_num > float(expected_values[0])
                elif operator == "gte":
                    result = actual_num >= float(expected_values[0])
                elif operator == "lt":
                    result = actual_num < float(expected_values[0])
                elif operator == "lte":
                    result = actual_num <= float(expected_values[0])
                else:  # between
                    low, high = expected_values
                    result = float(low) <= actual_num <= float(high)
        else:
            # Treat everything else as string comparisons
            actual_text = "" if actual is None else str(actual)
            cmp_values = ["" if v is None else str(v) for v in expected_values]

            if operator in {"eq", "equals"}:
                result = actual_text in cmp_values
            elif operator in {"neq", "ne", "not_equals"}:
                result = actual_text not in cmp_values
            elif operator in {"contains", "icontains"}:
                haystack = actual_text.lower() if operator.startswith("i") else actual_text
                result = any((needle.lower() if operator.startswith("i") else needle) in haystack for needle in cmp_values)
            elif operator in {"startswith", "istartswith"}:
                haystack = actual_text.lower() if operator.startswith("i") else actual_text
                result = any(haystack.startswith(needle.lower() if operator.startswith("i") else needle) for needle in cmp_values)
            elif operator in {"endswith", "iendswith"}:
                haystack = actual_text.lower() if operator.startswith("i") else actual_text
                result = any(haystack.endswith(needle.lower() if operator.startswith("i") else needle) for needle in cmp_values)
            elif operator in {"regex", "iregex"}:
                flags = re.IGNORECASE if operator.startswith("i") else 0
                result = any(re.search(pattern, actual_text or "", flags) for pattern in cmp_values)
            elif operator in {"in", "isin"}:
                result = actual_text in cmp_values
            else:
                raise ValueError(f"Unsupported operator: {self.operator}")

        return not result if self.negate else result


@dataclass
class AllMatcher(Matcher):
    children: Sequence[Matcher]

    def evaluate(self, txn: Transaction) -> bool:
        return all(child.evaluate(txn) for child in self.children)


@dataclass
class AnyMatcher(Matcher):
    children: Sequence[Matcher]

    def evaluate(self, txn: Transaction) -> bool:
        return any(child.evaluate(txn) for child in self.children)


@dataclass
class NotMatcher(Matcher):
    child: Matcher

    def evaluate(self, txn: Transaction) -> bool:
        return not self.child.evaluate(txn)


class AlwaysMatcher(Matcher):
    def evaluate(self, txn: Transaction) -> bool:  # pragma: no cover - trivial
        return True


# ---------------------------------------------------------------------------
# Rules


@dataclass
class Rule:
    name: str
    matcher: Matcher
    actions: Mapping[str, str]
    priority: int = 0
    stop_processing: bool = False
    description: str | None = None

    def applies(self, txn: Transaction) -> bool:
        return self.matcher.evaluate(txn)


class RuleSet:
    def __init__(self, rules: Sequence[Rule]):
        self.rules = sorted(rules, key=lambda r: (-r.priority, r.name.lower()))

    def apply(self, txn: Transaction) -> None:
        for rule in self.rules:
            if rule.applies(txn):
                txn.update(**rule.actions)
                txn.mark_rule(rule.name)
                if rule.stop_processing:
                    break


def _build_matcher(spec: Any) -> Matcher:
    if spec is None:
        return AlwaysMatcher()

    if isinstance(spec, Mapping):
        if "all" in spec:
            return AllMatcher([_build_matcher(item) for item in _as_sequence(spec["all"])])
        if "any" in spec:
            return AnyMatcher([_build_matcher(item) for item in _as_sequence(spec["any"])])
        if "not" in spec:
            return NotMatcher(_build_matcher(spec["not"]))
        if "field" in spec:
            return Condition(
                field=spec["field"],
                operator=spec.get("operator", "eq"),
                value=spec.get("value"),
                negate=bool(spec.get("negate", False)),
            )
    raise ValueError(f"Unrecognised matcher spec: {spec!r}")


def _parse_rules(data: Any) -> List[Rule]:
    raw_rules = data.get("rules", data) if isinstance(data, Mapping) else data
    if not isinstance(raw_rules, Sequence):
        raise ValueError("Rules file must be a list or have a top-level 'rules' key")

    parsed: List[Rule] = []
    for entry in raw_rules:
        if not isinstance(entry, Mapping):
            raise ValueError("Each rule must be a mapping/dictionary")
        name = str(entry.get("name") or f"rule_{len(parsed)+1}")
        matcher = _build_matcher(entry.get("match"))
        actions = entry.get("set") or entry.get("assign")
        if not isinstance(actions, Mapping) or not actions:
            raise ValueError(f"Rule '{name}' must define a non-empty 'set' mapping")
        parsed.append(
            Rule(
                name=name,
                matcher=matcher,
                actions={_normalise_header(k): str(v) for k, v in actions.items()},
                priority=int(entry.get("priority", 0)),
                stop_processing=bool(entry.get("stop", entry.get("stop_processing", False))),
                description=entry.get("description"),
            )
        )
    return parsed


def load_rules(path: Path) -> RuleSet:
    text = path.read_text(encoding="utf-8")
    data: Any
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - runtime safeguard
            raise RuntimeError("PyYAML is required to load YAML rule files.") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return RuleSet(_parse_rules(data))


# ---------------------------------------------------------------------------
# CSV I/O helpers


def read_transactions(csv_path: Path) -> Iterator[Transaction]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        normalised_fieldnames = [_normalise_header(name) for name in reader.fieldnames or []]
        reader.fieldnames = normalised_fieldnames
        for idx, row in enumerate(reader, start=2):  # account for header row being 1
            yield Transaction(row, row_number=idx)


def write_transactions(csv_path: Path, transactions: Sequence[Transaction], include_rule_column: bool = True) -> None:
    headers = list(STANDARD_CANONICAL)
    # Include any additional columns that may have been in the source file
    extra_headers = sorted({key for txn in transactions for key in txn.source_row.keys()} - set(headers))
    headers.extend(extra_headers)
    if include_rule_column and "matched_rules" not in headers:
        headers.append("matched_rules")

    export_headers = [_export_key(header) for header in headers]

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=export_headers)
        writer.writeheader()
        for txn in transactions:
            row = { _export_key(key): value for key, value in txn.source_row.items() }
            if include_rule_column:
                row[_export_key("matched_rules")] = "; ".join(txn.matched_rules)
            writer.writerow(row)


# ---------------------------------------------------------------------------
# CLI


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply AND/OR rules to Xero bank feed transactions.")
    parser.add_argument("rules", type=Path, help="Path to the JSON/YAML rules file.")
    parser.add_argument("input", type=Path, help="CSV file containing raw bank feed transactions.")
    parser.add_argument("output", type=Path, help="Destination CSV for coded transactions.")
    parser.add_argument("--unmatched", type=Path, help="Optional CSV file for transactions that matched no rules.")
    parser.add_argument("--strict", action="store_true", help="Exit with a non-zero status if any transactions remain unmatched.")
    parser.add_argument("--summary", action="store_true", help="Print a summary of rule matches to stdout.")
    return parser.parse_args(argv)


def _summarise(transactions: Sequence[Transaction]) -> str:
    total = len(transactions)
    matched = sum(1 for txn in transactions if txn.matched_rules)
    unmatched = total - matched
    counts: Dict[str, int] = {}
    for txn in transactions:
        for rule_name in txn.matched_rules:
            counts[rule_name] = counts.get(rule_name, 0) + 1
    lines = [
        f"Total transactions: {total}",
        f"Matched transactions: {matched}",
        f"Unmatched transactions: {unmatched}",
    ]
    if counts:
        lines.append("\nRule hit counts:")
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  {name}: {count}")
    return "\n".join(lines)


def run_cli(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    rule_set = load_rules(args.rules)
    transactions = list(read_transactions(args.input))

    for txn in transactions:
        rule_set.apply(txn)

    write_transactions(args.output, transactions)

    unmatched = [txn for txn in transactions if not txn.matched_rules]
    if args.unmatched:
        write_transactions(args.unmatched, unmatched)

    if args.summary:
        print(_summarise(transactions))

    if args.strict and unmatched:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(run_cli())

