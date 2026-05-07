// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title RugBusterRegistry
/// @notice Public on-chain registry for Avalanche token safety scores.
/// @dev Scores are 0-100 where higher means safer. Labels are emitted for monitoring.
contract RugBusterRegistry {
    enum RiskLabel {
        UNKNOWN,
        DANGER,
        WARN,
        GOOD
    }

    struct SafetyReport {
        uint8 score;
        RiskLabel label;
        bytes32 metadataHash;
        address reviewer;
        uint64 updatedAt;
        bool exists;
    }

    address public owner;
    mapping(address => bool) public authorizedReviewers;
    mapping(address => SafetyReport) private reports;

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event ReviewerUpdated(address indexed reviewer, bool authorized);
    event ScoreUpdated(
        address indexed token,
        uint8 score,
        RiskLabel indexed label,
        bytes32 metadataHash,
        address indexed reviewer
    );
    event RugDetected(address indexed token, uint8 score, bytes32 metadataHash, address indexed reviewer);

    error NotOwner();
    error NotAuthorizedReviewer();
    error InvalidToken();
    error InvalidScore();
    error LengthMismatch();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyReviewer() {
        if (msg.sender != owner && !authorizedReviewers[msg.sender]) revert NotAuthorizedReviewer();
        _;
    }

    constructor() {
        owner = msg.sender;
        authorizedReviewers[msg.sender] = true;
        emit OwnershipTransferred(address(0), msg.sender);
        emit ReviewerUpdated(msg.sender, true);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidToken();
        address previousOwner = owner;
        owner = newOwner;
        authorizedReviewers[newOwner] = true;
        emit OwnershipTransferred(previousOwner, newOwner);
        emit ReviewerUpdated(newOwner, true);
    }

    function setReviewer(address reviewer, bool authorized) external onlyOwner {
        if (reviewer == address(0)) revert InvalidToken();
        authorizedReviewers[reviewer] = authorized;
        emit ReviewerUpdated(reviewer, authorized);
    }

    function updateScore(address token, uint8 score, bytes32 metadataHash) external onlyReviewer {
        _writeReport(token, score, _labelForScore(score), metadataHash);
    }

    function updateScoreWithLabel(
        address token,
        uint8 score,
        RiskLabel label,
        bytes32 metadataHash
    ) external onlyReviewer {
        _writeReport(token, score, label, metadataHash);
    }

    function batchUpdate(
        address[] calldata tokens,
        uint8[] calldata scores,
        bytes32[] calldata metadataHashes
    ) external onlyReviewer {
        uint256 length = tokens.length;
        if (length != scores.length || length != metadataHashes.length) revert LengthMismatch();

        for (uint256 i = 0; i < length; i++) {
            _writeReport(tokens[i], scores[i], _labelForScore(scores[i]), metadataHashes[i]);
        }
    }

    function batchUpdateWithLabels(
        address[] calldata tokens,
        uint8[] calldata scores,
        RiskLabel[] calldata labels,
        bytes32[] calldata metadataHashes
    ) external onlyReviewer {
        uint256 length = tokens.length;
        if (length != scores.length || length != labels.length || length != metadataHashes.length) {
            revert LengthMismatch();
        }

        for (uint256 i = 0; i < length; i++) {
            _writeReport(tokens[i], scores[i], labels[i], metadataHashes[i]);
        }
    }

    function getReport(address token) external view returns (SafetyReport memory) {
        return reports[token];
    }

    function getScore(address token) external view returns (uint8 score, RiskLabel label, uint64 updatedAt) {
        SafetyReport memory report = reports[token];
        return (report.score, report.label, report.updatedAt);
    }

    function _writeReport(address token, uint8 score, RiskLabel label, bytes32 metadataHash) internal {
        if (token == address(0)) revert InvalidToken();
        if (score > 100) revert InvalidScore();

        reports[token] = SafetyReport({
            score: score,
            label: label,
            metadataHash: metadataHash,
            reviewer: msg.sender,
            updatedAt: uint64(block.timestamp),
            exists: true
        });

        emit ScoreUpdated(token, score, label, metadataHash, msg.sender);

        if (label == RiskLabel.DANGER) {
            emit RugDetected(token, score, metadataHash, msg.sender);
        }
    }

    function _labelForScore(uint8 score) internal pure returns (RiskLabel) {
        if (score < 40) return RiskLabel.DANGER;
        if (score < 70) return RiskLabel.WARN;
        return RiskLabel.GOOD;
    }
}
