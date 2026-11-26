"""Broker payment history audit CLI.

Reads a carrier's broker payment history CSV export (one row
per invoice: broker, MC number, delivered and invoiced
dates, amount, payment terms, due date, paid date, status)
and audits it for the payment risks that cost small carriers
real money: invoices overdue past their terms, invoices
unpaid more than 90 days past due (surety bond claim
territory), brokers paid late again and again, one broker
carrying too much of the book, the same broker name under
two different MC numbers (a double brokering signal),
missing or invalid MC numbers, and bad data such as
duplicate invoice ids, malformed rows, and unreadable dates.

Net 30 is the industry standard and the average broker pays
in about 33 days, but stated terms and actual days to pay
are different numbers, and a broker averaging 46 days or
more is a published red flag. The credit scores that track
this live in paid platforms such as Carrier411, TransCredit,
and Highway. This tool is the same math run on the carrier's
own payment history, offline, before the next load is
booked.

Each problem is a finding with an invoice id (or a broker
name for broker level findings), a rule name, a severity
(error or warning), and a message. Errors are unpaid or
badly recorded money; warnings are patterns worth
investigating. --check exits 1 when any error is found, and
--strict also fails on warnings.

Money uses Decimal cents math, so totals add up exactly.
Dates are parsed defensively. A bad date is reported as a
finding and the row is still audited on the fields that did
parse, so one bad row never crashes the run.

Zero dependencies, Python 3.10 or newer, standard library only.

Usage:
    python3 broker_credit_doctor.py examples/invoices.csv
    python3 broker_credit_doctor.py invoices.csv --json --check
    python3 broker_credit_doctor.py invoices.csv --as-of 2026-11-18
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

__version__ = "1.0.0"

DEFAULT_SLOW_DAYS = 45
DEFAULT_MAX_SHARE = 25
DEFAULT_WRITEOFF_DAYS = 90
MIN_INVOICES_FOR_CONCENTRATION = 5
MIN_PAID_FOR_SLOW_PAY = 2

KNOWN_STATUSES = {"paid", "unpaid", "void"}

TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y",
)

MC_PATTERN = re.compile(r"^\d{6,7}$")


class Finding:
    """One audit finding for one invoice, or for one broker."""

    def __init__(self, invoice, rule, severity, message):
        self.invoice = invoice
        self.rule = rule
        self.severity = severity
        self.message = message

    def as_dict(self):
        """Return the finding as a plain dict."""
        return {"invoice": self.invoice, "rule": self.rule,
                "severity": self.severity, "message": self.message}

    def __str__(self):
        return "%s [%s] %s: %s" % (self.severity.upper(), self.rule,
                                   self.invoice, self.message)


def parse_timestamp(text):
    """Parse a timestamp string into an aware datetime, or None.

    Accepts ISO 8601 with or without a timezone (a trailing Z is
    handled), plus a few common export formats. A naive timestamp
    is read as UTC. Returns None for an empty string or a value
    that matches no known format, so callers can report it
    instead of crashing.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    candidate = text
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_amount(text):
    """Parse a money amount, returning (amount, is_valid).

    An empty field means no amount was recorded. A value that
    is not a number, or a negative amount, is invalid.
    """
    if text is None:
        return None, False
    text = text.strip().replace("$", "").replace(",", "")
    if not text:
        return None, False
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, False
    if amount < 0:
        return None, False
    return amount, True


def parse_terms(text):
    """Parse a payment terms day count, returning (days, valid).

    An empty field means no terms were recorded. A value that
    is not a whole number of days is invalid.
    """
    if text is None:
        return None, True
    text = text.strip()
    if not text:
        return None, True
    try:
        days = int(text)
    except ValueError:
        return None, False
    if days < 0:
        return None, False
    return days, True


def find_column(header, names):
    """Return the first of names present in header, or None."""
    for name in names:
        if name in header:
            return name
    return None


def money(amount):
    """Format a Decimal amount as dollars and cents."""
    return "$%s" % format(amount, ",.2f")


def parse_csv(path, as_of):
    """Parse a broker payment history CSV export.

    Returns (invoices, findings). Each invoice is a dict with
    the id, broker, MC number, the parsed dates, the amount,
    the terms, the due date, and the status. A due date is
    computed from invoiced date plus terms when the due column
    is empty. Rows with the wrong number of fields are
    reported as malformed-row errors and skipped, never a
    crash. A UTF-8 BOM header is handled. Extra columns are
    ignored.
    """
    findings = []
    invoices = []
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("%s is empty, no header row found" % path)
        except csv.Error as exc:
            raise ValueError("%s is not a readable CSV file: %s"
                             % (path, exc))
        header = [name.strip() for name in header]
        id_column = find_column(header, ("id", "invoice_id",
                                         "invoice_number"))
        if id_column is None:
            raise ValueError(
                "%s has no id or invoice_id column (header is: %s)"
                % (path, ", ".join(header)))
        invoiced_column = find_column(
            header, ("invoiced_at", "invoiced", "invoice_date"))
        if invoiced_column is None:
            raise ValueError(
                "%s has no invoiced_at column (header is: %s)"
                % (path, ", ".join(header)))
        amount_column = find_column(
            header, ("amount", "total", "invoice_amount"))
        if amount_column is None:
            raise ValueError(
                "%s has no amount column (header is: %s)"
                % (path, ", ".join(header)))
        broker_column = find_column(
            header, ("broker", "broker_name"))
        mc_column = find_column(
            header, ("broker_mc", "mc_number", "mc"))
        delivered_column = find_column(
            header, ("delivered_at", "delivered", "delivery_date"))
        terms_column = find_column(
            header, ("terms_days", "terms", "payment_terms"))
        due_column = find_column(header, ("due_at", "due_date"))
        paid_column = find_column(header, ("paid_at", "paid_date"))
        status_column = find_column(header, ("status", "state"))
        seen_ids = set()
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(header):
                findings.append(Finding(
                    "row %d" % line_number, "malformed-row", "error",
                    "row has %d fields but the header has %d, "
                    "so the row was skipped" % (len(row), len(header))))
                continue
            cells = dict(zip(header, (cell.strip() for cell in row)))
            invoice_id = cells[id_column] or "row %d" % line_number
            if invoice_id in seen_ids:
                findings.append(Finding(
                    invoice_id, "duplicate-invoice-id", "error",
                    "invoice id appears more than once in the export"))
            else:
                seen_ids.add(invoice_id)
            invoice = {"id": invoice_id,
                       "broker": (cells[broker_column]
                                  if broker_column else ""),
                       "broker_mc": (cells[mc_column]
                                     if mc_column else ""),
                       "status": (cells[status_column].lower()
                                  if status_column else ""),
                       "delivered_at": None,
                       "invoiced_at": None,
                       "due_at": None,
                       "paid_at": None,
                       "amount": None,
                       "terms_days": None,
                       "line": line_number}
            for column, key, label in (
                    (delivered_column, "delivered_at", "delivered_at"),
                    (invoiced_column, "invoiced_at", "invoiced_at"),
                    (due_column, "due_at", "due_at"),
                    (paid_column, "paid_at", "paid_at")):
                if column is None:
                    continue
                raw = cells[column]
                if not raw:
                    continue
                parsed = parse_timestamp(raw)
                if parsed is None:
                    findings.append(Finding(
                        invoice_id, "invalid-timestamp", "error",
                        "%s %r is not a readable date"
                        % (label, raw)))
                else:
                    invoice[key] = parsed
            for key, label in (
                    ("delivered_at", "delivered_at"),
                    ("invoiced_at", "invoiced_at"),
                    ("paid_at", "paid_at")):
                value = invoice[key]
                if value is not None and value > as_of:
                    findings.append(Finding(
                        invoice_id, "future-timestamp", "error",
                        "%s %s is after the as-of date %s"
                        % (label, value.strftime("%Y-%m-%d %H:%M"),
                           as_of.strftime("%Y-%m-%d %H:%M"))))
            amount_raw = cells[amount_column]
            amount, valid = parse_amount(amount_raw)
            if not valid:
                findings.append(Finding(
                    invoice_id, "invalid-amount", "error",
                    "amount %r is not a valid non-negative amount, "
                    "so it was left out of the money totals"
                    % amount_raw))
            else:
                invoice["amount"] = amount
            if terms_column is not None:
                terms, valid = parse_terms(cells[terms_column])
                if not valid:
                    findings.append(Finding(
                        invoice_id, "invalid-terms", "warning",
                        "terms_days %r is not a whole number of "
                        "days, so it was ignored"
                        % cells[terms_column]))
                else:
                    invoice["terms_days"] = terms
            if invoice["due_at"] is None \
                    and invoice["invoiced_at"] is not None \
                    and invoice["terms_days"] is not None:
                invoice["due_at"] = invoice["invoiced_at"] + \
                    timedelta(days=invoice["terms_days"])
            if invoice["due_at"] is None \
                    and invoice["status"] != "void":
                findings.append(Finding(
                    invoice_id, "missing-due-date", "error",
                    "due_at is empty and no terms_days was given, "
                    "so the invoice has no due date to check"))
            invoices.append(invoice)
    return invoices, findings


def days_to_pay(invoice, as_of):
    """Return the days from invoicing to payment, or None.

    Only paid invoices with a sane paid date count: the paid
    date must be on or after the invoiced date and not in the
    future, so a misdated row cannot drag the average down.
    """
    if invoice["status"] != "paid":
        return None
    invoiced = invoice["invoiced_at"]
    paid = invoice["paid_at"]
    if invoiced is None or paid is None:
        return None
    if paid < invoiced or paid > as_of:
        return None
    return (paid - invoiced).days


def audit(invoices, as_of, slow_days=DEFAULT_SLOW_DAYS,
          max_share=DEFAULT_MAX_SHARE,
          writeoff_days=DEFAULT_WRITEOFF_DAYS):
    """Audit a parsed invoice list and return a list of findings."""
    findings = []
    live = [invoice for invoice in invoices
            if invoice["status"] != "void"]
    first_mc = {}
    for invoice in live:
        broker = invoice["broker"]
        mc_number = invoice["broker_mc"]
        if broker and mc_number and broker not in first_mc:
            first_mc[broker] = mc_number
    for invoice in live:
        invoice_id = invoice["id"]
        status = invoice["status"]
        delivered = invoice["delivered_at"]
        invoiced = invoice["invoiced_at"]
        due = invoice["due_at"]
        paid = invoice["paid_at"]

        if status and status not in KNOWN_STATUSES:
            findings.append(Finding(
                invoice_id, "invalid-status", "error",
                "status %r is not a known invoice status"
                % status))
        if not invoice["broker"]:
            findings.append(Finding(
                invoice_id, "missing-broker", "warning",
                "invoice has no broker name, so it cannot be "
                "counted in the per-broker summary"))
        if not invoice["broker_mc"]:
            findings.append(Finding(
                invoice_id, "missing-mc", "warning",
                "broker_mc is empty, so the broker cannot be "
                "checked on SAFER or Carrier411"))
        elif not MC_PATTERN.match(invoice["broker_mc"]):
            findings.append(Finding(
                invoice_id, "invalid-mc", "warning",
                "broker_mc %r is not a 6 or 7 digit MC number"
                % invoice["broker_mc"]))
        broker = invoice["broker"]
        mc_number = invoice["broker_mc"]
        if broker and mc_number \
                and first_mc.get(broker) is not None \
                and mc_number != first_mc[broker]:
            findings.append(Finding(
                invoice_id, "broker-mc-mismatch", "warning",
                "broker %s was first seen under MC %s but this "
                "invoice uses MC %s, so check for double "
                "brokering" % (broker, first_mc[broker], mc_number)))

        if delivered is not None and invoiced is not None \
                and invoiced < delivered:
            findings.append(Finding(
                invoice_id, "invoiced-before-delivered", "error",
                "invoiced_at %s is before delivered_at %s"
                % (invoiced.strftime("%Y-%m-%d"),
                   delivered.strftime("%Y-%m-%d"))))
        if paid is not None and invoiced is not None \
                and paid < invoiced:
            findings.append(Finding(
                invoice_id, "paid-before-invoiced", "error",
                "paid_at %s is before invoiced_at %s"
                % (paid.strftime("%Y-%m-%d"),
                   invoiced.strftime("%Y-%m-%d"))))
        if due is not None and invoiced is not None \
                and invoice["terms_days"] is not None:
            expected = invoiced + timedelta(
                days=invoice["terms_days"])
            if due.date() != expected.date():
                findings.append(Finding(
                    invoice_id, "terms-mismatch", "warning",
                    "due_at %s does not match invoiced_at plus "
                    "%d days (%s), so either the terms or the due "
                    "date is wrong"
                    % (due.strftime("%Y-%m-%d"),
                       invoice["terms_days"],
                       expected.strftime("%Y-%m-%d"))))

        if status == "unpaid" and due is not None:
            overdue_days = (as_of - due).days
            if overdue_days > 0:
                findings.append(Finding(
                    invoice_id, "overdue-invoice", "error",
                    "invoice is %d days past its due date %s and "
                    "still unpaid"
                    % (overdue_days, due.strftime("%Y-%m-%d"))))
                if overdue_days > writeoff_days:
                    findings.append(Finding(
                        invoice_id, "unpaid-over-90", "error",
                        "invoice is %d days past due, over the "
                        "limit of %d days, so it is bond claim "
                        "territory" % (overdue_days, writeoff_days)))
        if status == "paid" and paid is not None \
                and due is not None and paid <= as_of:
            late_days = (paid - due).days
            if late_days > 0:
                findings.append(Finding(
                    invoice_id, "paid-late", "warning",
                    "invoice was paid %d days after its due date "
                    "%s" % (late_days, due.strftime("%Y-%m-%d"))))

    per_broker = summarize_brokers(live, as_of)
    total_invoiced = sum((row["invoiced"] for row in per_broker),
                         Decimal("0"))
    for row in per_broker:
        if row["paid_count"] >= MIN_PAID_FOR_SLOW_PAY \
                and row["avg_dtp"] is not None \
                and row["avg_dtp"] > slow_days:
            findings.append(Finding(
                row["broker"], "slow-pay-broker", "warning",
                "broker averages %.1f days to pay over %d paid "
                "invoices, over the limit of %d days"
                % (row["avg_dtp"], row["paid_count"], slow_days)))
    if len(live) >= MIN_INVOICES_FOR_CONCENTRATION \
            and total_invoiced > 0:
        for row in per_broker:
            share = 100.0 * float(row["invoiced"]) \
                / float(total_invoiced)
            if share > max_share:
                findings.append(Finding(
                    row["broker"], "concentration-risk", "error",
                    "broker carries %.1f%% of all invoiced "
                    "revenue, over the limit of %d%%, so one "
                    "failure would hit the whole book"
                    % (share, max_share)))
    return findings


def summarize_brokers(invoices, as_of):
    """Return the per-broker summary rows for live invoices."""
    per_broker = {}
    for invoice in invoices:
        broker = invoice["broker"] or "(no broker)"
        row = per_broker.setdefault(broker, {
            "broker": broker, "invoices": 0,
            "invoiced": Decimal("0"), "paid": Decimal("0"),
            "paid_count": 0, "dtp_values": [], "overdue": 0})
        row["invoices"] += 1
        if invoice["amount"] is not None:
            row["invoiced"] += invoice["amount"]
            if invoice["status"] == "paid":
                row["paid"] += invoice["amount"]
        dtp = days_to_pay(invoice, as_of)
        if dtp is not None:
            row["paid_count"] += 1
            row["dtp_values"].append(dtp)
        if invoice["status"] == "unpaid" \
                and invoice["due_at"] is not None \
                and (as_of - invoice["due_at"]).days > 0:
            row["overdue"] += 1
    rows = []
    for row in sorted(per_broker.values(),
                      key=lambda item: item["broker"]):
        values = row.pop("dtp_values")
        row["avg_dtp"] = round(sum(values) / len(values), 1) \
            if values else None
        rows.append(row)
    return rows


def summarize(invoices, findings, as_of,
              slow_days=DEFAULT_SLOW_DAYS,
              max_share=DEFAULT_MAX_SHARE):
    """Return the summary counts for an invoice list and findings."""
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings
                   if item.severity == "warning")
    live = [invoice for invoice in invoices
            if invoice["status"] != "void"]
    void_count = len(invoices) - len(live)
    total_invoiced = sum(
        (invoice["amount"] for invoice in live
         if invoice["amount"] is not None), Decimal("0"))
    total_paid = sum(
        (invoice["amount"] for invoice in live
         if invoice["status"] == "paid"
         and invoice["amount"] is not None), Decimal("0"))
    overdue = [invoice for invoice in live
               if invoice["status"] == "unpaid"
               and invoice["due_at"] is not None
               and (as_of - invoice["due_at"]).days > 0]
    overdue_amount = sum(
        (invoice["amount"] for invoice in overdue
         if invoice["amount"] is not None), Decimal("0"))
    dtp_values = [days_to_pay(invoice, as_of) for invoice in live]
    dtp_values = [value for value in dtp_values if value is not None]
    on_time = 0
    for invoice in live:
        if days_to_pay(invoice, as_of) is not None \
                and invoice["due_at"] is not None \
                and invoice["paid_at"] <= invoice["due_at"]:
            on_time += 1
    per_broker = summarize_brokers(live, as_of)
    for row in per_broker:
        row["share"] = round(100.0 * float(row["invoiced"])
                             / float(total_invoiced), 1) \
            if total_invoiced > 0 else 0.0
    return {"invoices": len(invoices),
            "void": void_count,
            "findings": len(findings),
            "errors": errors,
            "warnings": warnings,
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "outstanding": total_invoiced - total_paid,
            "overdue": len(overdue),
            "overdue_amount": overdue_amount,
            "avg_dtp": round(sum(dtp_values) / len(dtp_values), 1)
            if dtp_values else None,
            "on_time_rate": round(100.0 * on_time / len(dtp_values), 1)
            if dtp_values else None,
            "per_broker": per_broker}


def render_text(invoices, findings, summary):
    """Render the text report."""
    lines = [str(item) for item in findings]
    lines.append("")
    lines.append("Invoices: %(invoices)d, findings: %(findings)d, "
                 "errors: %(errors)d, warnings: %(warnings)d"
                 % summary)
    if summary["void"]:
        lines.append("Void invoices: %d (excluded from totals)"
                     % summary["void"])
    lines.append("Total invoiced: %s, total paid: %s, outstanding: %s"
                 % (money(summary["total_invoiced"]),
                    money(summary["total_paid"]),
                    money(summary["outstanding"])))
    lines.append("Overdue: %(overdue)d invoices, %(overdue_amount)s"
                 % {"overdue": summary["overdue"],
                    "overdue_amount": money(summary["overdue_amount"])})
    if summary["avg_dtp"] is None:
        lines.append("Average days to pay: none")
    else:
        lines.append("Average days to pay: %.1f"
                     % summary["avg_dtp"])
    if summary["on_time_rate"] is None:
        lines.append("On-time payment rate: none")
    else:
        lines.append("On-time payment rate: %.1f%%"
                     % summary["on_time_rate"])
    lines.append("")
    lines.append("Per-broker summary:")
    lines.append("%-16s %8s %12s %12s %8s %8s %7s"
                 % ("broker", "invoices", "invoiced", "paid",
                    "avg dtp", "overdue", "share"))
    for row in summary["per_broker"]:
        avg = ("%.1f" % row["avg_dtp"]
               if row["avg_dtp"] is not None else "-")
        lines.append("%-16s %8d %12s %12s %8s %8d %6.1f%%"
                     % (row["broker"], row["invoices"],
                        money(row["invoiced"]), money(row["paid"]),
                        avg, row["overdue"], row["share"]))
    if summary["findings"] == 0:
        lines.append("")
        lines.append("All invoices clean.")
    return "\n".join(lines) + "\n"


def render_json(invoices, findings, summary):
    """Render the JSON report."""
    report = dict(summary)
    report["total_invoiced"] = str(summary["total_invoiced"])
    report["total_paid"] = str(summary["total_paid"])
    report["outstanding"] = str(summary["outstanding"])
    report["overdue_amount"] = str(summary["overdue_amount"])
    for row in report["per_broker"]:
        row["invoiced"] = str(row["invoiced"])
        row["paid"] = str(row["paid"])
    report["findings"] = [item.as_dict() for item in findings]
    return json.dumps(report, indent=2) + "\n"


def build_parser():
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="broker-credit-doctor",
        description="Audit a broker payment history CSV export "
                    "for slow pay, overdue invoices, and "
                    "concentration risk.")
    parser.add_argument("csv_file",
                        help="broker payment history CSV to audit")
    parser.add_argument("--slow-days", type=int,
                        default=DEFAULT_SLOW_DAYS,
                        help="a broker averaging more than this "
                        "many days to pay is a slow payer "
                        "(default: %d)" % DEFAULT_SLOW_DAYS)
    parser.add_argument("--max-share", type=int,
                        default=DEFAULT_MAX_SHARE,
                        help="one broker carrying more than this "
                        "percent of invoiced revenue is a "
                        "concentration risk (default: %d)"
                        % DEFAULT_MAX_SHARE)
    parser.add_argument("--writeoff-days", type=int,
                        default=DEFAULT_WRITEOFF_DAYS,
                        help="an unpaid invoice more than this many "
                        "days past due is bond claim territory "
                        "(default: %d)" % DEFAULT_WRITEOFF_DAYS)
    parser.add_argument("--as-of", default=None,
                        help="date to compare timestamps against, "
                        "for example 2026-11-18 (default: now)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any error is found")
    parser.add_argument("--strict", action="store_true",
                        help="with --check, also exit 1 on warnings")
    parser.add_argument("--out", default=None,
                        help="write the report to a file")
    return parser


def main(argv=None):
    """Run the CLI and return the exit code."""
    args = build_parser().parse_args(argv)
    if args.slow_days < 0:
        print("error: --slow-days must be at least 0",
              file=sys.stderr)
        return 2
    if args.max_share < 0 or args.max_share > 100:
        print("error: --max-share must be between 0 and 100",
              file=sys.stderr)
        return 2
    if args.writeoff_days < 0:
        print("error: --writeoff-days must be at least 0",
              file=sys.stderr)
        return 2
    if args.as_of is None:
        as_of = datetime.now(timezone.utc)
    else:
        as_of = parse_timestamp(args.as_of)
        if as_of is None:
            print("error: --as-of %r is not a readable date"
                  % args.as_of, file=sys.stderr)
            return 2
    try:
        invoices, findings = parse_csv(args.csv_file, as_of)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    findings.extend(audit(invoices, as_of,
                          slow_days=args.slow_days,
                          max_share=args.max_share,
                          writeoff_days=args.writeoff_days))
    summary = summarize(invoices, findings, as_of)
    if args.json:
        report = render_json(invoices, findings, summary)
    else:
        report = render_text(invoices, findings, summary)
    if args.out is not None:
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(report)
        except OSError as exc:
            print("error: cannot write %s: %s" % (args.out, exc),
                  file=sys.stderr)
            return 2
    else:
        sys.stdout.write(report)
    if args.check:
        if summary["errors"] > 0:
            return 1
        if args.strict and summary["warnings"] > 0:
            return 1
    return 0


def main_entry():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    main_entry()
