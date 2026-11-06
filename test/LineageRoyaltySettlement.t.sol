// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {LineageRoyaltySettlement} from "../contracts/LineageRoyaltySettlement.sol";

// forge-std 없이 쓰는 최소 치트코드 인터페이스
interface Vm {
    function prank(address) external;
    function deal(address, uint256) external;
    function expectRevert(bytes calldata) external;
    function expectEmit(bool, bool, bool, bool) external;
    function assume(bool) external;
}

// 재진입 시도 컨트랙트: claim 중 받은 ETH로 다시 claim을 호출해 본다
contract ReentrantHolder {
    LineageRoyaltySettlement public target;
    bytes32 public modelHash;
    bool public blocked;
    bool public reentered;

    constructor(LineageRoyaltySettlement _target) {
        target = _target;
    }

    function attack(bytes32 _modelHash) external {
        modelHash = _modelHash;
        target.claim(_modelHash);
    }

    receive() external payable {
        try target.claim(modelHash) {
            reentered = true;
        } catch {
            blocked = true;
        }
    }
}

contract LineageRoyaltySettlementTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    event SettlementRuleRegistered(bytes32 indexed modelHash, bytes32 indexed evidenceHash, uint16 escrowBps);
    event EscrowReleased(bytes32 indexed modelHash, address indexed recipient, uint256 amount, bytes32 releaseEvidenceHash);

    LineageRoyaltySettlement c;

    address constant OPERATOR = address(uint160(0x1001));
    address constant STRANGER = address(uint160(0x1002));
    address constant A = address(uint160(0xA11CE));
    address constant B = address(uint160(0xB0B));
    address constant C = address(uint160(0xCAFE));

    bytes32 constant MODEL = keccak256("model");
    bytes32 constant TAMPERED = keccak256("tampered-model");
    bytes32 constant EVID = keccak256("evidence");
    bytes32 constant RELEASE_EVID = keccak256("release-evidence");

    function setUp() public {
        c = new LineageRoyaltySettlement(OPERATOR);
    }

    // ---------- 도우미 ----------
    function eq(uint256 a, uint256 b, string memory m) internal pure {
        require(a == b, m);
    }

    function _addrs2(address x, address y) internal pure returns (address[] memory r) {
        r = new address[](2);
        r[0] = x;
        r[1] = y;
    }

    function _addrs3(address x, address y, address z) internal pure returns (address[] memory r) {
        r = new address[](3);
        r[0] = x;
        r[1] = y;
        r[2] = z;
    }

    function _bps1(uint16 x) internal pure returns (uint16[] memory r) {
        r = new uint16[](1);
        r[0] = x;
    }

    function _bps2(uint16 x, uint16 y) internal pure returns (uint16[] memory r) {
        r = new uint16[](2);
        r[0] = x;
        r[1] = y;
    }

    function _bps3(uint16 x, uint16 y, uint16 z) internal pure returns (uint16[] memory r) {
        r = new uint16[](3);
        r[0] = x;
        r[1] = y;
        r[2] = z;
    }

    function _addrs1(address x) internal pure returns (address[] memory r) {
        r = new address[](1);
        r[0] = x;
    }

    function _deposit(bytes32 m, uint256 amount) internal {
        vm.deal(OPERATOR, amount);
        vm.prank(OPERATOR);
        c.deposit{value: amount}(m);
    }

    function _registerNormal() internal {
        c.registerSettlementRule(MODEL, _addrs2(A, B), _bps2(7000, 3000), 0, EVID);
    }

    function _registerEscrow() internal {
        // 시연 ③: A 40%, C 30%, B 몫 30%는 에스크로
        c.registerSettlementRule(MODEL, _addrs2(A, C), _bps2(4000, 3000), 3000, EVID);
    }

    // ---------- 생성자 ----------
    function test_constructor_sets_roles() public view {
        require(c.reviewer() == address(this), "reviewer");
        require(c.operator() == OPERATOR, "operator");
    }

    function test_constructor_rejects_zero_operator() public {
        vm.expectRevert(bytes("INVALID_OPERATOR"));
        new LineageRoyaltySettlement(address(0));
    }

    function test_constructor_rejects_same_reviewer_and_operator() public {
        vm.expectRevert(bytes("REVIEWER_AND_OPERATOR_MUST_DIFFER"));
        new LineageRoyaltySettlement(address(this));
    }

    // ---------- 등록 ----------
    function test_register_stores_rule_and_emits_event() public {
        vm.expectEmit(true, true, false, true);
        emit SettlementRuleRegistered(MODEL, EVID, 3000);
        _registerEscrow();

        (bool registered, uint16 escrowBps, uint256 escrowBalance, bytes32 evidenceHash, address[] memory r, uint16[] memory s) =
            c.getSettlementRule(MODEL);
        require(registered, "registered");
        eq(escrowBps, 3000, "escrowBps");
        eq(escrowBalance, 0, "escrowBalance");
        require(evidenceHash == EVID, "evidenceHash");
        eq(r.length, 2, "recipients length");
        eq(s[0], 4000, "share A");
        eq(s[1], 3000, "share C");
    }

    function test_register_rejects_duplicate_registration() public {
        _registerNormal();
        vm.expectRevert(bytes("RULE_ALREADY_REGISTERED"));
        c.registerSettlementRule(MODEL, _addrs2(A, B), _bps2(7000, 3000), 0, EVID);
    }

    function test_register_rejects_non_reviewer() public {
        vm.expectRevert(bytes("ONLY_REVIEWER"));
        vm.prank(STRANGER);
        c.registerSettlementRule(MODEL, _addrs2(A, B), _bps2(7000, 3000), 0, EVID);
    }

    function test_register_rejects_zero_hashes() public {
        vm.expectRevert(bytes("INVALID_MODEL_HASH"));
        c.registerSettlementRule(bytes32(0), _addrs2(A, B), _bps2(7000, 3000), 0, EVID);
        vm.expectRevert(bytes("INVALID_EVIDENCE_HASH"));
        c.registerSettlementRule(MODEL, _addrs2(A, B), _bps2(7000, 3000), 0, bytes32(0));
    }

    function test_register_rejects_bad_recipient_lists() public {
        vm.expectRevert(bytes("NO_RECIPIENTS"));
        c.registerSettlementRule(MODEL, new address[](0), new uint16[](0), 10000, EVID);

        vm.expectRevert(bytes("LENGTH_MISMATCH"));
        c.registerSettlementRule(MODEL, _addrs2(A, B), _bps1(10000), 0, EVID);

        vm.expectRevert(bytes("INVALID_RECIPIENT"));
        c.registerSettlementRule(MODEL, _addrs2(A, address(0)), _bps2(7000, 3000), 0, EVID);

        vm.expectRevert(bytes("INVALID_SHARE"));
        c.registerSettlementRule(MODEL, _addrs2(A, B), _bps2(10000, 0), 0, EVID);
    }

    // 같은 지갑 중복 등록 거부 (정책 엔진이 합산해서 넘기는 이유)
    function test_register_rejects_duplicate_recipient() public {
        vm.expectRevert(bytes("DUPLICATE_RECIPIENT"));
        c.registerSettlementRule(MODEL, _addrs2(A, A), _bps2(7000, 3000), 0, EVID);
    }

    function test_register_rejects_total_not_100_percent() public {
        vm.expectRevert(bytes("SHARES_MUST_EQUAL_100_PERCENT"));
        c.registerSettlementRule(MODEL, _addrs2(A, B), _bps2(6000, 3000), 0, EVID);
        vm.expectRevert(bytes("SHARES_MUST_EQUAL_100_PERCENT"));
        c.registerSettlementRule(MODEL, _addrs2(A, B), _bps2(7000, 3000), 500, EVID);
    }

    // ---------- 입금 ----------
    function test_deposit_only_operator() public {
        _registerNormal();
        vm.deal(STRANGER, 1 ether);
        vm.expectRevert(bytes("ONLY_OPERATOR"));
        vm.prank(STRANGER);
        c.deposit{value: 1 ether}(MODEL);
    }

    // 시연 ④: 변조된 모델 해시로는 입금이 거부됨
    function test_deposit_rejects_tampered_model_hash() public {
        _registerNormal();
        vm.deal(OPERATOR, 1 ether);
        vm.expectRevert(bytes("MODEL_HASH_NOT_REGISTERED"));
        vm.prank(OPERATOR);
        c.deposit{value: 1 ether}(TAMPERED);
    }

    function test_deposit_rejects_zero_value() public {
        _registerNormal();
        vm.expectRevert(bytes("ZERO_DEPOSIT"));
        vm.prank(OPERATOR);
        c.deposit{value: 0}(MODEL);
    }

    function test_direct_eth_transfer_is_rejected() public {
        vm.deal(address(this), 1 ether);
        (bool ok, ) = address(c).call{value: 1}("");
        require(!ok, "direct transfer must fail");
        eq(address(c).balance, 0, "contract balance");
    }

    // ---------- 시연 ①: 정상 정산 ----------
    function test_normal_flow_deposit_and_claim() public {
        _registerNormal();
        _deposit(MODEL, 1 ether);

        eq(c.claimable(MODEL, A), 0.7 ether, "claimable A");
        eq(c.claimable(MODEL, B), 0.3 ether, "claimable B");

        vm.prank(A);
        c.claim(MODEL);
        eq(A.balance, 0.7 ether, "A balance");
        eq(c.claimable(MODEL, A), 0, "A claimable after");

        vm.prank(B);
        c.claim(MODEL);
        eq(B.balance, 0.3 ether, "B balance");
        eq(address(c).balance, 0, "contract drained");
    }

    function test_claim_twice_is_rejected() public {
        _registerNormal();
        _deposit(MODEL, 1 ether);
        vm.prank(A);
        c.claim(MODEL);
        vm.expectRevert(bytes("NOTHING_TO_CLAIM"));
        vm.prank(A);
        c.claim(MODEL);
    }

    function test_claim_by_stranger_is_rejected() public {
        _registerNormal();
        _deposit(MODEL, 1 ether);
        vm.expectRevert(bytes("NOTHING_TO_CLAIM"));
        vm.prank(STRANGER);
        c.claim(MODEL);
    }

    // 나눗셈 잔여분(dust)은 마지막 수령자에게
    function test_dust_goes_to_last_recipient() public {
        c.registerSettlementRule(MODEL, _addrs3(A, B, C), _bps3(3333, 3333, 3334), 0, EVID);
        _deposit(MODEL, 101);
        eq(c.claimable(MODEL, A), 33, "A");
        eq(c.claimable(MODEL, B), 33, "B");
        eq(c.claimable(MODEL, C), 35, "C gets dust");
    }

    // ---------- 시연 ③: 에스크로 ----------
    function test_escrow_hold_and_release() public {
        _registerEscrow();
        _deposit(MODEL, 1 ether);

        eq(c.claimable(MODEL, A), 0.4 ether, "A");
        eq(c.claimable(MODEL, C), 0.3 ether, "C");
        (, , uint256 escrowBalance, , , ) = c.getSettlementRule(MODEL);
        eq(escrowBalance, 0.3 ether, "escrow held");

        // 해제 전에는 B가 가져갈 수 없음
        vm.expectRevert(bytes("NOTHING_TO_CLAIM"));
        vm.prank(B);
        c.claim(MODEL);

        vm.expectEmit(true, true, false, true);
        emit EscrowReleased(MODEL, B, 0.3 ether, RELEASE_EVID);
        c.releaseEscrow(MODEL, B, 0.3 ether, RELEASE_EVID);

        (, , escrowBalance, , , ) = c.getSettlementRule(MODEL);
        eq(escrowBalance, 0, "escrow emptied");
        eq(c.claimable(MODEL, B), 0.3 ether, "B claimable");

        vm.prank(B);
        c.claim(MODEL);
        eq(B.balance, 0.3 ether, "B balance");
    }

    function test_release_only_reviewer() public {
        _registerEscrow();
        _deposit(MODEL, 1 ether);
        vm.expectRevert(bytes("ONLY_REVIEWER"));
        vm.prank(STRANGER);
        c.releaseEscrow(MODEL, B, 0.3 ether, RELEASE_EVID);
    }

    function test_release_validations() public {
        _registerEscrow();
        _deposit(MODEL, 1 ether);

        vm.expectRevert(bytes("MODEL_HASH_NOT_REGISTERED"));
        c.releaseEscrow(TAMPERED, B, 0.3 ether, RELEASE_EVID);

        vm.expectRevert(bytes("INVALID_RECIPIENT"));
        c.releaseEscrow(MODEL, address(0), 0.3 ether, RELEASE_EVID);

        vm.expectRevert(bytes("INVALID_AMOUNT"));
        c.releaseEscrow(MODEL, B, 0, RELEASE_EVID);

        vm.expectRevert(bytes("INSUFFICIENT_ESCROW"));
        c.releaseEscrow(MODEL, B, 0.31 ether, RELEASE_EVID);

        vm.expectRevert(bytes("INVALID_RELEASE_EVIDENCE_HASH"));
        c.releaseEscrow(MODEL, B, 0.3 ether, bytes32(0));
    }

    // ---------- 재진입 방어 ----------
    function test_reentrancy_is_blocked() public {
        ReentrantHolder attacker = new ReentrantHolder(c);
        c.registerSettlementRule(MODEL, _addrs1(address(attacker)), _bps1(10000), 0, EVID);
        _deposit(MODEL, 1 ether);

        attacker.attack(MODEL);

        require(attacker.blocked(), "reentry should be blocked");
        require(!attacker.reentered(), "reentry must not succeed");
        eq(address(attacker).balance, 1 ether, "paid exactly once");
        eq(c.claimable(MODEL, address(attacker)), 0, "ledger cleared");
        eq(address(c).balance, 0, "contract drained");
    }

    // ---------- 퍼징: 어떤 금액을 넣어도 장부 합계가 입금액과 같다 ----------
    function testFuzz_deposit_accounting_matches_amount(uint96 amount) public {
        vm.assume(amount > 0);
        c.registerSettlementRule(MODEL, _addrs3(A, B, C), _bps3(4000, 2000, 1000), 3000, EVID);
        _deposit(MODEL, amount);

        (, , uint256 escrowBalance, , , ) = c.getSettlementRule(MODEL);
        uint256 total = c.claimable(MODEL, A) + c.claimable(MODEL, B) + c.claimable(MODEL, C) + escrowBalance;
        eq(total, amount, "ledger sum equals deposit");
        eq(address(c).balance, amount, "contract holds deposit");
    }
}
