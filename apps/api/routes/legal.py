"""Public legal pages: privacy policy and data-deletion instructions (no auth required).

Meta App Review mandates a publicly reachable Privacy Policy URL and a Data
Deletion URL. The deletion endpoint itself lives at
POST /api/v1/auth/meta/data-deletion; this page documents the policy and the
deletion paths for users.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/legal", tags=["legal"])

_PRIVACY_POLICY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Metuse AI — Privacy Policy</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:0 auto;padding:32px 20px;line-height:1.6;color:#1a1a2e;background:#fff}
h1{font-size:28px}h2{font-size:19px;margin-top:28px}code{background:#f1f1f6;padding:1px 6px;border-radius:4px;font-size:13px}
.muted{color:#666;font-size:13px}
</style>
</head>
<body>
<h1>Metuse AI — Privacy Policy</h1>
<p class="muted">Last updated: 28 November 2026</p>

<h2>1. What Metuse AI does</h2>
<p>Metuse AI is a tenant-aware social growth and media operations platform. Agencies and
creators use it to research, plan, create, edit, publish, and measure social content,
including organic Instagram publishing and Meta advertising workflows.</p>

<h2>2. Data we collect</h2>
<ul>
<li><strong>Account data:</strong> email address, name, and avatar when you register directly
or sign in with Meta or Google. Passwords are stored only as salted scrypt hashes.</li>
<li><strong>Workspace content:</strong> workspaces, clients, brand profiles, projects, media
assets, worker outputs, publishing calendars, and ad campaign drafts you create.</li>
<li><strong>Meta platform data:</strong> when you connect Meta accounts, we receive OAuth
tokens and the Pages, Instagram Professional accounts, and ad accounts you explicitly
select. Raw tokens are held only in an encrypted server-side secret vault; our database
stores opaque references only.</li>
<li><strong>Advertising data:</strong> campaign structures, budgets, and performance snapshots
synced from Meta Marketing API, plus Conversions API events you explicitly send.</li>
<li><strong>Operational data:</strong> audit logs (who did what, when), error reports, and
standard request metadata used for security and abuse prevention.</li>
</ul>

<h2>3. How we use data</h2>
<p>We use your data to operate your workspaces, execute the AI, publishing, and advertising
workflows you request, enforce tenant isolation and approvals, prevent abuse, and comply
with legal obligations. We do not sell personal data and we do not use your content to
train shared models.</p>

<h2>4. Data sharing</h2>
<p>Data is shared only with the service providers required to run Metuse AI (hosting,
database, object storage, error monitoring) and with Meta Platforms, Inc. when you
authorize a connection or publish content. Each integration receives only the data needed
for the action you requested.</p>

<h2>5. Data retention and deletion</h2>
<p>You can delete your account at any time from the product (account settings) or by
requesting deletion — this irreversibly scrubs your profile data and revokes your
sessions and connected secrets. If you remove Metuse AI from your Meta account, Meta
notifies our Data Deletion endpoint and we scrub the linked account data automatically;
you can check the status of such a request with the confirmation code Meta provides via
<code>/api/v1/auth/meta/data-deletion/status</code>. Audit log entries required for fraud
prevention and legal compliance are retained in anonymized form.</p>

<h2>6. Security</h2>
<p>We protect data with encrypted connections, salted password hashing, signed sessions
with server-side revocation, tenant-isolated database access, encrypted secret storage
for third-party tokens, human approval gates on publishing and ad spend, and secret
scrubbing in logs and error reports.</p>

<h2>7. Your rights</h2>
<p>Depending on your jurisdiction you may have the right to access, correct, export, or
delete your personal data, and to object to or restrict certain processing. Contact us
through the product support channel to exercise these rights.</p>

<h2>8. Changes</h2>
<p>We will update this page when our practices change and note the revision date above.
Continued use of Metuse AI after changes take effect constitutes acceptance.</p>
</body>
</html>
"""


@router.get("/privacy-policy", response_class=HTMLResponse)
def get_privacy_policy() -> HTMLResponse:
    """Return the public Metuse AI privacy policy (Meta App Review requirement)."""
    return HTMLResponse(content=_PRIVACY_POLICY_HTML)
