import json
import unittest
from unittest import mock

from app.api import server


def case(name):
    return json.loads((server.FIXTURES_DIR / f"{name}.json").read_text())


class DepositTest(unittest.TestCase):
    def test_not_approved_is_refused_before_network(self):
        for name in ("02_hold_noncommercial", "03_review_m4xx101"):
            with mock.patch.object(server.wa, "deposit") as sent:
                result = server.deposit(case(name), "0.001")
            sent.assert_not_called()
            self.assertEqual(result["status"], server.wa.REJECTED)
            self.assertEqual(result["error_code"], "NOT_APPROVED")

    def test_approved_sends_wei(self):
        with mock.patch.object(server.wa, "deposit", return_value={"ok": True}) as sent:
            server.deposit(case("03_escrow_unknown_recipient"), "0.001")
        self.assertEqual(sent.call_args.args[1], 10**15)
        self.assertTrue(sent.call_args.kwargs["send"])

    def test_bad_amount_is_not_sent(self):
        for amount in ("abc", "NaN", "Infinity", "0.0000000000000000001"):
            with mock.patch.object(server.wa, "deposit") as sent:
                result = server.deposit(case("03_escrow_unknown_recipient"), amount)
            sent.assert_not_called()
            self.assertEqual(result["error_code"], "INVALID_AMOUNT")

    def test_eth_format(self):
        self.assertEqual(server.eth(0), "0")
        self.assertEqual(server.eth(10 * 10**18), "10")
        self.assertEqual(server.eth(10**15), "0.001")


if __name__ == "__main__":
    unittest.main()
