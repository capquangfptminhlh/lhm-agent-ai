# Security policy

This repository is public. **Never commit API keys, OAuth tokens, personal lead data, customer lists, database snapshots, or real `.env` files.** Use `.env.example` only as a configuration template.

The local MVP listens on `127.0.0.1` and is **not security-hardened for direct public internet access**. Before SaaS deployment, add authentication and role-based permissions for every API, tenant isolation, encrypted secrets storage, rate limits, CSRF protection where applicable, audit logs, backups, and HTTPS.

- Use provider-scoped API credentials with least privilege.
- Rotate any leaked keys immediately.
- Review third-party repo licenses before bundling; the upstream Hermes3D repository is linked but not bundled in this source tree.
- Do not enable automated messaging or publishing without consent, service permissions, approval rules, and evidence of success.

If you find a vulnerability, do not post credentials or exploit steps in a public issue. Contact the maintainers privately through GitHub's security advisory/reporting mechanisms where available.