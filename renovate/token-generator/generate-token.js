#!/usr/bin/env node

const fs = require('fs');
const { createSign } = require('crypto');

const APP_ID = process.env.APP_ID;
const PRIVATE_KEY_PATH = process.env.PRIVATE_KEY_PATH || '/app/private-key.pem';
const INSTALLATION_ID = process.env.INSTALLATION_ID;
const GITHUB_API_URL = process.env.GITHUB_API_URL || 'https://api.github.com';

if (!APP_ID) { console.error('ERROR: APP_ID environment variable is required'); process.exit(1); }
if (!INSTALLATION_ID) { console.error('ERROR: INSTALLATION_ID environment variable is required'); process.exit(1); }

async function generateInstallationToken() {
  let privateKey;
  try {
    privateKey = fs.readFileSync(PRIVATE_KEY_PATH, 'utf8');
  } catch {
    console.error(`ERROR: Private key file not found at ${PRIVATE_KEY_PATH}`);
    process.exit(1);
  }

  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
  const payload = Buffer.from(JSON.stringify({ iat: now - 60, exp: now + 600, iss: APP_ID })).toString('base64url');
  const signingInput = `${header}.${payload}`;
  const sign = createSign('RSA-SHA256');
  sign.update(signingInput);
  const jwtToken = `${signingInput}.${sign.sign(privateKey, 'base64url')}`;

  const response = await fetch(
    `${GITHUB_API_URL}/app/installations/${INSTALLATION_ID}/access_tokens`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${jwtToken}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
    }
  );

  if (!response.ok) {
    const body = await response.text().catch(() => '');
    if (response.status === 401) console.error('ERROR: Authentication failed. Check APP_ID and private key.');
    else if (response.status === 404) console.error('ERROR: Installation not found. Check INSTALLATION_ID.');
    else console.error(`ERROR: GitHub API returned ${response.status}: ${body}`);
    process.exit(1);
  }

  const { token } = await response.json();
  console.log(token);
}

generateInstallationToken();
