const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

async function main() {
  await hre.run("compile");

  const artifact = await hre.artifacts.readArtifact("RugBusterScanner");
  const artifactDir = path.join(__dirname, "..", "artifacts");
  fs.mkdirSync(artifactDir, { recursive: true });

  const abiPath = path.join(artifactDir, "RugBusterScanner.abi.json");
  fs.writeFileSync(abiPath, JSON.stringify(artifact.abi, null, 2));
  console.log(`ABI written to ${abiPath}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
