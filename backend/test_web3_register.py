"""web3_register 테스트. 실행: python3 -m unittest discover -s backend -v

- 기본 테스트는 네트워크·개인키 없이 가짜 Web3 객체로 검증한다.
- 실제 Sepolia 조회 테스트는 RUN_SEPOLIA_TESTS=1일 때만 돈다. 조회만 하고 트랜잭션은 보내지 않는다.
"""

import hashlib
import importlib.util
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

import web3_register as wr

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
ESCROW_FIXTURE = FIXTURES_DIR / "03_escrow_unknown_recipient.json"
APPROVED_FIXTURE = FIXTURES_DIR / "04_hash_mismatch.json"

CONTRACT_ADDRESS = "0x651610df7DCA42d1273e159DdDFf1F22679f9fFA"
REVIEWER = "0x9860b505569783A3f9b36a9cB1A3835745efE8f4"
OTHER = "0x40F57730A509fE02c451cABE1A916258c5dA2661"
HOLDER_A = "0x4D9352a9d5512BBc171107ED4CC4F67fb0261F18"
HOLDER_B = "0xcc5BDcAa07b00fc9a40E4629169E83106090672d"
HOLDER_C = "0x69F926Dd50FD6D48F2d89dCEB5d425FE861f99e3"

# 실제 값이 아닌 표식. 반환값에 섞여 나오지 않는지 확인하는 데 쓴다
FAKE_RPC = "https://rpc.invalid/secret-token-marker"
FAKE_KEY = "fake-key-marker"
FAKE_ENV = {"SEPOLIA_RPC_URL": FAKE_RPC, "CONTRACT_ADDRESS": CONTRACT_ADDRESS, "REVIEWER_PRIVATE_KEY": FAKE_KEY}
TX_HASH = "0x" + "ab" * 32

HAS_WEB3 = importlib.util.find_spec("web3") is not None


def sha256_hex(path):
    return "0x" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeCall:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def call(self):
        if self.error:
            raise self.error
        return self.value


class FakeRegisterFn:
    def __init__(self, contract, args):
        self.contract = contract
        self.args = args

    def build_transaction(self, tx):
        self.contract.built.append((self.args, tx))
        if self.contract.build_error:
            raise self.contract.build_error
        return dict(tx, gas=100000)


class FakeFunctions:
    def __init__(self, contract):
        self.contract = contract

    def getSettlementRule(self, model_hash):
        self.contract.queried.append(model_hash)
        return FakeCall(self.contract.rule, self.contract.query_error)

    def reviewer(self):
        return FakeCall(self.contract.reviewer)

    def registerSettlementRule(self, *args):
        return FakeRegisterFn(self.contract, args)


class FakeContract:
    def __init__(self, rule=None, reviewer=REVIEWER):
        self.rule = rule or (False, 0, 0, b"\x00" * 32, [], [])
        self.reviewer = reviewer
        self.query_error = None
        self.build_error = None
        self.built = []
        self.queried = []
        self.functions = FakeFunctions(self)


class FakeEth:
    def __init__(self, contract, chain_id=wr.SEPOLIA_CHAIN_ID):
        self._contract = contract
        self._chain_id = chain_id
        self.code = b"\x60\x80"
        self.sent = []
        self.receipt = {"status": 1}
        self.receipt_error = None
        self.send_error = None

    @property
    def chain_id(self):
        if isinstance(self._chain_id, Exception):
            raise self._chain_id
        return self._chain_id

    def get_code(self, address):
        return self.code

    def contract(self, address, abi):
        self._contract.address = address
        self._contract.abi = abi
        return self._contract

    def get_transaction_count(self, address):
        return 7

    def send_raw_transaction(self, raw):
        if self.send_error:
            raise self.send_error
        self.sent.append(raw)
        return bytes.fromhex(TX_HASH[2:])

    def wait_for_transaction_receipt(self, tx_hash, timeout):
        if self.receipt_error:
            raise self.receipt_error
        return self.receipt


class FakeW3:
    def __init__(self, contract=None, **eth_kwargs):
        self.contract = contract or FakeContract()
        self.eth = FakeEth(self.contract, **eth_kwargs)


class FakeSigned:
    raw_transaction = b"signed-tx"


class FakeAccount:
    address = REVIEWER

    def sign_transaction(self, tx):
        self.signed_tx = tx
        return FakeSigned()


def fail_if_called(*args, **kwargs):
    raise AssertionError("호출되면 안 되는 단계가 호출됨")


class Web3RegisterMockTest(unittest.TestCase):
    def setUp(self):
        # web3가 없어도 돌도록 체크섬 변환은 그대로 통과시킴(실제 변환은 RealWeb3OfflineTest에서 확인)
        patches = [
            mock.patch.object(wr, "_checksum", side_effect=lambda a: a),
            mock.patch.object(wr, "_load_account", return_value=FakeAccount()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def register(self, path=ESCROW_FIXTURE, w3=None, env=FAKE_ENV, **kwargs):
        result = wr.register_settlement_rule(path, w3=w3, env=env, **kwargs)
        self.assertEqual(set(result) >= {"ok", "status", "tx_hash", "etherscan_url", "contract_address",
                                         "error_code", "error_message"}, True)
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(FAKE_KEY, dumped)
        self.assertNotIn("secret-token-marker", dumped)
        self.assertNotIn("rpc.invalid", dumped)
        return result

    def test_hold_and_review_blocked_before_settings_and_network(self):
        cases = {"02_hold_noncommercial.json": "HOLD", "03_review_m4xx101.json": "REVIEW"}
        with mock.patch.object(wr, "_read_settings", side_effect=fail_if_called), \
                mock.patch.object(wr, "_connect", side_effect=fail_if_called), \
                mock.patch.object(wr, "_load_account", side_effect=fail_if_called):
            for name, status in cases.items():
                with self.subTest(fixture=name):
                    result = self.register(FIXTURES_DIR / name, env=None)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["error_code"], "BLOCKED_BY_POLICY")
                    self.assertIn(status, result["error_message"])
                    self.assertIsNone(result["tx_hash"])

    def test_missing_model_hash_blocked_before_network(self):
        # 01은 APPROVED지만 input.artifact_sha256이 null
        with mock.patch.object(wr, "_read_settings", side_effect=fail_if_called):
            result = self.register(FIXTURES_DIR / "01_normal_paper_qa.json", env=None)
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(result["error_code"], "MISSING_MODEL_HASH")

    def test_invalid_case_file(self):
        result = self.register(FIXTURES_DIR / "does-not-exist.json")
        self.assertEqual(result["error_code"], "INVALID_CASE")

    def test_success_returns_tx_and_sends_expected_args(self):
        w3 = FakeW3()
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "APPROVED_WITH_ESCROW")
        self.assertEqual(result["tx_hash"], TX_HASH)
        self.assertEqual(result["etherscan_url"], f"https://sepolia.etherscan.io/tx/{TX_HASH}")
        self.assertEqual(result["contract_address"], CONTRACT_ADDRESS)
        self.assertIsNone(result["error_code"])

        case = json.loads(ESCROW_FIXTURE.read_text(encoding="utf-8"))
        model_hash, recipients, shares, escrow, evidence = w3.contract.built[0][0]
        self.assertEqual(model_hash, bytes.fromhex(case["input"]["artifact_sha256"]))
        self.assertEqual(evidence, bytes.fromhex(sha256_hex(ESCROW_FIXTURE)[2:]))
        self.assertEqual(recipients, [HOLDER_A, HOLDER_C])
        self.assertEqual(shares, [4000, 3000])
        self.assertEqual(escrow, 3000)
        self.assertEqual(result["model_hash"], "0x" + case["input"]["artifact_sha256"])
        self.assertEqual(result["evidence_hash"], sha256_hex(ESCROW_FIXTURE))

        tx = w3.contract.built[0][1]
        self.assertEqual(tx["from"], REVIEWER)
        self.assertEqual(tx["chainId"], wr.SEPOLIA_CHAIN_ID)
        self.assertEqual(w3.eth.sent, [b"signed-tx"])

    def test_already_registered_same_evidence_not_sent(self):
        evidence = bytes.fromhex(sha256_hex(ESCROW_FIXTURE)[2:])
        w3 = FakeW3(FakeContract(rule=(True, 3000, 0, evidence, [HOLDER_A, HOLDER_C], [4000, 3000])))
        with mock.patch.object(wr, "_load_account", side_effect=fail_if_called):
            result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "ALREADY_REGISTERED")
        self.assertIn("같은 근거", result["error_message"])
        self.assertEqual(result["onchain_evidence_hash"], sha256_hex(ESCROW_FIXTURE))
        self.assertEqual(w3.contract.built, [])
        self.assertEqual(w3.eth.sent, [])

    def test_already_registered_with_older_evidence(self):
        old_evidence = bytes.fromhex("327dea11a87d7600b3305941b8b4fe20a2a974bf872ef8e162d19a5d0a930721")
        w3 = FakeW3(FakeContract(rule=(True, 3000, 0, old_evidence, [HOLDER_A, HOLDER_C], [4000, 3000])))
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "ALREADY_REGISTERED")
        self.assertIn("근거 파일이 바뀌었을 수 있습니다", result["error_message"])
        self.assertNotEqual(result["onchain_evidence_hash"], result["evidence_hash"])
        self.assertEqual(w3.eth.sent, [])

    def test_check_only_does_not_read_key(self):
        w3 = FakeW3()
        env = {k: v for k, v in FAKE_ENV.items() if k != "REVIEWER_PRIVATE_KEY"}
        with mock.patch.object(wr, "_load_account", side_effect=fail_if_called):
            result = self.register(ESCROW_FIXTURE, w3=w3, env=env, send=False)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["tx_hash"])
        self.assertEqual(w3.eth.sent, [])

    def test_revert_during_simulation_is_mapped(self):
        for code, message in wr.REVERT_MESSAGES.items():
            with self.subTest(code=code):
                w3 = FakeW3()
                w3.contract.build_error = Exception(f"('execution reverted: {code}', '0x08c379a0...')")
                result = self.register(ESCROW_FIXTURE, w3=w3)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["error_message"], message)
                self.assertEqual(w3.eth.sent, [])

    def test_revert_mapping_prefers_full_code(self):
        # INVALID_RECIPIENT와 INVALID_RELEASE_EVIDENCE_HASH처럼 겹치는 이름도 정확히 구분
        self.assertEqual(wr.revert_to_message("execution reverted: INVALID_RELEASE_EVIDENCE_HASH")[0],
                         "INVALID_RELEASE_EVIDENCE_HASH")
        self.assertEqual(wr.revert_to_message("execution reverted: INVALID_RECIPIENT")[0], "INVALID_RECIPIENT")
        self.assertEqual(wr.revert_to_message("something else")[0], "UNKNOWN_REVERT")

    def test_unknown_simulation_error_hides_raw_text(self):
        w3 = FakeW3()
        w3.contract.build_error = Exception(f"insufficient funds; url={FAKE_RPC}")
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "SIMULATION_FAILED")

    def test_wrong_reviewer_not_sent(self):
        w3 = FakeW3(FakeContract(reviewer=OTHER))
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "ONLY_REVIEWER")
        self.assertEqual(w3.contract.built, [])

    def test_wrong_network_not_sent(self):
        w3 = FakeW3(chain_id=1)
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "WRONG_NETWORK")
        self.assertEqual(w3.contract.queried, [])

    def test_network_error_hides_url(self):
        w3 = FakeW3(chain_id=ConnectionError(f"HTTPSConnectionPool failed for {FAKE_RPC}"))
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "NETWORK_ERROR")

    def test_contract_not_found(self):
        w3 = FakeW3()
        w3.eth.code = b""
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "CONTRACT_NOT_FOUND")

    def test_missing_config_names_variable_only(self):
        env = dict(FAKE_ENV, CONTRACT_ADDRESS="")
        result = self.register(ESCROW_FIXTURE, w3=FakeW3(), env=env)
        self.assertEqual(result["error_code"], "MISSING_CONFIG")
        self.assertIn("CONTRACT_ADDRESS", result["error_message"])

    def test_receipt_failed_and_timeout(self):
        w3 = FakeW3()
        w3.eth.receipt = {"status": 0}
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "TX_FAILED")
        self.assertEqual(result["tx_hash"], TX_HASH)

        w3 = FakeW3()
        w3.eth.receipt_error = TimeoutError("timeout")
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "TX_PENDING")
        self.assertEqual(result["tx_hash"], TX_HASH)
        self.assertTrue(result["etherscan_url"].endswith(TX_HASH))

    def test_send_revert_is_mapped(self):
        w3 = FakeW3()
        w3.eth.send_error = Exception("execution reverted: RULE_ALREADY_REGISTERED")
        result = self.register(ESCROW_FIXTURE, w3=w3)
        self.assertEqual(result["error_code"], "RULE_ALREADY_REGISTERED")


class AbiTest(unittest.TestCase):
    def test_abi_matches_contract_source(self):
        source = (ROOT / "contracts" / "LineageRoyaltySettlement.sol").read_text(encoding="utf-8")
        for entry in wr.load_abi():
            with self.subTest(name=entry["name"]):
                # public 상태 변수의 자동 getter
                getters = {
                    "reviewer": r"address public immutable reviewer;",
                    "operator": r"address public immutable operator;",
                    "claimable": r"mapping\(bytes32 => mapping\(address => uint256\)\) public claimable;",
                }
                if entry["name"] in getters:
                    self.assertRegex(source, getters[entry["name"]])
                    continue
                match = re.search(rf"function {entry['name']}\((.*?)\)", source, re.S)
                self.assertIsNotNone(match)
                params = [p.split()[0] for p in match.group(1).split(",") if p.strip()]
                params = [p.replace("calldata", "").strip() for p in params]
                self.assertEqual(params, [i["type"] for i in entry["inputs"]])

    def test_abi_matches_compiled_artifact_if_present(self):
        artifact = ROOT / "out" / "LineageRoyaltySettlement.sol" / "LineageRoyaltySettlement.json"
        if not artifact.exists():
            self.skipTest("Foundry 빌드 산출물(out/) 없음")
        compiled = {e["name"]: e for e in json.loads(artifact.read_text())["abi"] if e.get("type") == "function"}
        for entry in wr.load_abi():
            with self.subTest(name=entry["name"]):
                ref = compiled[entry["name"]]
                for key in ("inputs", "outputs"):
                    self.assertEqual([p["type"] for p in entry[key]], [p["type"] for p in ref[key]])
                self.assertEqual(entry["stateMutability"], ref["stateMutability"])


@unittest.skipUnless(HAS_WEB3, "web3 미설치")
class RealWeb3OfflineTest(unittest.TestCase):
    """실제 web3로 체크섬 변환과 ABI 인코딩을 확인한다(네트워크 없음)."""

    def test_checksum_and_encoding(self):
        from web3 import Web3

        for address in (HOLDER_A, HOLDER_B, HOLDER_C, CONTRACT_ADDRESS, REVIEWER):
            self.assertEqual(wr._checksum(address.lower()), address)

        w3 = Web3()
        contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=wr.load_abi())
        fn = contract.functions.registerSettlementRule(
            b"\x11" * 32, [HOLDER_A, HOLDER_C], [4000, 3000], 3000, b"\x22" * 32
        )
        data = fn._encode_transaction_data()
        selector = Web3.keccak(text="registerSettlementRule(bytes32,address[],uint16[],uint16,bytes32)")[:4]
        self.assertTrue(Web3.to_bytes(hexstr=data).startswith(selector))

    def test_full_flow_with_real_checksum(self):
        w3 = FakeW3()
        with mock.patch.object(wr, "_load_account", return_value=FakeAccount()):
            result = wr.register_settlement_rule(ESCROW_FIXTURE, w3=w3, env=FAKE_ENV)
        self.assertTrue(result["ok"], result)
        self.assertEqual(w3.contract.built[0][0][1], [HOLDER_A, HOLDER_C])


@unittest.skipUnless(os.environ.get("RUN_SEPOLIA_TESTS") == "1", "RUN_SEPOLIA_TESTS=1일 때만 실행")
class SepoliaLiveReadOnlyTest(unittest.TestCase):
    """실제 Sepolia 조회 테스트. .env의 SEPOLIA_RPC_URL, CONTRACT_ADDRESS를 쓰고 트랜잭션은 보내지 않는다."""

    def test_check_registration_status(self):
        for path in (ESCROW_FIXTURE, APPROVED_FIXTURE):
            with self.subTest(fixture=path.name):
                result = wr.register_settlement_rule(path, send=False)
                print(f"\n{path.name}\n{wr.format_register_result(result)}")
                self.assertIn(result["error_code"], (None, "ALREADY_REGISTERED"), result["error_message"])


if __name__ == "__main__":
    unittest.main()
