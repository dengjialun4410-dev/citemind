# Security Policy

## Supported version

The latest `main` branch is supported during active development.

## Reporting a vulnerability

Do not open a public issue for authentication bypasses, secret exposure, arbitrary file access, injection vulnerabilities, or other security-sensitive findings.

Contact the repository owner privately and include:

- affected version or commit;
- reproduction steps;
- expected and actual behavior;
- potential impact;
- a suggested fix, if available.

Do not include real API keys, private documents, personal data, or production database dumps in a report.

## Deployment responsibilities

The included demo credentials and default development secrets are not suitable for production. Before public deployment, operators must rotate all secrets, disable demo seeding, restrict CORS, enable HTTPS, add request throttling, and configure upload scanning and backups.
