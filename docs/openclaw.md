# OpenClaw — Distributed Execution

OpenClaw provides remote compute nodes for Zeus, extending execution beyond
the local machine.

## Architecture

OpenClaw runs on Oracle ARM (Ampere A1) instances, providing:
- Remote Python sandbox execution
- Offloaded compute for heavy tasks
- Parallel execution alongside local Zeus instance

## Setup

```bash
# Install OpenClaw CLI
npm install -g openclaw

# Configure with your Oracle Cloud credentials
openclaw config

# Launch a node
openclaw launch --type arm --shape VM.Standard.A1.Flex
```

## Integration with Zeus

Zeus delegates tasks to OpenClaw nodes via the delegation system:
- Terminal backend: `ssh` or `modal` for remote execution
- Configured in config.yaml under `terminal.backend`
- Tasks are queued and dispatched based on node availability

## Node Types

| Type | Shape | CPU | RAM | Use Case |
|------|-------|-----|-----|----------|
| ARM Flex | VM.Standard.A1.Flex | 1-4 OCPU | 6-24 GB | General compute |
| ARM Dense | VM.Standard.A1.Flex | 4 OCPU | 24 GB | ML inference, data processing |

## Configuration

In `~/.hermes/config.yaml`:
```yaml
terminal:
  backend: ssh
  ssh:
    host: your-openclaw-node.oraclecloud.com
    user: ubuntu
    key: ~/.ssh/openclaw_key
```
