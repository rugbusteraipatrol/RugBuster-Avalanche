const deployment = {
  network: "Avalanche Fuji Testnet",
  contractAddress: "0x5F30276B3A5079E088Ec3072884286de5a868355",
  reviewerAddress: "0x66065488Af8FbeB34705f966FA43b1BEb4015E83",
  deployGas: 898802,
  batchGas: 387350,
  deployTx: "0xd2fcf74b391cd583e0cf13c9460e91f828bdfaae99163efe0c7c1d6a3dd5b867",
  batchTx: "0x92b9c9bc818144f116adbb7dd85153a9164689f6ea9a5035c3eb46bc075d02cc",
  explorerBase: "https://testnet.snowtrace.io",
};

const tokenResults = [
  {
    symbol: "CCC",
    project: "Culture Catalyst Coin",
    score: 92,
    verdict: "GOOD",
    token: "0x1111111111111111111111111111111111111111",
  },
  {
    symbol: "AMI",
    project: "Avalanche Meme Index",
    score: 92,
    verdict: "GOOD",
    token: "0x2222222222222222222222222222222222222222",
  },
  {
    symbol: "CLAIM",
    project: "Claim Airdrop 100x",
    score: 37,
    verdict: "DANGER",
    token: "0x3333333333333333333333333333333333333333",
  },
  {
    symbol: "Unknown",
    project: "Unresolved Metadata",
    score: 27,
    verdict: "DANGER",
    token: "0x4444444444444444444444444444444444444444",
  },
  {
    symbol: "BST",
    project: "Builder Safety Token",
    score: 92,
    verdict: "GOOD",
    token: "0x5555555555555555555555555555555555555555",
  },
];

const proofLinks = [
  {
    title: "Deployed Contract",
    body: "Public RugBusterRegistry contract on Avalanche Fuji.",
    href: `${deployment.explorerBase}/address/${deployment.contractAddress}`,
  },
  {
    title: "Deploy Transaction",
    body: "Registry deployment with 898802 gas used.",
    href: `${deployment.explorerBase}/tx/${deployment.deployTx}`,
  },
  {
    title: "Batch Analysis Transaction",
    body: "Five token certifications published in one write.",
    href: `${deployment.explorerBase}/tx/${deployment.batchTx}`,
  },
];

const formatNumber = (value) => new Intl.NumberFormat("en-US").format(value);
const shortAddress = (value) => `${value.slice(0, 10)}...${value.slice(-8)}`;

document.getElementById("network-name").textContent = deployment.network;
document.getElementById("contract-address").textContent = deployment.contractAddress;
document.getElementById("reviewer-address").textContent = deployment.reviewerAddress;
document.getElementById("deploy-gas").textContent = formatNumber(deployment.deployGas);
document.getElementById("batch-gas").textContent = formatNumber(deployment.batchGas);
document.getElementById("token-count").textContent = tokenResults.length;
document.getElementById("danger-count").textContent = tokenResults.filter((item) => item.verdict === "DANGER").length;
document.getElementById("contract-link").href = `${deployment.explorerBase}/address/${deployment.contractAddress}`;
document.getElementById("batch-link").href = `${deployment.explorerBase}/tx/${deployment.batchTx}`;

document.getElementById("token-table").innerHTML = tokenResults
  .map(
    (item) => `
      <tr>
        <td>${item.symbol}</td>
        <td>${item.project}</td>
        <td class="score">${item.score}</td>
        <td><span class="pill ${item.verdict.toLowerCase()}">${item.verdict}</span></td>
        <td class="mono">${shortAddress(item.token)}</td>
      </tr>
    `,
  )
  .join("");

document.getElementById("proof-links").innerHTML = proofLinks
  .map(
    (item) => `
      <a class="proof-link" href="${item.href}" target="_blank" rel="noreferrer">
        <strong>${item.title}</strong>
        <span>${item.body}</span>
      </a>
    `,
  )
  .join("");
