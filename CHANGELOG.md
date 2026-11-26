# Changelog

## Unreleased

* The text summary names the single oldest overdue invoice
  (invoice id, broker, due date, days overdue, and amount)
  right after the overdue totals, so the worst case can be
  chased first. The line is left out when nothing is
  overdue, and the JSON output is unchanged. The web demo
  prints the same line.

## 1.0.0 - 2026-11-18

Initial release.

* Broker payment history audit over a carrier's invoice CSV
  export: overdue invoices, invoices unpaid more than a
  configurable number of days past due, paid-late invoices,
  slow-pay brokers, one broker carrying too much of the
  book, the same broker name under two different MC numbers,
  and missing or invalid MC numbers.
* Average days to pay, on-time payment rate, overdue totals,
  and a per-broker summary table with invoice count,
  invoiced and paid amounts, average days to pay, overdue
  count, and revenue share.
* Money totals with Decimal cents math, so totals add up
  exactly; a due date is computed from the invoiced date
  plus terms when the due column is empty.
* Data quality checks: duplicate invoice ids, malformed
  rows, invalid or future timestamps, invalid amounts and
  terms, missing due dates and brokers, unknown statuses,
  and impossible date orderings (invoiced before delivered,
  paid before invoiced).
* Void invoices are excluded from the money totals, the
  overdue checks, and the per-broker averages, and are
  counted separately.
* Defensive timestamp parsing (ISO 8601 with or without a
  timezone, plus common day/month/year forms); a bad row is
  reported as a finding and never crashes the run. UTF-8 BOM
  headers and extra columns handled.
* Findings carry an invoice id or broker name, a rule name,
  a severity (error or warning), and a message; errors and
  warnings are counted in the summary.
* Text and --json output, --out file output, --slow-days,
  --max-share, --writeoff-days, and --as-of thresholds, and
  a --check CI gate that exits 1 on errors (and on warnings
  with --strict).
* Zero dependencies, Python 3.10 or newer.
