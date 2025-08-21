# Docker Stacks Public Repository

This repository demonstrates a GitOps workflow for managing Docker stacks using Komodo.

## 🚀 GitOps Workflow with Komodo

We use Komodo to declaratively manage Docker stacks via GitOps. All stack configurations are stored as code in this repository. Komodo continuously synchronizes the desired state defined here with your Docker environment, ensuring consistency and easy rollbacks.

## ⚙️ Environment Variables

Environment variables are managed globally and per-stack:

- **Global env files:** Shared environment variables applicable to all stacks are defined in global env files.
- **Per-stack env files:** Each stack can have its own env files to override or extend global settings.

This separation allows flexible and maintainable configuration management.

## 🏠 Homepage Labels

Stacks are labeled with metadata to improve discoverability on the Komodo homepage. Labels include stack categories, technologies used, and status indicators.

## 🔄 Renovate Versioning

We use Renovate to automate dependency updates. Renovate is configured to handle versioning carefully, ensuring that updates are safe and compatible with existing stacks.

## 📂 Repository Structure and Principles

- 📝 **Declarative:** All stacks and configurations are defined declaratively.
- 📦 **Modular:** Stacks are organized into separate directories with clear boundaries.
- ♻️ **Reproducible:** The repository contains everything needed to reproduce the Docker environment.
- 🤖 **Automated:** CI/CD pipelines and tools like Komodo and Renovate automate deployment and maintenance.

This structure promotes collaboration, transparency, and reliability in managing Docker stacks.