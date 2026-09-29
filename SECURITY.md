# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability, please report it responsibly.

### How to Report

1. **Do NOT** open a public GitHub issue for security vulnerabilities
2. Open a private security advisory on GitHub
3. Include as much detail as possible:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### What to Expect

- **Acknowledgment**: Within 48 hours of your report
- **Initial Assessment**: Within 5 business days
- **Resolution Timeline**: Depends on severity
  - Critical: 24-72 hours
  - High: 1-2 weeks
  - Medium: 2-4 weeks
  - Low: Next release cycle

### Disclosure Policy

- We follow coordinated disclosure
- We will credit reporters (unless anonymity is requested)
- We aim to release fixes before public disclosure
- We will notify you when the vulnerability is fixed

## Security Best Practices

When deploying HiveGate:

1. **Environment Variables**
   - Never commit secrets to version control
   - Use secret management services (Vault, AWS Secrets Manager, etc.)
   - Rotate `SECRET_TOKEN_ENC_KEY` periodically

2. **Database Security**
   - Use strong passwords for PostgreSQL
   - Enable SSL for database connections in production
   - Restrict database access to application servers only

3. **API Security**
   - Enable HTTPS in production
   - Implement rate limiting
   - Use authentication for all endpoints

4. **Token Management**
   - OAuth tokens are encrypted at rest using Fernet
   - Tokens are automatically refreshed when expired
   - Store `SECRET_TOKEN_ENC_KEY` securely

## Security Features

- **Token Encryption**: All OAuth tokens encrypted with Fernet (AES-128-CBC)
- **Input Validation**: Pydantic models validate all API inputs
- **SQL Injection Prevention**: SQLAlchemy ORM with parameterized queries
- **Dependency Scanning**: Dependabot monitors for vulnerable dependencies
- **Secret Detection**: Gitleaks prevents accidental secret commits

## Acknowledgments

We thank the security researchers who have responsibly disclosed vulnerabilities:

*No vulnerabilities reported yet*