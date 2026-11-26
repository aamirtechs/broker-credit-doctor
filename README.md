# broker-credit-doctor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/broker-credit-doctor/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/broker-credit-doctor/issues)

A zero-dependency Python CLI that audits a carrier's broker
payment history CSV export for the payment risks that cost
small carriers real money: invoices overdue past their
terms, invoices unpaid more than 90 days past due (surety
bond claim territory), brokers that pay late again and
again, one broker carrying too much of the book, the same
broker name under two different MC numbers (a double
brokering signal), missing or invalid MC numbers, duplicate
invoice ids, malformed rows, and unreadable dates. It also
prints the average days to pay, the on-time payment rate,
and a per-broker summary table, so a dispatcher can see
which brokers to keep booking and which to avoid.

Net 30 is the industry standard and the average broker pays
in about 33 days, but stated terms and actual days to pay
are different numbers, and a broker averaging 46 days or
more is a published red flag. More than $2 billion in
carrier cash is tied up by slow-paying brokers, and the
credit scores that track this live in paid platforms such
as Carrier411, TransCredit, and Highway. This tool is the
same math run on the carrier's own payment history,
offline, before the next load is booked.

## Install

Requires Python 3.10 or newer. No dependencies beyond the
standard library.

```bash
pip install broker-credit-doctor
```

Or run it straight from the source checkout:

```bash
python3 broker_credit_doctor.py examples/invoices.csv
```

## Input format

A CSV export with a header row, one row per invoice.
Columns used: `id` (or `invoice_id`), `broker`,
`broker_mc`, `delivered_at`, `invoiced_at`, `amount`,
`terms_days`, `due_at`, `paid_at`, and `status`. Extra
columns are ignored, so a full payment history export
works as-is. A UTF-8 BOM header is handled. Only `id`,
`invoiced_at`, and `amount` are required; the other
columns are optional, and the checks that need a missing
column are skipped.

`invoiced_at` also accepts `invoiced` or `invoice_date`,
`broker_mc` also accepts `mc_number` or `mc`,
`delivered_at` also accepts `delivered` or
`delivery_date`, `terms_days` also accepts `terms` or
`payment_terms`, and `amount` also accepts `total` or
`invoice_amount`.

A due date is computed from the invoiced date plus
`terms_days` when the `due_at` column is empty. Dates
accept ISO 8601 with or without a timezone
(`2026-11-01 09:00`, `2026-11-01T09:30:00Z`,
`2026-11-01`), plus day/month/year and month/day/year
forms. A date with no timezone is read as UTC. Amounts
accept a plain number, with an optional dollar sign and
thousands commas (`$2,500.00`).

```csv
id,broker,broker_mc,delivered_at,invoiced_at,amount,terms_days,due_at,paid_at,status
INV-101,Acme Logistics,123456,2026-10-01,2026-10-02,2500.00,30,2026-11-01,2026-10-28,paid
INV-102,Acme Logistics,123456,2026-10-05,2026-10-06,1800.00,30,2026-11-05,2026-11-10,paid
INV-103,Beta Freight,234567,2026-09-01,2026-09-02,3200.00,30,2026-10-02,,unpaid
INV-104,Beta Freight,234567,2026-07-01,2026-07-02,4100.00,30,2026-08-01,,unpaid
INV-105,Gamma Transport,345678,2026-10-10,2026-10-11,1500.00,30,2026-11-25,,unpaid
```

Status is `paid`, `unpaid`, or `void`. Void invoices are
excluded from the money totals, the overdue checks, and
the per-broker averages, and are counted separately.

## What it checks

Errors, the ones that mean unpaid or badly recorded money:

* `overdue-invoice`: an unpaid invoice past its due date.
* `unpaid-over-90`: an unpaid invoice more than
  `--writeoff-days` (default 90) past due, so it is
  surety bond claim territory.
* `concentration-risk`: one broker carries more than
  `--max-share` (default 25) percent of all invoiced
  revenue, so one failure would hit the whole book. Only
  checked when the file has at least 5 invoices, since
  concentration is meaningless on a tiny book.
* `duplicate-invoice-id`: the same invoice id twice in
  the export.
* `malformed-row`: a row with the wrong number of fields.
  The row is skipped and reported, never a crash.
* `invalid-timestamp`: a date field that matches no known
  format. The row is still audited on the fields that did
  parse.
* `future-timestamp`: a delivered, invoiced, or paid date
  after `--as-of` (default now). A due date in the future
  is normal and is never flagged here.
* `invalid-amount`: an amount that is not a valid
  non-negative number. It is left out of the money totals.
* `missing-due-date`: no `due_at` and no `terms_days` to
  compute one from, so the invoice has no due date.
* `invoiced-before-delivered`: `invoiced_at` earlier than
  `delivered_at`.
* `paid-before-invoiced`: `paid_at` earlier than
  `invoiced_at`.
* `invalid-status`: a status that is not paid, unpaid, or
  void.

Warnings, patterns worth investigating:

* `paid-late`: a paid invoice settled after its due date,
  with the days late.
* `slow-pay-broker`: a broker averaging more than
  `--slow-days` (default 45) days to pay over at least
  two paid invoices.
* `broker-mc-mismatch`: a broker name first seen under
  one MC number appears under a different one, so check
  for double brokering.
* `missing-mc`: an empty `broker_mc`, so the broker
  cannot be checked on SAFER or Carrier411.
* `invalid-mc`: a `broker_mc` that is not a 6 or 7 digit
  MC number.
* `terms-mismatch`: `due_at` does not match the invoiced
  date plus `terms_days`, so either the terms or the due
  date is wrong.
* `invalid-terms`: a `terms_days` that is not a whole
  number of days. It is ignored.
* `missing-broker`: an invoice with no broker name, so it
  cannot be counted in the per-broker summary.

The summary reports the total invoiced, total paid, and
outstanding amounts with Decimal cents math, the overdue
invoice count and amount, the single oldest overdue
invoice (invoice id, broker, due date, days overdue, and
amount, so the worst case can be chased first; the line is
left out when nothing is overdue), the average days to
pay, the on-time payment rate, and a per-broker table with
invoice count, invoiced and paid amounts, average days to
pay, overdue count, and revenue share.

## Usage

```bash
python3 broker_credit_doctor.py examples/invoices.csv --as-of 2026-11-18
```

Captured output:

```
ERROR [invalid-amount] INV-112: amount '-500.00' is not a valid non-negative amount, so it was left out of the money totals
ERROR [invalid-amount] INV-113: amount 'lots' is not a valid non-negative amount, so it was left out of the money totals
ERROR [invalid-timestamp] INV-114: invoiced_at 'not-a-date' is not a readable date
ERROR [missing-due-date] INV-114: due_at is empty and no terms_days was given, so the invoice has no due date to check
ERROR [future-timestamp] INV-115: paid_at 2026-12-05 00:00 is after the as-of date 2026-11-18 00:00
ERROR [duplicate-invoice-id] INV-101: invoice id appears more than once in the export
ERROR [malformed-row] row 18: row has 11 fields but the header has 10, so the row was skipped
WARNING [paid-late] INV-102: invoice was paid 5 days after its due date 2026-11-05
ERROR [overdue-invoice] INV-103: invoice is 47 days past its due date 2026-10-02 and still unpaid
ERROR [overdue-invoice] INV-104: invoice is 109 days past its due date 2026-08-01 and still unpaid
ERROR [unpaid-over-90] INV-104: invoice is 109 days past due, over the limit of 90 days, so it is bond claim territory
WARNING [terms-mismatch] INV-105: due_at 2026-11-25 does not match invoiced_at plus 30 days (2026-11-10), so either the terms or the due date is wrong
WARNING [missing-mc] INV-107: broker_mc is empty, so the broker cannot be checked on SAFER or Carrier411
WARNING [invalid-mc] INV-108: broker_mc 'ABC12' is not a 6 or 7 digit MC number
WARNING [broker-mc-mismatch] INV-109: broker Acme Logistics was first seen under MC 123456 but this invoice uses MC 999999, so check for double brokering
ERROR [invoiced-before-delivered] INV-110: invoiced_at 2026-10-02 is before delivered_at 2026-10-03
ERROR [paid-before-invoiced] INV-111: paid_at 2026-10-05 is before invoiced_at 2026-10-09
ERROR [overdue-invoice] INV-112: invoice is 6 days past its due date 2026-11-12 and still unpaid
ERROR [overdue-invoice] INV-113: invoice is 4 days past its due date 2026-11-14 and still unpaid
WARNING [paid-late] INV-116: invoice was paid 25 days after its due date 2026-10-02
WARNING [paid-late] INV-117: invoice was paid 29 days after its due date 2026-10-06
WARNING [slow-pay-broker] Slowpay Brokers: broker averages 57.0 days to pay over 2 paid invoices, over the limit of 45 days
ERROR [concentration-risk] Acme Logistics: broker carries 32.8% of all invoiced revenue, over the limit of 25%, so one failure would hit the whole book

Invoices: 18, findings: 23, errors: 15, warnings: 8
Total invoiced: $35,350.00, total paid: $20,650.00, outstanding: $14,700.00
Overdue: 4 invoices, $7,300.00
Oldest overdue: INV-104, Beta Freight, due 2026-08-01, 109 days overdue, $4,100.00
Average days to pay: 36.9
On-time payment rate: 57.1%

Per-broker summary:
broker           invoices     invoiced         paid  avg dtp  overdue   share
Acme Logistics          5   $11,600.00    $9,000.00     29.0        0   32.8%
Beta Freight            2    $7,300.00        $0.00        -        2   20.7%
Delta Carriers          2    $2,000.00        $0.00        -        0    5.7%
Echo Freight            2    $3,750.00    $3,750.00     28.0        0   10.6%
Foxtrot Lines           2        $0.00        $0.00        -        2    0.0%
Gamma Transport         1    $1,500.00        $0.00        -        0    4.2%
Golf Logistics          1    $1,300.00        $0.00        -        0    3.7%
Hotel Freight           1    $2,100.00    $2,100.00        -        0    5.9%
Slowpay Brokers         2    $5,800.00    $5,800.00     57.0        0   16.4%
```

The sample has 19 rows; the malformed row 18 is skipped, so
18 invoices are audited. The headline numbers are
$14,700.00 outstanding, 4 overdue invoices worth $7,300.00,
an average of 36.9 days to pay, and a 57.1% on-time rate.
Beta Freight holds both long-overdue invoices (INV-104 is
109 days past due), Slowpay Brokers averages 57.0 days to
pay, and Acme Logistics carries 32.8% of the book. Hotel
Freight counts as paid in the totals because its status is
paid, even though its paid date is in the future and is
itself an error.

Use it as a CI gate: `--check` exits 1 when any error is
found, and 0 otherwise. On the sample above it exits 1:

```bash
python3 broker_credit_doctor.py examples/invoices.csv --as-of 2026-11-18 --check
```

On `examples/clean.csv`, four clean invoices, it exits 0:

```
Invoices: 4, findings: 0, errors: 0, warnings: 0
Total invoiced: $8,000.00, total paid: $6,000.00, outstanding: $2,000.00
Overdue: 0 invoices, $0.00
Average days to pay: 27.3
On-time payment rate: 100.0%

Per-broker summary:
broker           invoices     invoiced         paid  avg dtp  overdue   share
Acme Logistics          1    $2,000.00    $2,000.00     26.0        0   25.0%
Beta Freight            1    $2,000.00        $0.00        -        0   25.0%
Delta Carriers          1    $2,000.00    $2,000.00     27.0        0   25.0%
Gamma Transport         1    $2,000.00    $2,000.00     29.0        0   25.0%

All invoices clean.
```

With `--strict`, `--check` also fails on warnings.
`examples/warnings-only.csv` has one paid-late invoice and
one invoice with no MC number, so it passes `--check`
(exit 0) and fails `--check --strict` (exit 1). With a
higher slow-pay limit the slow payer drops out of the
sample report:

```bash
python3 broker_credit_doctor.py examples/invoices.csv --as-of 2026-11-18 --slow-days 60 --check
```

That run still exits 1 on the sample's errors, but the
`slow-pay-broker` warning for Slowpay Brokers is gone.

`--json` emits the summary, the per-broker table, and the
findings as JSON (money amounts as strings, to keep the
cents exact), and `--out` writes the report to a file.

## Options

| Flag | Meaning |
| --- | --- |
| `--slow-days` | A broker averaging more than this many days to pay is a slow payer (default: 45) |
| `--max-share` | One broker carrying more than this percent of invoiced revenue is a concentration risk (default: 25) |
| `--writeoff-days` | An unpaid invoice more than this many days past due is bond claim territory (default: 90) |
| `--as-of` | Date to compare timestamps against, for example 2026-11-18 (default: now) |
| `--json` | Emit JSON instead of text |
| `--check` | Exit 1 if any error is found |
| `--strict` | With --check, also exit 1 on warnings |
| `--out` | Write the report to a file |

## Edge cases

* A row with too many or too few fields is a malformed-row
  error and is skipped; the rest of the file is still
  audited.
* A bad date is an invalid-timestamp error, and the row is
  still audited on the fields that did parse, so one bad
  date never hides the other problems on the invoice.
* Money totals use the status column: an invoice marked
  paid counts as paid even when its paid date is bad, and
  the bad date is itself an error. Average days to pay and
  the on-time rate only count paid invoices with a sane
  paid date (on or after the invoiced date and not in the
  future), so a misdated row cannot drag the average down.
* An invalid amount is left out of the money totals
  entirely, including the overdue amount, so the totals
  only ever add up real numbers.
* The concentration check only runs when the file has at
  least 5 invoices, and the slow-pay check only counts a
  broker with at least 2 paid invoices, so a tiny book
  cannot trigger either one on noise.
* A due date in the future is normal and never fires the
  future-timestamp check; only delivered, invoiced, and
  paid dates after the as-of date are errors.
* A blank line in the file is skipped silently. An invoice
  with no broker is grouped under `(no broker)` in the
  per-broker table.
* `--as-of` makes runs reproducible: without it, the
  future-timestamp check compares against the current time.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

72 tests, all passing: amount and terms parsing, timestamp
parsing, header aliases and BOM handling, malformed rows,
duplicate ids, invalid and future timestamps, due date
computation from terms, overdue and writeoff rules, paid
late rules, days-to-pay math, MC number rules and the
double brokering signal, slow-pay and concentration rules
with their minimum-count gates, void invoice handling,
summary counts and money totals, per-broker tables, the
oldest overdue invoice line, text and JSON output, and
`--check` and `--strict` exit codes. The same 72 tests
pass under pytest.

## License

MIT. See [LICENSE](LICENSE).
