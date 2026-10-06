# Render Deployment Security Checklist

This document details the security controls and configurations implemented for deploying **SCX.AI Threaded ChatBot** on Render, covering all 18 security requirements and the 8 key deployment rules.

---

## 8 Essential Deployment Rules (Team Quick Checklist)

```
HTTPS → Environment Secrets → Production DB → Debug OFF → Authentication → No Sensitive Logs → LLM Data Policy → Security Testing
```

1. **HTTPS**: Enforce HTTPS/TLS encryption across all endpoints.
2. **Environment Secrets**: Keep API keys out of repository code files; use Render Dashboard Environment Variables.
3. **Production DB**: Use encrypted database connections with WAL mode or PostgreSQL.
4. **Debug OFF**: Set `DEBUG=false` and `ENVIRONMENT=production`.
5. **Authentication**: Configure application authentication tokens (`APP_AUTH_KEY`).
6. **No Sensitive Logs**: Never log confidential user prompts, uploaded documents, or API tokens.
7. **LLM Data Policy**: Verify provider data retention and model training privacy policies.
8. **Security Testing**: Conduct pre-deployment tests for authentication, API rate limits, and path traversal vulnerabilities.

---

## Complete 18-Point Render Security Implementation Matrix

| # | Security Checklist Item | Project Implementation & Status |
|---|-------------------------|---------------------------------|
| **1** | **Enable HTTPS/TLS** | Enforced automatically by Render (`*.onrender.com`). `HSTS` headers (`Strict-Transport-Security`) configured in `server.py`. |
| **2** | **Secrets in Render Env Vars** | All API keys (`SCX_API_KEY`, `GROUNDED_API_KEY`, `GROUNDED_AGENT_ID`, `APP_AUTH_KEY`) moved to environment variables (`sync: false` in `render.yaml`). `.env` added to `.gitignore`. |
| **3** | **Production Database Encryption** | SQLite configured with `WAL` mode and timeout safety (`timeout=30.0`). PostgreSQL support ready via `DATABASE_URL` for production SSL encrypted connections. |
| **4** | **Production Env Segregation** | Supported via `ENVIRONMENT=production` and `.env.example` template file for local vs production configuration. |
| **5** | **Disable Debug Mode** | Debug mode dynamically controlled via `DEBUG=false` in `render.yaml`. Dynamic FastAPI Swagger docs auto-disabled when `DEBUG=false`. |
| **6** | **Clean Deployment Package** | Created `.dockerignore` and updated `.gitignore` to prevent confidential database files (`*.db`), `.env` secrets, and test documents from entering the build context. |
| **7** | **Secure Authentication & Sessions** | Added `verify_auth()` middleware helper supporting `APP_AUTH_KEY` validation via headers (`X-API-Key` or `Authorization: Bearer`). |
| **8** | **API Rate & Request Size Limits** | In-memory IP rate limiter middleware (`RATE_LIMIT_PER_MINUTE=60`). Upload file size strictly capped at 20MB (`MAX_UPLOAD_SIZE_MB=20`). |
| **9** | **Validate Inputs & File Uploads** | Path traversal protection via `safe_path_in_dir()`, file extension whitelist (`.pdf`, `.docx`, `.txt`, `.md`, `.json`, `.csv`, `.py`, `.js`, `.html`), and filename sanitization (`os.path.basename`). |
| **10** | **Prevent Sensitive Content Logging** | Prompts, responses, and API credentials redacted in server logs. Generic user-facing error messages delivered on failures. |
| **11** | **Secure Access & CORS Control** | CORS origin whitelist enforced via `ALLOWED_ORIGINS` environment variable instead of wildcard `*`. |
| **12** | **Verify LLM Data Retention Policy** | SCX.AI API and Grounded AI providers configured for enterprise endpoints; zero local storage of unencrypted payloads outside DB. |
| **13** | **Security Alerts & Monitoring** | Clean `/health` check endpoint provided for uptime monitoring with status flags, without leaking credentials. |
| **14** | **Latest Dependency Versions** | Locked stable versions specified in `requirements.txt` (`fastapi`, `uvicorn`, `pydantic`, `openai`, `pypdf`, `python-docx`). |
| **15** | **Pre-Deployment Security Testing** | Automated path traversal tests, input boundary checks, and route auth verification performed. |
| **16** | **Backup & Data Retention** | Database backup strategy defined for Render disk mounts or managed PostgreSQL daily snapshots. |
| **17** | **Rotate Dev/Test Credentials** | Hardcoded test keys completely purged from `render.yaml` and `server.py`. |
| **18** | **Render Security Terms Review** | Reviewed Render SOC 2 Type II compliance standards, TLS configurations, and zero-trust container environment requirements. |

---

## Render Deployment Setup Instructions

1. **Push Repository**: Ensure `.env` is NOT pushed to GitHub (`.gitignore` protects this).
2. **Create Web Service on Render**:
   - Select **Web Service** on Render Dashboard.
   - Connect repository `samarthpatel-commits/Scx.ai`.
   - Select `render.yaml` Blueprint or set Build Command: `pip install -r requirements.txt`, Start Command: `uvicorn server:app --host 0.0.0.0 --port $PORT`.
3. **Set Environment Variables in Render Dashboard**:
   - `SCX_API_KEY` = `<your_production_scx_key>`
   - `GROUNDED_API_KEY` = `<your_production_grounded_key>`
   - `GROUNDED_AGENT_ID` = `<your_production_grounded_agent_id>`
   - `ENVIRONMENT` = `production`
   - `DEBUG` = `false`
   - `ALLOWED_ORIGINS` = `https://your-domain.onrender.com`
