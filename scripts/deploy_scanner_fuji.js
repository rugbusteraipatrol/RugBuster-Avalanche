const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  if (!deployer) {
    throw new Error("Missing deployer account. Set PRIVATE_KEY in .env before deploying.");
  }

  console.log(`Deploying RugBusterScanner with: ${deployer.address}`);
  console.log(`Network: ${hre.network.name}`);

  const RugBusterScanner = await hre.ethers.getContractFactory("RugBusterScanner");
  const scanner = await RugBusterScanner.deploy();
  await scanner.waitForDeployment();

  const address = await scanner.getAddress();
  const deploymentTx = scanner.deploymentTransaction();
  const receipt = await deploymentTx.wait();

  console.log("RugBusterScanner deployed");
  console.log(`Contract: ${address}`);
  console.log(`Deploy tx: ${deploymentTx.hash}`);
  console.log(`Gas used: ${receipt.gasUsed.toString()}`);

  const artifact = await hre.artifacts.readArtifact("RugBusterScanner");
  const artifactDir = path.join(__dirname, "..", "artifacts");
  fs.mkdirSync(artifactDir, { recursive: true });
  fs.writeFileSync(
    path.join(artifactDir, "RugBusterScanner.abi.json"),
    JSON.stringify(artifact.abi, null, 2)
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
