#!/usr/bin/env node

const fs = require('fs');
const jwt = require('jsonwebtoken');

// Configuration from environment variables
const APP_ID = process.env.APP_ID;
const PRIVATE_KEY_PATH = process.env.PRIVATE_KEY_PATH || '/app/private-key.pem';
const INSTALLATION_ID = process.env.INSTALLATION_ID;
const GITHUB_API_URL = process.env.GITHUB_API_URL || 'https://api.github.com';

// Validation
if (!APP_ID) {
  console.error('ERROR: APP_ID environment variable is required');
  process.exit(1);
}

if (!INSTALLATION_ID) {
  console.error('ERROR: INSTALLATION_ID environment variable is required');
  process.exit(1);
}

if (!fs.existsSync(PRIVATE_KEY_PATH)) {
  console.error(`ERROR: Private key file not found at ${PRIVATE_KEY_PATH}`);
  process.exit(1);
}

async function generateInstallationToken() {
  try {
    // Dynamic import of ES module
    const { Octokit } = await import('@octokit/rest');
    
    // Read the private key
    const privateKey = fs.readFileSync(PRIVATE_KEY_PATH, 'utf8');
    
    // Create JWT for GitHub App authentication
    const now = Math.floor(Date.now() / 1000);
    const payload = {
      iat: now - 60, // Issued at time (60 seconds ago to account for clock drift)
      exp: now + (10 * 60), // Expiration time (10 minutes from now)
      iss: APP_ID // Issuer (GitHub App ID)
    };
    
    const token = jwt.sign(payload, privateKey, { algorithm: 'RS256' });
    
    // Create Octokit instance with GitHub App token
    const octokit = new Octokit({
      auth: token,
      baseUrl: GITHUB_API_URL
    });
    
    // Generate installation access token
    const { data } = await octokit.rest.apps.createInstallationAccessToken({
      installation_id: INSTALLATION_ID,
      // Optional: specify repositories if you want to limit scope
      // repositories: ['repo1', 'repo2'],
    });
    
    // Output only the token (this will be captured by the shell command)
    console.log(data.token);
    
  } catch (error) {
    console.error('ERROR: Failed to generate installation token:', error.message);
    
    // Provide more specific error messages
    if (error.status === 401) {
      console.error('Authentication failed. Check your APP_ID and private key.');
    } else if (error.status === 404) {
      console.error('Installation not found. Check your INSTALLATION_ID.');
    } else if (error.code === 'ENOENT') {
      console.error('Private key file not found.');
    }
    
    process.exit(1);
  }
}

// Run the token generation
generateInstallationToken();