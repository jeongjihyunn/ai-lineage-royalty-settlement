// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title LineageRoyaltySettlement
 * @notice AI 모델 계보 기반 사용료 정산 시연용 컨트랙트
 *
 * Reviewer: 정산 규칙 등록, 에스크로 해제
 * Operator: 등록된 모델에 테스트 ETH 입금
 * Holder: 본인 몫 claim
 */
contract LineageRoyaltySettlement {
    uint16 public constant BPS_DENOMINATOR = 10_000;

    address public immutable reviewer;
    address public immutable operator;

    uint256 private unlocked = 1;

    struct SettlementRule {
        bool registered;
        uint16 escrowBps;
        uint256 escrowBalance;
        bytes32 evidenceHash;
        address[] recipients;
        uint16[] sharesBps;
    }

    mapping(bytes32 => SettlementRule) private rules;
    mapping(bytes32 => mapping(address => uint256)) public claimable;

    event SettlementRuleRegistered(
        bytes32 indexed modelHash,
        bytes32 indexed evidenceHash,
        uint16 escrowBps
    );

    event Deposited(
        bytes32 indexed modelHash,
        address indexed operator,
        uint256 amount,
        uint256 escrowAmount
    );

    event Claimed(
        bytes32 indexed modelHash,
        address indexed recipient,
        uint256 amount
    );

    event EscrowReleased(
        bytes32 indexed modelHash,
        address indexed recipient,
        uint256 amount,
        bytes32 releaseEvidenceHash
    );

    modifier onlyReviewer() {
        require(msg.sender == reviewer, "ONLY_REVIEWER");
        _;
    }

    modifier onlyOperator() {
        require(msg.sender == operator, "ONLY_OPERATOR");
        _;
    }

    modifier nonReentrant() {
        require(unlocked == 1, "REENTRANCY_BLOCKED");
        unlocked = 2;
        _;
        unlocked = 1;
    }

    constructor(address _operator) {
        require(_operator != address(0), "INVALID_OPERATOR");
        require(
            _operator != msg.sender,
            "REVIEWER_AND_OPERATOR_MUST_DIFFER"
        );

        reviewer = msg.sender;
        operator = _operator;
    }

    /**
     * @notice Reviewer가 모델 해시별 정산 규칙을 한 번 등록한다.
     * @param modelHash SHA-256 모델 파일 해시
     * @param recipients 수령 지갑 주소 목록
     * @param sharesBps 각 수령자의 비율 (basis point)
     * @param escrowBps 수령 지갑 미확정 몫의 비율
     * @param evidenceHash 오프체인 근거 JSON의 해시
     */
    function registerSettlementRule(
        bytes32 modelHash,
        address[] calldata recipients,
        uint16[] calldata sharesBps,
        uint16 escrowBps,
        bytes32 evidenceHash
    ) external onlyReviewer {
        require(!rules[modelHash].registered, "RULE_ALREADY_REGISTERED");
        require(modelHash != bytes32(0), "INVALID_MODEL_HASH");
        require(evidenceHash != bytes32(0), "INVALID_EVIDENCE_HASH");
        require(recipients.length > 0, "NO_RECIPIENTS");
        require(recipients.length == sharesBps.length, "LENGTH_MISMATCH");

        uint256 totalBps = escrowBps;

        for (uint256 i = 0; i < recipients.length; i++) {
            require(recipients[i] != address(0), "INVALID_RECIPIENT");
            require(sharesBps[i] > 0, "INVALID_SHARE");

            // 같은 지갑을 두 번 등록하는 것을 방지한다.
            for (uint256 j = 0; j < i; j++) {
                require(
                    recipients[i] != recipients[j],
                    "DUPLICATE_RECIPIENT"
                );
            }

            totalBps += sharesBps[i];
            rules[modelHash].recipients.push(recipients[i]);
            rules[modelHash].sharesBps.push(sharesBps[i]);
        }

        require(
            totalBps == BPS_DENOMINATOR,
            "SHARES_MUST_EQUAL_100_PERCENT"
        );

        rules[modelHash].registered = true;
        rules[modelHash].escrowBps = escrowBps;
        rules[modelHash].evidenceHash = evidenceHash;

        emit SettlementRuleRegistered(modelHash, evidenceHash, escrowBps);
    }

    /**
     * @notice Operator가 등록된 모델 해시에 테스트 ETH를 입금한다.
     * 등록되지 않은 모델 해시 또는 변조본 해시는 거부된다.
     */
    function deposit(bytes32 modelHash) external payable onlyOperator {
        SettlementRule storage rule = rules[modelHash];

        require(rule.registered, "MODEL_HASH_NOT_REGISTERED");
        require(msg.value > 0, "ZERO_DEPOSIT");

        uint256 escrowAmount =
            (msg.value * rule.escrowBps) / BPS_DENOMINATOR;

        uint256 allocatedAmount = 0;
        uint256 lastIndex = rule.recipients.length - 1;

        // 마지막 수령자 전까지의 몫을 장부에 기록한다.
        for (uint256 i = 0; i < lastIndex; i++) {
            uint256 amount =
                (msg.value * rule.sharesBps[i]) / BPS_DENOMINATOR;

            claimable[modelHash][rule.recipients[i]] += amount;
            allocatedAmount += amount;
        }

        // 나눗셈으로 생기는 1 wei 단위 잔여분은 마지막 수령자에게 준다.
        uint256 lastRecipientAmount =
            msg.value - escrowAmount - allocatedAmount;

        claimable[modelHash][rule.recipients[lastIndex]] +=
            lastRecipientAmount;

        rule.escrowBalance += escrowAmount;

        emit Deposited(modelHash, msg.sender, msg.value, escrowAmount);
    }

    /**
     * @notice 권리자가 본인에게 기록된 몫만 출금한다.
     */
    function claim(bytes32 modelHash) external nonReentrant {
        uint256 amount = claimable[modelHash][msg.sender];

        require(amount > 0, "NOTHING_TO_CLAIM");

        // 송금 전에 장부를 먼저 차감해 재진입을 방어한다.
        claimable[modelHash][msg.sender] = 0;

        (bool success, ) = payable(msg.sender).call{value: amount}("");
        require(success, "TRANSFER_FAILED");

        emit Claimed(modelHash, msg.sender, amount);
    }

    /**
     * @notice Reviewer가 확인된 수령자에게 에스크로 몫을 claim 가능 상태로 전환한다.
     * @param releaseEvidenceHash 수령자 확인 근거의 해시
     */
    function releaseEscrow(
        bytes32 modelHash,
        address recipient,
        uint256 amount,
        bytes32 releaseEvidenceHash
    ) external onlyReviewer {
        SettlementRule storage rule = rules[modelHash];

        require(rule.registered, "MODEL_HASH_NOT_REGISTERED");
        require(recipient != address(0), "INVALID_RECIPIENT");
        require(amount > 0, "INVALID_AMOUNT");
        require(amount <= rule.escrowBalance, "INSUFFICIENT_ESCROW");
        require(
            releaseEvidenceHash != bytes32(0),
            "INVALID_RELEASE_EVIDENCE_HASH"
        );

        rule.escrowBalance -= amount;
        claimable[modelHash][recipient] += amount;

        emit EscrowReleased(
            modelHash,
            recipient,
            amount,
            releaseEvidenceHash
        );
    }

    function getSettlementRule(
        bytes32 modelHash
    )
        external
        view
        returns (
            bool registered,
            uint16 escrowBps,
            uint256 escrowBalance,
            bytes32 evidenceHash,
            address[] memory recipients,
            uint16[] memory sharesBps
        )
    {
        SettlementRule storage rule = rules[modelHash];

        return (
            rule.registered,
            rule.escrowBps,
            rule.escrowBalance,
            rule.evidenceHash,
            rule.recipients,
            rule.sharesBps
        );
    }

    // deposit() 이외의 직접 ETH 전송은 막는다.
    receive() external payable {
        revert("USE_DEPOSIT");
    }
}