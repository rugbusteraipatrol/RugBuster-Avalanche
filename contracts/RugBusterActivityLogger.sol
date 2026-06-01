// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title RugBusterActivityLogger
/// @notice Public activity logger for RugBuster AVAX scan modules.
/// @dev Anyone can log module output; each call emits indexed data for Retro9000-visible app activity.
contract RugBusterActivityLogger {
    event ModuleLogged(
        address indexed user,
        address indexed token,
        string module,
        string verdict,
        uint8 score,
        bytes32 payloadHash,
        uint64 timestamp
    );

    error InvalidToken();
    error InvalidScore();

    function logModule(
        address token,
        string calldata module,
        string calldata verdict,
        uint8 score,
        bytes32 payloadHash
    ) external {
        if (token == address(0)) revert InvalidToken();
        if (score > 100) revert InvalidScore();

        emit ModuleLogged(
            msg.sender,
            token,
            module,
            verdict,
            score,
            payloadHash,
            uint64(block.timestamp)
        );
    }
}
