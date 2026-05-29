// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";

/// @title RugBusterScanner
/// @notice Minimal Avalanche C-Chain scanner contract for token risk score requests and owner-submitted scores.
contract RugBusterScanner is Ownable {
    uint8 public constant DANGER = 0;
    uint8 public constant WARN = 1;
    uint8 public constant GOOD = 2;

    struct TokenScore {
        uint8 score;
        string label;
        address submitter;
        uint64 updatedAt;
        bool exists;
    }

    mapping(address => TokenScore) private scores;

    event ScanRequested(
        address indexed tokenAddress,
        address indexed requester,
        uint8 score,
        string label,
        bool exists
    );
    event ScoreSubmitted(
        address indexed tokenAddress,
        uint8 score,
        string label,
        address indexed submitter,
        uint64 updatedAt
    );

    error InvalidTokenAddress();
    error InvalidScore();

    constructor() Ownable(msg.sender) {}

    function scanToken(address tokenAddress) external {
        if (tokenAddress == address(0)) revert InvalidTokenAddress();

        TokenScore storage tokenScore = scores[tokenAddress];
        emit ScanRequested(
            tokenAddress,
            msg.sender,
            tokenScore.exists ? tokenScore.score : DANGER,
            tokenScore.exists ? tokenScore.label : "UNKNOWN",
            tokenScore.exists
        );
    }

    function submitScore(address tokenAddress, uint8 score, string calldata label) external onlyOwner {
        if (tokenAddress == address(0)) revert InvalidTokenAddress();
        if (score > GOOD) revert InvalidScore();

        uint64 updatedAt = uint64(block.timestamp);
        scores[tokenAddress] = TokenScore({
            score: score,
            label: label,
            submitter: msg.sender,
            updatedAt: updatedAt,
            exists: true
        });

        emit ScoreSubmitted(tokenAddress, score, label, msg.sender, updatedAt);
    }

    function getScore(
        address tokenAddress
    )
        external
        view
        returns (
            uint8 score,
            string memory label,
            address submitter,
            uint64 updatedAt,
            bool exists
        )
    {
        TokenScore storage tokenScore = scores[tokenAddress];
        return (
            tokenScore.score,
            tokenScore.label,
            tokenScore.submitter,
            tokenScore.updatedAt,
            tokenScore.exists
        );
    }
}
