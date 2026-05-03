---
name: "hermes-email-backends"
description: "Four ready-to-deploy email backends for Hermes — AgentMail, Gmail SMTP, Proton Bridge, SendGrid/Resend. Local and production-compatible. Drop in a key and deploy."
triggers:
  - "set up email"
  - "email backend"
  - "configure email sending"
  - "multi email setup"
  - "hermes email identity"
tags: [email, smtp, agentmail, gmail, proton, sendgrid, resend, hermes]
---

# Hermes Email Backends — Instant Deploy

Four drop-in email backends. Each is self-contained — provide the key/token and it's operational.

## Quick Select

| # | Backend | Best For | Requires | When to use |
|---|---|---|---|---|
| **1** | AgentMail | Already works | Nothing (done) | Quick send, @agentmail.to identity |
| **2** | Gmail SMTP | Personal identity | App password | Sending as `ariscsc@gmail.com` |
| **3** | Proton Bridge | Privacy/E2EE | Paid plan + Docker | Sending as your Proton identity |
| **4** | Resend (preferred) / SendGrid | Bulk/marketing | Free tier API key | Newsletters, automated campaigns |

---

## 1. AgentMail (ALREADY OPERATIONAL)

**Status:** ✅ Working. Zero setup needed.

```
Inbox: hermesomni@agentmail.to
Auth: Automatic (Hermes agentmail MCP tools)
Domains: @agentmail.to
```

**Usage:** Just tell Hermes to send via AgentMail. MCP tools handle it.

**Limitations:** @agentmail.to identity only. Not configurable for custom domain. Good for agent-to-human emails.

**Env vars needed:** None (built into Hermes MCP stack)

---

## 2. Gmail SMTP (FREE — 2 min setup)

**What you need:** A Gmail App Password (not your regular password).

### Step 1: Generate App Password
1. Go to https://myaccount.google.com/security → "2-Step Verification" must be ON
2. Then https://myaccount.google.com/apppasswords
3. Select "Mail" → "Other" → name it "Hermes"
4. Copy the 16-char password (spaces don't matter)

### Step 2: Store it (secure)
```bash
# Option A: Environment variable
echo 'HERMES_GMAIL_APP_PASSWORD="abcd efgh ijkl mnop"' >> ~/.hermes/.env

# Option B: pass (if you use password-store)
pass insert email/hermes-gmail
# Paste the app password
```

### Step 3: Deploy — Local (WSL2)
```bash
# Test SMTP connectivity
curl -v smtp://smtp.gmail.com:587 --mail-from ariscsc@gmail.com --mail-rcpt ariscsc@gmail.com 2>&1 | head -5
```

**SMTP Config:**
```
Host: smtp.gmail.com
Port: 587
Encryption: STARTTLS
Username: ariscsc@gmail.com
Password: <APP PASSWORD>
From: Aris <ariscsc@gmail.com>
```

**Daily limit:** 500 emails (Gmail free tier)

### Hermes integration:
Add to `~/.hermes/config.yaml`:
```yaml
email:
  provider: smtp
  smtp:
    host: smtp.gmail.com
    port: 587
    username: ariscsc@gmail.com
    password_env: HERMES_GMAIL_APP_PASSWORD
    encryption: starttls
    from: "Aris <ariscsc@gmail.com>"
```

### Production deployment:
Same config. Gmail SMTP works from any machine. Just export `HERMES_GMAIL_APP_PASSWORD` in prod env.

---

## 3. Proton Mail Bridge (PAID ONLY — $4+/mo)

**⚠️ REQUIRES PAID PROTON PLAN (Mail Plus, Unlimited, or Business). Free accounts CANNOT use Bridge.**

### Step 1: Paid Proton account
Minimum: Mail Plus (~$4/mo or ~$48/yr)

### Step 2: Deploy Bridge via Docker
```bash
# Pull image
docker pull ghcr.io/videocurio/proton-mail-bridge:latest

# Create storage volume
mkdir -p ~/protonmail_bridge_data

# Create network (optional, for container communication)
docker network create --subnet 172.20.0.0/16 network20 2>/dev/null || true

# Launch (local-only binding for security)
docker run -d \
  --name=protonmail_bridge \
  -v ~/protonmail_bridge_data:/root \
  -p 127.0.0.1:12025:25/tcp \
  -p 127.0.0.1:12143:143/tcp \
  --network network20 \
  --restart=unless-stopped \
  ghcr.io/videocurio/proton-mail-bridge:latest
```

### Step 3: Login (interactive, one-time)
```bash
docker exec -it protonmail_bridge /bin/bash

# Inside container:
pkill bridge                          # Kill default instance
/usr/bin/bridge --cli                 # Start interactive CLI

# In bridge CLI:
>>> login
Username: your_email@proton.me
Password: ********
Two factor code: 123456

>>> change mode 0                    # Split address mode (for multiple addresses)
>>> info                              # Copy username AND password (random generated)
# Output shows IMAP and SMTP creds — SAVE THESE

>>> exit
exit                                  # Exit container shell

# Restart to apply
docker container restart protonmail_bridge
```

### Step 4: SMTP/IMAP Config
```
SMTP Host: 127.0.0.1
SMTP Port: 12025
SMTP Encryption: STARTTLS
SMTP Username: <from bridge 'info' command>
SMTP Password: <from bridge 'info' command>

IMAP Host: 127.0.0.1
IMAP Port: 12143
IMAP Username: <same as SMTP>
IMAP Password: <same as SMTP>
```

**⚠️ IMPORTANT:** The password from `info` is NOT your Proton password — Bridge generates a random app-specific password. Copy it exactly.

### Hermes integration:
```yaml
# ~/.hermes/config.yaml (Proton backend)
email:
  provider: smtp
  smtp:
    host: 127.0.0.1
    port: 12025
    username: "<bridge generated username>"
    password_env: HERMES_PROTON_BRIDGE_PASSWORD
    encryption: starttls
    from: "Your Name <your@proton.me>"
```

Store the bridge password:
```bash
echo 'HERMES_PROTON_BRIDGE_PASSWORD="<bridge password>"' >> ~/.hermes/.env
```

### Production deployment:
Bridge must run on the same host (binds 127.0.0.1). For true production, run Bridge container alongside Hermes in same Docker network.

### Limitations:
- Proton SMTP rate limits apply (expect ~50-100 emails/hour max)
- NOT suitable for bulk/marketing campaigns
- Best for personal/transactional agent emails

---

## 4. Resend (FREE TIER — Bulk/Marketing — RECOMMENDED)

**Why Resend over SendGrid:**
- Cleaner modern API, built for developers
- Free: 100 emails/day (3,000/month) 
- First-class React Email support, webhooks
- Better deliverability for transactional emails
- SDK available in Python, Node, etc.

**SendGrid alternative:** Free tier: 100 emails/day forever. More established but older API.

### Step 1: Sign up
1. Go to https://resend.com/signup
2. Sign up with `ariscsc@gmail.com` (or GitHub login)
3. Go to https://resend.com/api-keys
4. Click "Create API Key" → name it "Hermes" → **Full access** permission
5. Copy the key (starts with `re_`)

### Step 2: Verify domain (optional for testing)
Resend lets you send from `onboarding@resend.dev` for testing immediately. For production, add your domain in Resend dashboard.

### Step 3: Store the key
```bash
echo 'RESEND_API_KEY="re_xxxxxxxxxxxx"' >> ~/.hermes/.env
```

### Step 4: Python SDK (Hermes-compatible)
```bash
pip install resend
```

Test send:
```python
import resend, os
resend.api_key = os.environ["RESEND_API_KEY"]

resend.Emails.send({
    "from": "Hermes <onboarding@resend.dev>",
    "to": ["ariscsc@gmail.com"],
    "subject": "Hermes is online",
    "text": "Email backend #4 operational."
})
```

### Hermes integration:
```yaml
# ~/.hermes/config.yaml
email:
  provider: resend
  resend:
    api_key_env: RESEND_API_KEY
    from: "Hermes <hermes@yourdomain.com>"  # Once domain verified
    # For testing:
    # from: "Hermes <onboarding@resend.dev>"
```

### Production deployment:
1. Verify your domain in Resend dashboard (add DNS TXT records)
2. Update `from` to verified domain
3. Same `RESEND_API_KEY` — works everywhere

### Free tier limits:
- 100 emails/day
- 3,000 emails/month
- If exceeded: upgrade to $20/mo for 50K emails

### SendGrid Alternative Setup (if preferred):
```bash
#1. Sign up at https://signup.sendgrid.com/ (free tier: 100/day)
#2. Create API key: Settings → API Keys → Full Access
#3. Store:
echo 'SENDGRID_API_KEY="SG.xxxxxxxx"' >> ~/.hermes/.env

#4.qt Python:
# pip install sendgrid
# from sendgrid import SendGridAPIClient
# sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
```

---

## Switching Between Backends

The user says: *"send you a copy paste of a token or a key and deploy in an instant"*

Here's exactly what to tell Hermes to switch:

### Switch commands:

| Switch to | Tell Hermes |
|---|---|
| **AgentMail** | "use agentmail for email" |
| **Gmail** | "use gmail smtp, here's the app password: abcd efgh ijkl mnop" |
| **Proton** | "use proton bridge, here's the bridge password: abc123def456" |
| **Resend** | "use resend, here's the API key: re_xxxxx" |
| **SendGrid** | "use sendgrid, here's the API key: SG.xxxxx" |

###1 Automated — the config approach:

All four can coexist in `~/.hermes/config.yaml`:

```yaml
email_backends:
  default: agentmail  # which one to use by default
  
  agentmail:
    enabled: true
    # No config needed — uses MCP tools
    
  gmail:
    enabled: false  # set true to activate
    smtp_host: smtp.gmail.com
    smtp_port: 587
    username: ariscsc@gmail.com
    password_env: HERMES_GMAIL_APP_PASSWORD
    
  proton_bridge:
    enabled: false
    smtp_host: 127.0.0.1
    smtp_port: 12025
    username_env: HERMES_PROTON_USERNAME
    password_env: HERMES_PROTON_BRIDGE_PASSWORD
    
  resend:
    enabled: false
    api_key_env: RESEND_API_KEY
    from: "Hermes <hermes@yourdomain.com>"
```

To switch: change `default` and mark `enabled: true` — done.

---

## Quick Reference: What to Copy-Paste

| Backend |acción What to give Hermes | Where to get it |
|---|---|---|
| AgentMail | Nothing — works | Built-in |
| Gmail | 16-char app password | myaccount.google.com/apppasswords |
| Proton | Bridge username + password | `docker exec -it protonmail_bridge /bin/bash` → `bridge --cli` → `info` |
| Resend | `re_xxxx` API key | resend.com/api-keys |
| SendGrid | `SG.xxxx` API key | app.sendgrid.com/settings/api_keys |

---

## Pitfalls

1. **Proton free = dead end.** Don't waste time. Needs paid plan.
2. **Gmail app passwords need 2FA enabled first.** Can't skip.
3. **Resend testing domain** (`onboarding@resend.dev`) works instantly but looks unprofessional. Verify your domain for production.
4. **SendGrid free tier sends from shared IPs** — deliverability varies.
5. **Proton Bridge port mapping:** Container internal is 25/143, external is 12025/12143. Don't mix them.
6. **Never commit `.env` to git.** The `.gitignore` must41 exclude it.

## Verification

After setting up any backend:
```bash
# Quick test: send yourself a test email
# AgentMail: just tell Hermes "email me a test via agentmail"
# Others: Hermes should14 send a test to ariscsc@gmail.com
```
Check inbox — if14 received, backend is operational.