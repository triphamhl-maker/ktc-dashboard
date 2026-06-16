<!-- security-rules v1 -->

# Security Rules for AI Coding Assistants

Copy this block into your AI assistant's rules/instructions file to enforce secure coding practices on every code generation request. Common targets:

- Claude Code — `CLAUDE.md`
- Antigravity — `Agent.md`
- Cursor — `.cursorrules`
- Windsurf — `.windsurfrules`
- GitHub Copilot — `.github/copilot-instructions.md`
- Cline — `.clinerules/`

---

## Input & Output

- Never render user-supplied input as raw HTML; always escape output before inserting it into the DOM.
- Never concatenate user input directly into SQL queries; always use parameterized queries or a query builder.
- Validate and sanitize all input at system boundaries: HTTP request bodies, query parameters, file uploads, and form fields.
- Never use `dangerouslySetInnerHTML`, `innerHTML`, or `eval()` with user-supplied content.

## Authentication & Authorization

- Require authentication on every state-changing endpoint (POST, PUT, PATCH, DELETE).
- After authenticating a request, verify the authenticated user owns or has explicit permission to access the requested resource before returning or modifying it.
- Never derive ownership or access rights from a client-supplied ID in the request body — always use the authenticated session to determine who the caller is.
- Protect all pages and API routes that display private data behind an authentication gate.
- Always hash passwords with a slow, salted algorithm (bcrypt, scrypt, or argon2); never store, log, or transmit plaintext passwords.
- Always enforce a minimum-effort account lockout or backoff after repeated failed authentication attempts.

## Secrets

- Never hardcode API keys, tokens, passwords, database credentials, or any secret in source code.
- Always read secrets from environment variables or a secrets manager (e.g., AWS Secrets Manager, Vault).
- Never log secrets, tokens, or full request bodies that may contain credentials.
- Never return secrets or internal credentials in API responses or error messages.

## CSRF & Session

- Use `SameSite=Strict` or `SameSite=Lax` on all session and authentication cookies.
- Always set the `HttpOnly` and `Secure` flags on all session and authentication cookies.
- Require a CSRF token on all state-changing endpoints when using cookie-based authentication.
- Treat `SameSite` as defense-in-depth, not a replacement for CSRF tokens.
- Validate the `Origin` or `Referer` header on state-changing endpoints as a defense-in-depth measure.

## Transport Security

- Always serve all traffic over HTTPS/TLS; redirect HTTP to HTTPS.
- Always set `Strict-Transport-Security` (HSTS) on HTTPS responses to enforce secure connections.

## Security Headers

- Always set `Content-Security-Policy` on all HTTP responses; default to `default-src 'self'` and expand only as needed.
- Always set `X-Frame-Options: DENY` to prevent clickjacking.
- Always set `X-Content-Type-Options: nosniff`.
- Always set `Referrer-Policy: strict-origin-when-cross-origin`.
- Always set `Permissions-Policy` to disable browser features the application does not use.
- Never expose framework or server version headers; remove `X-Powered-By` and equivalent headers.

## Rate Limiting

- Apply rate limiting to every public endpoint, with stricter limits on authentication, password reset, email/SMS sending, and any endpoint that triggers outbound requests.
- Enforce per-IP limits; do not rely on per-account limits alone for unauthenticated endpoints.
- Return HTTP 429 with a `Retry-After` header when a rate limit is exceeded.

## Data Exposure & External Requests

- Return only the fields the caller needs; never return full database rows or internal model objects.
- Never expose stack traces, internal error messages, file paths, or server infrastructure details in API responses.
- Add `<meta name="robots" content="noindex, nofollow">` to any server-rendered page that displays private or sensitive data.
- Never make server-side HTTP requests to user-supplied URLs without validating against an explicit allowlist of permitted domains or resource IDs (SSRF prevention).
- Allowlist specific external resource identifiers (e.g., allowed Google Sheet IDs) on the server side; do not trust user-provided identifiers.
