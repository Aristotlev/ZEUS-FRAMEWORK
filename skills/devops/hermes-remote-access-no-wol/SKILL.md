---
name: hermes-remote-access-no-wol
title: Hermes Remote Access Setup (No Wake-on-LAN)
description: Set up 24/7 remote access to Hermes on Windows+WSL via Tailscale and Parsec when WOL is not feasible
tags: [hermes, remote-access, windows, wsl, tailscale, parsec, networking]
---

# Hermes Remote Access Setup (No Wake-on-LAN)

## When to Use This

Wake-on-LAN doesn't work in certain network setups:
- WiFi extenders don't forward magic packets
- Ethernet through extenders is unstable
- Some network hardware lacks WOL support

**Alternative:** Keep PC always-on 24/7, use Tailscale + Parsec for remote access.

## Windows Setup (You Do This)

### 1. Disable Sleep Mode
Settings → System → Power & Sleep → Sleep: **Never**

### 2. Install Tailscale
- tailscale.com → Download Windows
- Create free account, log in
- Verify blue dot in system tray

### 3. Install Parsec  
- parsec.app → Download Windows
- Create free account, log in
- It auto-detects your PC

### 4. Create Auto-Start Task
Run PowerShell as Admin:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
C:\Users\<YourUsername>\Desktop\setup-hermes-autostart.ps1
```

## WSL Setup (Agent-Provided)

### 1. Install Tailscale (Binary Method)
```bash
cd /tmp
wget https://pkgs.tailscale.com/stable/tailscale_1.68.1_amd64.tgz
tar xzf tailscale.tgz
mkdir -p ~/.local/bin
cp tailscale_*/tailscale ~/.local/bin/
cp tailscale_*/tailscaled ~/.local/bin/
chmod +x ~/.local/bin/tail*
```

### 2. Create Startup Script
Save as `~/.local/bin/start-tailscale.sh` and make executable:
```bash
#!/bin/bash
STATE_DIR="/var/lib/tailscale"
STATE_FILE="$STATE_DIR/tailscaled.state"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

~/.local/bin/tailscaled -state=$STATE_FILE &
sleep 2

if ! ~/.local/bin/tailscale status 2>/dev/null; then
    ~/.local/bin/tailscale up --accept-routes --accept-dns
fi
```

## Phone Setup (You Do This)

1. App Store / Play Store → Install **Tailscale** → Log in (same account as desktop)
2. App Store / Play Store → Install **Parsec** → Log in (same account as desktop)

## Verify It Works

On WSL:
```bash
~/.local/bin/start-tailscale.sh
tailscale status  # Should show 100.x.x.x IP
```

On phone:
- Open Parsec → desktop appears → tap to connect → full desktop access

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Desktop unreachable from phone | Disable sleep mode; verify both have Tailscale connected; same account on both |
| Hermes not responding to Discord | Gateway might not have restarted; check `ps aux \| grep hermes`; restart if needed |
| Tailscale installer failed in WSL | Use binary method above (apt bootstrap needs interactive sudo) |
| Parsec says PC offline | May take 30s to update; restart Parsec app on phone |

## Key Pitfalls

- ❌ Leave sleep mode enabled → PC goes to sleep, unreachable
- ❌ Use different Tailscale accounts → Can't reach each other  
- ❌ Try to fix WOL through extender → Won't work; skip it
- ❌ Forget to verify auto-start task runs → Hermes won't start on next boot

## When This Approach Makes Sense

✅ WiFi extender, managed switch, no WOL support  
✅ Want simple always-on without hardware tweaking  
✅ Need reliable remote access from anywhere  

❌ Need to power on from completely off  
❌ PC power usage is a concern  
❌ Can physically rewire to direct router connection  

**Setup time: ~20 minutes. Reward: always-available personal agent server.**
