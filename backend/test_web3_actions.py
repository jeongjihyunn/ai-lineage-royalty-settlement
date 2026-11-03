"""web3_actions 테스트 (네트워크·개인키 없이 가짜 Web3로 검증). 실행: python3 -m unittest discover -s backend -v"""

import json
import unittest
from unittest import mock

import web3_actions as wa
import web3_register as wr
from test_web3_register import (
    CONTRACT_ADDRESS, FAKE_KEY, FAKE_RPC, HAS_WEB3, HOLDER_A, HOLDER_B, HOLDER_C, OTHER, REVIEWER, TX_HASH,
    FakeAccount, FakeCall, FakeContract, FakeFunctions, FakeRegisterFn, FakeW3, fail_if_called,
)

OPERATOR = OTHER
# 04 fixture findings의 원본 해시(등록됨)와 1바이트를 덧붙인 변조본 해시(미등록)
ORIGINAL_HASH = "0x4ef4de62188b4b3718258d63a1a6d9ad90416aa2c578abd67fabe69a7111a352"
TAMPERED_HASH = "0xb1522f09eaf32b94e8263ed3b56d65d210a4eeeb550774554138c85ab9b8151a"
RELEASE_EVIDENCE = "0x" + "cd" * 32
REGISTERED_RULE = (True, 3000, 5000, b"\x01" * 32, [HOLDER_A, HOLDER_C], [4000, 3000])

FAKE_ENV = {
    "SEPOLIA_RPC_URL": FAKE_RPC,
    "CONTRACT_ADDRESS": CONTRACT_ADDRESS,
    "REVIEWER_PRIVATE_KEY": FAKE_KEY,
    "OPERATOR_PRIVATE_KEY": FAKE_KEY,
    "HOLDER_A_PRIVATE_KEY": FAKE_KEY,
    "HOLDER_B_PRIVATE_KEY": FAKE_KEY,
    "HOLDER_C_PRIVATE_KEY": FAKE_KEY,
}


class ActionFunctions(FakeFunctions):
    def operator(self):
        return FakeCall(self.contract.operator)

    def claimable(self, model_hash, address):
        return FakeCall(self.contract.claimable_amounts.get(address, 0))

    def deposit(self, model_hash):
        return FakeRegisterFn(self.contract, ("deposit", model_hash))

    def claim(self, model_hash):
        return FakeRegisterFn(self.contract, ("claim", model_hash))

    def releaseEscrow(self, *args):
        return FakeRegisterFn(self.contract, ("releaseEscrow",) + args)


class ActionContract(FakeContract):
    def __init__(self, rule=REGISTERED_RULE, operator=OPERATOR, claimable=None, **kwargs):
        super().__init__(rule=rule, **kwargs)
        self.operator = operator
        self.claimable_amounts = claimable or {}
        self.functions = ActionFunctions(self)


def account_at(address):
    account = FakeAccount()
    account.address = address
    return account


class ActionTestBase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(wr, "_checksum", side_effect=lambda a: a)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_account(self, address):
        patcher = mock.patch.object(wr, "_load_account", return_value=account_at(address))
        loader = patcher.start()
        self.addCleanup(patcher.stop)
        return loader

    def forbid(self, *names):
        for name in names:
            patcher = mock.patch.object(wr, name, side_effect=fail_if_called)
            patcher.start()
            self.addCleanup(patcher.stop)

    def check_result(self, result):
        for key in ("ok", "status", "tx_hash", "etherscan_url", "contract_address", "error_code", "error_message"):
            self.assertIn(key, result)
        dumped = json.dumps(result, ensure_ascii=False, default=str)
        self.assertNotIn(FAKE_KEY, dumped)
        self.assertNotIn("secret-token-marker", dumped)
        return result


class DepositTest(ActionTestBase):
    def deposit(self, model_hash=ORIGINAL_HASH, amount=10**15, **kwargs):
        kwargs.setdefault("env", FAKE_ENV)
        return self.check_result(wa.deposit(model_hash, amount, **kwargs))

    def test_tampered_hash_rejected_in_korean(self):
        # 시연 4: 변조 파일 해시로 입금 시도 -> 미등록 해시로 거부 (조회·전송 모드 모두 키를 읽기 전에 차단)
        self.forbid("_load_account")
        for send in (False, True):
            with self.subTest(send=send):
                w3 = FakeW3(ActionContract(rule=(False, 0, 0, b"\x00" * 32, [], [])))
                result = self.deposit(TAMPERED_HASH, w3=w3, send=send)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], wa.REJECTED)
                self.assertEqual(result["error_code"], "MODEL_HASH_NOT_REGISTERED")
                self.assertEqual(result["error_message"], wr.REVERT_MESSAGES["MODEL_HASH_NOT_REGISTERED"])
                self.assertIn("등록되지 않은 모델 해시", result["error_message"])
                self.assertEqual(w3.contract.queried, [bytes.fromhex(TAMPERED_HASH[2:])])
                self.assertEqual(w3.contract.built, [])
                self.assertEqual(w3.eth.sent, [])

    def test_contract_revert_not_registered_is_mapped(self):
        # 사전 조회를 지나도 컨트랙트가 revert하면 같은 한국어 문장으로 바뀜
        self.use_account(OPERATOR)
        w3 = FakeW3(ActionContract())
        w3.contract.build_error = Exception("('execution reverted: MODEL_HASH_NOT_REGISTERED', '0x08c379a0')")
        result = self.deposit(TAMPERED_HASH, w3=w3, send=True)
        self.assertEqual(result["status"], wa.REJECTED)
        self.assertEqual(result["error_code"], "MODEL_HASH_NOT_REGISTERED")
        self.assertEqual(result["error_message"], wr.REVERT_MESSAGES["MODEL_HASH_NOT_REGISTERED"])
        self.assertEqual(w3.eth.sent, [])

    def test_invalid_input_blocked_before_network(self):
        self.forbid("_read_settings", "_connect", "_load_account")
        cases = {
            "zero_amount": (ORIGINAL_HASH, 0, "ZERO_DEPOSIT"),
            "negative_amount": (ORIGINAL_HASH, -1, "ZERO_DEPOSIT"),
            "bool_amount": (ORIGINAL_HASH, True, "ZERO_DEPOSIT"),
            "bad_hash": ("0x1234", 1, "INVALID_MODEL_HASH"),
            "zero_hash": ("0x" + "0" * 64, 1, "INVALID_MODEL_HASH"),
        }
        for label, (model_hash, amount, code) in cases.items():
            with self.subTest(label=label):
                result = self.deposit(model_hash, amount, env=None, send=True)
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["status"], wa.REJECTED)

    def test_check_only_does_not_read_key(self):
        self.forbid("_load_account")
        w3 = FakeW3(ActionContract())
        env = {k: v for k, v in FAKE_ENV.items() if "PRIVATE_KEY" not in k}
        result = self.deposit(w3=w3, env=env)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["status"])
        self.assertIsNone(result["tx_hash"])
        self.assertEqual(result["details"]["escrow_bps"], 3000)
        self.assertEqual(w3.eth.sent, [])

    def test_send_success_uses_operator_and_value(self):
        loader = self.use_account(OPERATOR)
        w3 = FakeW3(ActionContract())
        result = self.deposit(amount=12345, w3=w3, send=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], wa.ACCEPTED)
        self.assertEqual(result["tx_hash"], TX_HASH)
        self.assertEqual(result["etherscan_url"], f"https://sepolia.etherscan.io/tx/{TX_HASH}")
        self.assertEqual(loader.call_args.args, (FAKE_KEY, "Operator"))
        args, tx = w3.contract.built[0]
        self.assertEqual(args, ("deposit", bytes.fromhex(ORIGINAL_HASH[2:])))
        self.assertEqual(tx["value"], 12345)
        self.assertEqual(tx["from"], OPERATOR)

    def test_wrong_operator_key_not_sent(self):
        self.use_account(REVIEWER)
        w3 = FakeW3(ActionContract())
        result = self.deposit(w3=w3, send=True)
        self.assertEqual(result["error_code"], "ONLY_OPERATOR")
        self.assertEqual(result["status"], wa.REJECTED)
        self.assertEqual(w3.contract.built, [])

    def test_missing_operator_key(self):
        env = {k: v for k, v in FAKE_ENV.items() if k != "OPERATOR_PRIVATE_KEY"}
        result = self.deposit(w3=FakeW3(ActionContract()), env=env, send=True)
        self.assertEqual(result["error_code"], "MISSING_CONFIG")
        self.assertIn("OPERATOR_PRIVATE_KEY", result["error_message"])

    def test_wrong_network(self):
        w3 = FakeW3(ActionContract(), chain_id=1)
        result = self.deposit(w3=w3)
        self.assertEqual(result["error_code"], "WRONG_NETWORK")
        self.assertIsNone(result["status"])

    def test_tx_failed_is_rejected(self):
        self.use_account(OPERATOR)
        w3 = FakeW3(ActionContract())
        w3.eth.receipt = {"status": 0}
        result = self.deposit(w3=w3, send=True)
        self.assertEqual(result["error_code"], "TX_FAILED")
        self.assertEqual(result["status"], wa.REJECTED)


class ClaimTest(ActionTestBase):
    def claim(self, holder="B", model_hash=ORIGINAL_HASH, **kwargs):
        kwargs.setdefault("env", FAKE_ENV)
        return self.check_result(wa.claim(model_hash, holder, **kwargs))

    def test_holder_addresses_from_wallet_map(self):
        self.assertEqual(wa.load_holder_addresses(), {"Holder_A": HOLDER_A, "Holder_B": HOLDER_B, "Holder_C": HOLDER_C})

    def test_invalid_holder_blocked_before_network(self):
        self.forbid("_read_settings", "_connect", "_load_account")
        for holder in ("D", "", None, "Holder_Z"):
            with self.subTest(holder=holder):
                self.assertEqual(self.claim(holder, env=None)["error_code"], "INVALID_HOLDER")

    def test_holder_spellings(self):
        for holder in ("B", "b", "holder_b", "Holder_B"):
            with self.subTest(holder=holder):
                result = self.claim(holder, w3=FakeW3(ActionContract(claimable={HOLDER_B: 7})))
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["details"]["holder"], "Holder_B")

    def test_check_only_reports_claimable(self):
        self.forbid("_load_account")
        w3 = FakeW3(ActionContract(claimable={HOLDER_B: 300}))
        result = self.claim(w3=w3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["details"]["holder_address"], HOLDER_B)
        self.assertEqual(result["details"]["claimable_wei"], 300)
        self.assertEqual(w3.eth.sent, [])

    def test_nothing_to_claim(self):
        result = self.claim(w3=FakeW3(ActionContract()), send=True)
        self.assertEqual(result["error_code"], "NOTHING_TO_CLAIM")
        self.assertEqual(result["error_message"], wr.REVERT_MESSAGES["NOTHING_TO_CLAIM"])

    def test_unregistered_hash(self):
        w3 = FakeW3(ActionContract(rule=(False, 0, 0, b"\x00" * 32, [], [])))
        self.assertEqual(self.claim(model_hash=TAMPERED_HASH, w3=w3)["error_code"], "MODEL_HASH_NOT_REGISTERED")

    def test_send_uses_matching_holder_key(self):
        loader = self.use_account(HOLDER_B)
        w3 = FakeW3(ActionContract(claimable={HOLDER_B: 300}))
        env = {k: v for k, v in FAKE_ENV.items() if k not in ("HOLDER_A_PRIVATE_KEY", "HOLDER_C_PRIVATE_KEY")}
        result = self.claim(w3=w3, env=env, send=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(loader.call_args.args, (FAKE_KEY, "Holder"))
        self.assertEqual(w3.contract.built[0][0], ("claim", bytes.fromhex(ORIGINAL_HASH[2:])))
        self.assertEqual(w3.contract.built[0][1]["from"], HOLDER_B)

    def test_missing_holder_key(self):
        env = {k: v for k, v in FAKE_ENV.items() if k != "HOLDER_C_PRIVATE_KEY"}
        result = self.claim("C", w3=FakeW3(ActionContract(claimable={HOLDER_C: 1})), env=env, send=True)
        self.assertEqual(result["error_code"], "MISSING_CONFIG")
        self.assertIn("HOLDER_C_PRIVATE_KEY", result["error_message"])

    def test_key_of_other_holder_not_sent(self):
        self.use_account(HOLDER_A)
        w3 = FakeW3(ActionContract(claimable={HOLDER_B: 300}))
        result = self.claim("B", w3=w3, send=True)
        self.assertEqual(result["error_code"], "HOLDER_KEY_MISMATCH")
        self.assertEqual(w3.contract.built, [])


class ReleaseEscrowTest(ActionTestBase):
    def release(self, recipient=HOLDER_B, amount=1000, evidence=RELEASE_EVIDENCE, **kwargs):
        kwargs.setdefault("env", FAKE_ENV)
        return self.check_result(wa.release_escrow(ORIGINAL_HASH, recipient, amount, evidence, **kwargs))

    def test_invalid_input_blocked_before_network(self):
        self.forbid("_read_settings", "_connect", "_load_account")
        with mock.patch.object(wr, "_checksum", side_effect=lambda a: a if wr.pe.ADDRESS_PATTERN.match(a) else int("x")):
            cases = {
                "zero_evidence": ({"evidence": "0x" + "0" * 64}, "INVALID_RELEASE_EVIDENCE_HASH"),
                "bad_evidence": ({"evidence": "abc"}, "INVALID_RELEASE_EVIDENCE_HASH"),
                "zero_amount": ({"amount": 0}, "INVALID_AMOUNT"),
                "zero_recipient": ({"recipient": "0x" + "0" * 40}, "INVALID_RECIPIENT"),
                "bad_recipient": ({"recipient": "0x1234"}, "INVALID_RECIPIENT"),
            }
            for label, (kwargs, code) in cases.items():
                with self.subTest(label=label):
                    self.assertEqual(self.release(env=None, send=True, **kwargs)["error_code"], code)

    def test_insufficient_escrow(self):
        result = self.release(amount=5001, w3=FakeW3(ActionContract()), send=True)
        self.assertEqual(result["error_code"], "INSUFFICIENT_ESCROW")
        self.assertIn("5000", result["error_message"])

    def test_check_only(self):
        self.forbid("_load_account")
        result = self.release(w3=FakeW3(ActionContract()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["details"]["escrow_balance_wei"], 5000)

    def test_send_success(self):
        loader = self.use_account(REVIEWER)
        w3 = FakeW3(ActionContract())
        result = self.release(w3=w3, send=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(loader.call_args.args, (FAKE_KEY, "Reviewer"))
        self.assertEqual(w3.contract.built[0][0], (
            "releaseEscrow", bytes.fromhex(ORIGINAL_HASH[2:]), HOLDER_B, 1000, bytes.fromhex(RELEASE_EVIDENCE[2:]),
        ))
        self.assertNotIn("value", w3.contract.built[0][1])

    def test_non_reviewer_not_sent(self):
        self.use_account(OPERATOR)
        w3 = FakeW3(ActionContract())
        result = self.release(w3=w3, send=True)
        self.assertEqual(result["error_code"], "ONLY_REVIEWER")
        self.assertEqual(w3.contract.built, [])


@unittest.skipUnless(HAS_WEB3, "web3 미설치")
class RealWeb3EncodingTest(unittest.TestCase):
    """실제 web3로 세 함수의 ABI 인코딩(함수 선택자)을 확인한다(네트워크 없음)."""

    def test_selectors(self):
        from web3 import Web3

        contract = Web3().eth.contract(address=CONTRACT_ADDRESS, abi=wr.load_abi())
        model = bytes.fromhex(ORIGINAL_HASH[2:])
        calls = {
            "deposit(bytes32)": contract.functions.deposit(model),
            "claim(bytes32)": contract.functions.claim(model),
            "releaseEscrow(bytes32,address,uint256,bytes32)": contract.functions.releaseEscrow(
                model, HOLDER_B, 1000, bytes.fromhex(RELEASE_EVIDENCE[2:])
            ),
        }
        for signature, fn in calls.items():
            with self.subTest(signature=signature):
                selector = Web3.keccak(text=signature)[:4]
                self.assertTrue(Web3.to_bytes(hexstr=fn._encode_transaction_data()).startswith(selector))


if __name__ == "__main__":
    unittest.main()
