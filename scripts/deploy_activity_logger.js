const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  if (!deployer) {
    throw new Error("Missing deployer account. Set PRIVATE_KEY in .env before deploying.");
  }

  console.log(`Deploying RugBusterActivityLogger with: ${deployer.address}`);
  console.log(`Network: ${hre.network.name}`);

  const RugBusterActivityLogger = await hre.ethers.getContractFactory("RugBusterActivityLogger");
  const logger = await RugBusterActivityLogger.deploy();
  await logger.waitForDeployment();

  const address = await logger.getAddress();
  const deploymentTx = logger.deploymentTransaction();
  const receipt = await deploymentTx.wait();

  console.log("RugBusterActivityLogger deployed");
  console.log(`Contract: ${address}`);
  console.log(`Deploy tx: ${deploymentTx.hash}`);
  console.log(`Gas used: ${receipt.gasUsed.toString()}`);
  console.log("Add this to Railway/local env:");
  console.log(`ACTIVITY_LOGGER_ADDRESS=${address}`);

  const artifact = await hre.artifacts.readArtifact("RugBusterActivityLogger");
  const artifactDir = path.join(__dirname, "..", "artifacts");
  fs.mkdirSync(artifactDir, { recursive: true });
  fs.writeFileSync(
    path.join(artifactDir, "RugBusterActivityLogger.abi.json"),
    JSON.stringify(artifact.abi, null, 2)
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
