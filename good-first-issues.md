# Good first issues

Seeded issues for new contributors. File each one on GitHub with
the `good first issue` and `help wanted` labels.

## 1. Add a worst-offenders table

Title: Show the brokers with the worst payment behaviour

Description: After the per-broker summary, print a short table
of the three brokers with the highest average days to pay
(broker, average days to pay, overdue count), so a dispatcher
can see at a glance who to stop booking. Brokers with no paid
invoices are excluded. Ties should break by overdue count,
then by broker name.

Difficulty: Easy

Acceptance criteria: the table appears in text output with the
correct top three for the sample file (Slowpay Brokers at
57.0 days first, then Acme Logistics at 29.0, then Echo
Freight at 28.0); JSON output is unchanged; a test checks
the table rows.

## 2. Add a monthly payment trend

Title: Show invoiced and paid amounts per month

Description: Add a --monthly flag that prints, after the
per-broker summary, one line per invoiced month (month,
invoices, invoiced amount, paid amount), so a carrier can
see whether the book is growing and whether collections
keep up. Months are counted from the invoiced_at date.

Difficulty: Easy

Acceptance criteria: the monthly table appears with the
correct amounts for the sample file (2026-07: 1 invoice,
$4,100.00 invoiced, $0.00 paid; 2026-09: 3 invoices,
$9,000.00 invoiced, $5,800.00 paid; 2026-10: 12 invoices,
$18,850.00 invoiced, $12,750.00 paid; 2026-11: 1 invoice,
$2,100.00 invoiced, $2,100.00 paid; the invoice with no
readable invoiced date is left out of the table); JSON
output is unchanged; a test checks the monthly rows.

## 3. Support a custom slow-pay minimum

Title: Let teams set how many paid invoices a slow-pay call needs

Description: The slow-pay rule needs at least 2 paid invoices
per broker before it fires, so one slow payment cannot flag
a broker. Add a --min-paid flag that sets that minimum.
When the flag is absent, the minimum stays 2.

Difficulty: Easy

Acceptance criteria: with --min-paid 3 the Slowpay Brokers
warning disappears from the sample report, and with
--min-paid 1 a single-invoice broker with a 50 day payment
is flagged; a test covers both cases; README documents the
flag.

## 4. Report the oldest overdue invoice in the summary

Title: Print the single oldest overdue invoice

Description: Add one line to the text summary naming the
oldest overdue invoice (invoice id, broker, due date, days
overdue, amount), so a carrier can act on the worst case
without reading every finding. When nothing is overdue,
the line is omitted.

Difficulty: Easy

Acceptance criteria: the line appears for the sample file
naming INV-104, Beta Freight, due 2026-08-01, 109 days
overdue, $4,100.00; it is absent for the clean file; JSON
output is unchanged; a test checks both cases.
