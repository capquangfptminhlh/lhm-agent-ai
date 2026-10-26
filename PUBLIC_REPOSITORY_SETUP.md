# Publish this codebase as a public GitHub repository

Recommended target: `https://github.com/capquangfptminhlh/lhm-agent-ai`

1. Open https://github.com/new while signed into `capquangfptminhlh`.
2. Name: `lhm-agent-os`; visibility: **Public**.
3. Do **not** initialize with README, gitignore or license (the package contains them).
4. Create repository, then in a terminal **inside this source folder** run:

```bash
git init -b main
git add .
git commit -m "feat: initial open source LHM Agent OS Tool Hub V2"
git remote add origin https://github.com/capquangfptminhlh/lhm-agent-ai.git
git push -u origin main
```

GitHub will prompt for an appropriate authenticated method; do not send passwords or tokens in chat or check credentials into files. Alternatively, after the blank repository is created, ask ChatGPT to push via the connected GitHub integration.

Verify after pushing:

- README, LICENSE, SECURITY.md and CONTRIBUTING.md appear in root.
- Repo visibility is Public, all tests remain green.
- No `.env` or customer database is present in GitHub's file tree.
- Tool connectors remain unconfigured and external publishing is not claimed as verified until live account tests succeed.