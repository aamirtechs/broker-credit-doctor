"""Tests for broker-credit-doctor."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import broker_credit_doctor as bcd

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "examples")

AS_OF = datetime(2026, 11, 18, tzinfo=timezone.utc)

HEADER = ("id,broker,broker_mc,delivered_at,invoiced_at,amount,"
          "terms_days,due_at,paid_at,status\n")


def write_csv(content):
    """Write content to a temp CSV file and return its path."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8")
    handle.write(content)
    handle.close()
    return handle.name


def row(invoice_id="INV-1", broker="Acme Logistics", mc="123456",
        delivered="2026-10-01", invoiced="2026-10-02",
        amount="2500.00", terms="30", due="2026-11-01",
        paid="2026-10-28", status="paid"):
    """Build one CSV row with the standard columns."""
    return ",".join([invoice_id, broker, mc, delivered, invoiced,
                     amount, terms, due, paid, status]) + "\n"


def parse(content, as_of=AS_OF):
    """Parse CSV content and return (invoices, findings)."""
    return bcd.parse_csv(write_csv(content), as_of)


def audit(content, as_of=AS_OF, **kwargs):
    """Parse CSV content and audit it, returning all findings."""
    invoices, findings = parse(content, as_of)
    findings.extend(bcd.audit(invoices, as_of, **kwargs))
    return invoices, findings


def rules(findings):
    """Return the sorted rule names of a findings list."""
    return sorted(item.rule for item in findings)


def by_rule(findings, rule):
    """Return the findings with one rule name."""
    return [item for item in findings if item.rule == rule]


class TestParseAmount(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(bcd.parse_amount("2500.00"),
                         (Decimal("2500.00"), True))

    def test_dollar_and_commas(self):
        self.assertEqual(bcd.parse_amount("$2,500.00"),
                         (Decimal("2500.00"), True))

    def test_zero_is_valid(self):
        self.assertEqual(bcd.parse_amount("0"),
                         (Decimal("0"), True))

    def test_empty_is_invalid(self):
        self.assertEqual(bcd.parse_amount(""), (None, False))
        self.assertEqual(bcd.parse_amount(None), (None, False))

    def test_word_is_invalid(self):
        self.assertEqual(bcd.parse_amount("lots"), (None, False))

    def test_negative_is_invalid(self):
        self.assertEqual(bcd.parse_amount("-500.00"), (None, False))


class TestParseTerms(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(bcd.parse_terms("30"), (30, True))

    def test_empty_is_none(self):
        self.assertEqual(bcd.parse_terms(""), (None, True))
        self.assertEqual(bcd.parse_terms(None), (None, True))

    def test_word_is_invalid(self):
        self.assertEqual(bcd.parse_terms("net"), (None, False))

    def test_negative_is_invalid(self):
        self.assertEqual(bcd.parse_terms("-5"), (None, False))


class TestParseTimestamp(unittest.TestCase):
    def test_iso_with_space(self):
        parsed = bcd.parse_timestamp("2026-11-01 09:30")
        self.assertEqual(parsed,
                         datetime(2026, 11, 1, 9, 30,
                                  tzinfo=timezone.utc))

    def test_iso_with_z(self):
        parsed = bcd.parse_timestamp("2026-11-01T09:30:00Z")
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_garbage_returns_none(self):
        self.assertIsNone(bcd.parse_timestamp("not-a-date"))


class TestParseCsv(unittest.TestCase):
    def test_header_aliases(self):
        content = ("invoice_id,broker_name,mc_number,delivery_date,"
                   "invoice_date,total,payment_terms,due_date,"
                   "paid_date,state\n"
                   "INV-1,Acme Logistics,123456,2026-10-01,"
                   "2026-10-02,2500.00,30,2026-11-01,2026-10-28,"
                   "paid\n")
        invoices, findings = parse(content)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0]["broker"], "Acme Logistics")
        self.assertEqual(findings, [])

    def test_bom_header(self):
        content = "\ufeff" + HEADER + row()
        invoices, findings = parse(content)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(findings, [])

    def test_missing_id_column(self):
        content = "broker,invoiced_at,amount\nAcme,2026-10-02,100\n"
        with self.assertRaises(ValueError):
            parse(content)

    def test_missing_invoiced_column(self):
        content = "id,broker,amount\nINV-1,Acme,100\n"
        with self.assertRaises(ValueError):
            parse(content)

    def test_missing_amount_column(self):
        content = "id,broker,invoiced_at\nINV-1,Acme,2026-10-02\n"
        with self.assertRaises(ValueError):
            parse(content)

    def test_empty_file(self):
        with self.assertRaises(ValueError):
            parse("")

    def test_malformed_row_skipped(self):
        content = HEADER + row() + "INV-2,Acme,123456\n"
        invoices, findings = parse(content)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(rules(findings), ["malformed-row"])

    def test_duplicate_id(self):
        content = HEADER + row("INV-1") + row("INV-1")
        invoices, findings = parse(content)
        self.assertEqual(len(invoices), 2)
        self.assertEqual(rules(findings), ["duplicate-invoice-id"])

    def test_invalid_timestamp(self):
        content = HEADER + row(invoiced="not-a-date")
        invoices, findings = parse(content)
        self.assertEqual(rules(findings), ["invalid-timestamp"])
        self.assertIsNone(invoices[0]["invoiced_at"])

    def test_future_paid_date(self):
        content = HEADER + row(paid="2026-12-05")
        invoices, findings = parse(content)
        self.assertEqual(rules(findings), ["future-timestamp"])

    def test_future_due_date_is_not_an_error(self):
        content = HEADER + row(due="2026-12-05", paid="",
                               status="unpaid")
        invoices, findings = parse(content)
        self.assertEqual(findings, [])

    def test_due_computed_from_terms(self):
        content = HEADER + row(due="")
        invoices, findings = parse(content)
        self.assertEqual(invoices[0]["due_at"],
                         datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertEqual(findings, [])

    def test_missing_due_date(self):
        content = HEADER + row(terms="", due="")
        invoices, findings = parse(content)
        self.assertEqual(rules(findings), ["missing-due-date"])

    def test_invalid_amount(self):
        content = HEADER + row(amount="lots")
        invoices, findings = parse(content)
        self.assertEqual(rules(findings), ["invalid-amount"])
        self.assertIsNone(invoices[0]["amount"])

    def test_invalid_terms_is_warning(self):
        content = HEADER + row(terms="net")
        invoices, findings = parse(content)
        self.assertEqual(rules(findings), ["invalid-terms"])
        self.assertEqual(findings[0].severity, "warning")

    def test_blank_lines_skipped(self):
        content = HEADER + "\n" + row() + "\n"
        invoices, findings = parse(content)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(findings, [])

    def test_missing_file(self):
        with self.assertRaises(ValueError):
            bcd.parse_csv("/tmp/no-such-file-bcd.csv", AS_OF)


class TestDateRules(unittest.TestCase):
    def test_invoiced_before_delivered(self):
        content = HEADER + row(delivered="2026-10-03",
                               invoiced="2026-10-02")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["invoiced-before-delivered"])

    def test_paid_before_invoiced(self):
        content = HEADER + row(paid="2026-10-01")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["paid-before-invoiced"])

    def test_terms_mismatch(self):
        content = HEADER + row(due="2026-11-25")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["terms-mismatch"])

    def test_terms_match_is_clean(self):
        content = HEADER + row()
        invoices, findings = audit(content)
        self.assertEqual(findings, [])


class TestPaymentRules(unittest.TestCase):
    def test_overdue_invoice(self):
        content = HEADER + row(due="2026-10-02", paid="",
                               status="unpaid", terms="")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["overdue-invoice"])

    def test_not_due_yet_is_clean(self):
        content = HEADER + row(due="2026-11-25", paid="",
                               status="unpaid", terms="")
        invoices, findings = audit(content)
        self.assertEqual(findings, [])

    def test_unpaid_over_90(self):
        content = HEADER + row(due="2026-08-01", paid="",
                               status="unpaid", terms="")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["overdue-invoice", "unpaid-over-90"])

    def test_paid_late(self):
        content = HEADER + row(paid="2026-11-10")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["paid-late"])

    def test_paid_on_time_is_clean(self):
        content = HEADER + row()
        invoices, findings = audit(content)
        self.assertEqual(findings, [])

    def test_future_paid_date_has_no_paid_late(self):
        content = HEADER + row(paid="2026-12-05")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["future-timestamp"])

    def test_days_to_pay(self):
        invoices, _ = parse(HEADER + row())
        self.assertEqual(bcd.days_to_pay(invoices[0], AS_OF), 26)

    def test_days_to_pay_excludes_negative(self):
        invoices, _ = parse(HEADER + row(paid="2026-10-01"))
        self.assertIsNone(bcd.days_to_pay(invoices[0], AS_OF))


class TestBrokerRules(unittest.TestCase):
    def test_missing_mc(self):
        content = HEADER + row(mc="")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["missing-mc"])

    def test_invalid_mc(self):
        content = HEADER + row(mc="ABC12")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["invalid-mc"])

    def test_seven_digit_mc_is_valid(self):
        content = HEADER + row(mc="1234567")
        invoices, findings = audit(content)
        self.assertEqual(findings, [])

    def test_broker_mc_mismatch(self):
        content = HEADER + row("INV-1") + row("INV-2", mc="999999")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["broker-mc-mismatch"])

    def test_slow_pay_broker(self):
        content = HEADER + row("INV-1", delivered="2026-09-19",
                               invoiced="2026-09-20",
                               due="2026-10-20",
                               paid="2026-11-10") \
            + row("INV-2", delivered="2026-09-24",
                  invoiced="2026-09-25", due="2026-10-25",
                  paid="2026-11-12")
        invoices, findings = audit(content)
        self.assertIn("slow-pay-broker", rules(findings))

    def test_slow_pay_needs_two_paid(self):
        content = HEADER + row(paid="2026-11-17", due="2026-11-01")
        invoices, findings = audit(content)
        self.assertNotIn("slow-pay-broker", rules(findings))

    def test_concentration_risk(self):
        content = HEADER + row("INV-1") + row("INV-2") \
            + row("INV-3") + row("INV-4") + row("INV-5")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["concentration-risk"])

    def test_concentration_gated_under_five_invoices(self):
        content = HEADER + row("INV-1") + row("INV-2")
        invoices, findings = audit(content)
        self.assertEqual(findings, [])

    def test_void_invoice_is_skipped(self):
        content = HEADER + row("INV-1", status="void", due="",
                               paid="", terms="")
        invoices, findings = audit(content)
        self.assertEqual(findings, [])
        summary = bcd.summarize(invoices, findings, AS_OF)
        self.assertEqual(summary["void"], 1)
        self.assertEqual(summary["total_invoiced"], Decimal("0"))

    def test_invalid_status(self):
        content = HEADER + row(status="weird")
        invoices, findings = audit(content)
        self.assertEqual(rules(findings), ["invalid-status"])


class TestSummary(unittest.TestCase):
    def test_sample_counts(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        self.assertEqual(summary["invoices"], 18)
        self.assertEqual(summary["findings"], 23)
        self.assertEqual(summary["errors"], 15)
        self.assertEqual(summary["warnings"], 8)

    def test_sample_money_totals(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        self.assertEqual(summary["total_invoiced"],
                         Decimal("35350.00"))
        self.assertEqual(summary["total_paid"],
                         Decimal("20650.00"))
        self.assertEqual(summary["outstanding"],
                         Decimal("14700.00"))
        self.assertEqual(summary["overdue"], 4)
        self.assertEqual(summary["overdue_amount"],
                         Decimal("7300.00"))
        self.assertEqual(summary["avg_dtp"], 36.9)
        self.assertEqual(summary["on_time_rate"], 57.1)

    def test_per_broker_rows(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        by_broker = {item["broker"]: item
                     for item in summary["per_broker"]}
        self.assertEqual(by_broker["Acme Logistics"]["invoices"], 5)
        self.assertEqual(by_broker["Acme Logistics"]["invoiced"],
                         Decimal("11600.00"))
        self.assertEqual(by_broker["Acme Logistics"]["avg_dtp"], 29.0)
        self.assertEqual(by_broker["Acme Logistics"]["share"], 32.8)
        self.assertEqual(by_broker["Slowpay Brokers"]["avg_dtp"], 57.0)
        self.assertEqual(by_broker["Beta Freight"]["overdue"], 2)
        self.assertEqual(by_broker["Foxtrot Lines"]["invoiced"],
                         Decimal("0"))

    def test_clean_summary(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        self.assertEqual(summary["findings"], 0)
        self.assertEqual(summary["total_invoiced"],
                         Decimal("8000.00"))
        self.assertEqual(summary["avg_dtp"], 27.3)
        self.assertEqual(summary["on_time_rate"], 100.0)


class TestRender(unittest.TestCase):
    def test_text_contains_summary_lines(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        text = bcd.render_text(invoices, findings, summary)
        self.assertIn("Invoices: 18, findings: 23, errors: 15, "
                      "warnings: 8", text)
        self.assertIn("Total invoiced: $35,350.00, total paid: "
                      "$20,650.00, outstanding: $14,700.00", text)
        self.assertIn("Overdue: 4 invoices, $7,300.00", text)
        self.assertIn("Average days to pay: 36.9", text)
        self.assertIn("On-time payment rate: 57.1%", text)
        self.assertIn("Per-broker summary:", text)

    def test_text_names_oldest_overdue(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        text = bcd.render_text(invoices, findings, summary)
        self.assertIn("Oldest overdue: INV-104, Beta Freight, due "
                      "2026-08-01, 109 days overdue, $4,100.00\n",
                      text)
        self.assertEqual(text.count("Oldest overdue:"), 1)

    def test_clean_text_has_no_oldest_overdue(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        self.assertIsNone(summary["oldest_overdue"])
        self.assertNotIn("Oldest overdue",
                         bcd.render_text(invoices, findings, summary))

    def test_oldest_overdue_without_valid_amount(self):
        content = HEADER + row("INV-1", broker="", amount="lots",
                               due="2026-10-02", paid="",
                               status="unpaid", terms="") \
            + row("INV-2", due="2026-11-01", paid="",
                  status="unpaid", terms="")
        invoices, findings = audit(content)
        summary = bcd.summarize(invoices, findings, AS_OF)
        text = bcd.render_text(invoices, findings, summary)
        self.assertIn("Oldest overdue: INV-1, (no broker), due "
                      "2026-10-02, 47 days overdue, no valid amount",
                      text)

    def test_json_has_no_oldest_overdue(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        report = json.loads(bcd.render_json(invoices, findings,
                                            summary))
        self.assertNotIn("oldest_overdue", report)
        self.assertIn("oldest_overdue", summary)

    def test_json_is_valid(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        report = json.loads(bcd.render_json(invoices, findings,
                                            summary))
        self.assertEqual(report["invoices"], 18)
        self.assertEqual(len(report["findings"]), 23)
        self.assertEqual(report["total_invoiced"], "35350.00")

    def test_clean_text_says_clean(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        invoices, findings = bcd.parse_csv(path, AS_OF)
        findings.extend(bcd.audit(invoices, AS_OF))
        summary = bcd.summarize(invoices, findings, AS_OF)
        self.assertIn("All invoices clean.",
                      bcd.render_text(invoices, findings, summary))


class TestMain(unittest.TestCase):
    def run_main(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = bcd.main(argv)
        return code, buffer.getvalue()

    def test_sample_check_exits_1(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        code, _ = self.run_main([path, "--as-of", "2026-11-18",
                                 "--check"])
        self.assertEqual(code, 1)

    def test_clean_check_exits_0(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        code, _ = self.run_main([path, "--as-of", "2026-11-18",
                                 "--check"])
        self.assertEqual(code, 0)

    def test_warnings_only_passes_check_fails_strict(self):
        path = os.path.join(EXAMPLES, "warnings-only.csv")
        code, _ = self.run_main([path, "--as-of", "2026-11-18",
                                 "--check"])
        self.assertEqual(code, 0)
        code, _ = self.run_main([path, "--as-of", "2026-11-18",
                                 "--check", "--strict"])
        self.assertEqual(code, 1)

    def test_slow_days_flag_changes_output(self):
        path = os.path.join(EXAMPLES, "invoices.csv")
        code, output = self.run_main(
            [path, "--as-of", "2026-11-18", "--slow-days", "60",
             "--check"])
        self.assertEqual(code, 1)
        self.assertNotIn("slow-pay-broker", output)

    def test_json_flag(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        code, output = self.run_main(
            [path, "--as-of", "2026-11-18", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["invoices"], 4)

    def test_out_writes_file(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8")
        handle.close()
        code, _ = self.run_main(
            [path, "--as-of", "2026-11-18", "--out", handle.name])
        self.assertEqual(code, 0)
        with open(handle.name, encoding="utf-8") as read_handle:
            self.assertIn("All invoices clean.", read_handle.read())

    def test_bad_as_of_exits_2(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        code, _ = self.run_main([path, "--as-of", "not-a-date"])
        self.assertEqual(code, 2)

    def test_missing_file_exits_2(self):
        code, _ = self.run_main(["/tmp/no-such-file-bcd.csv"])
        self.assertEqual(code, 2)

    def test_bad_max_share_exits_2(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        code, _ = self.run_main([path, "--max-share", "101"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
