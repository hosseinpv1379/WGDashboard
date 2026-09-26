# wg-node

`wg-node` is the single-binary data-plane agent for the WGDashboard commercial
panel. It discovers local WireGuard interfaces, polls authenticated jobs,
creates/removes peers, reports cumulative RX/TX counters, and applies optional
WireGuard outbounds with source-based policy routing.

The agent exposes no management port. It only makes outbound HTTP(S) requests
to the panel.

## Files

| Path | Purpose |
| --- | --- |
| `/usr/local/bin/wg-node` | Static agent binary |
| `/etc/wg-node/config.json` | Non-secret and multi-interface configuration |
| `/etc/wg-node.env` | systemd environment overrides and secrets |
| `/var/lib/wg-node/credentials.json` | Credentials created by bootstrap registration |
| `/var/lib/wg-node/state.json` | Session, peer, and outbound state |
| `/etc/wg-node/outbounds/*.conf` | Sanitized upstream WireGuard configurations |

Environment variables override values from `config.json`. See
[`config.example.json`](./config.example.json) and [`.env.example`](./.env.example).

## Prepare the server

The server WireGuard interface must already be active. The first host address
is reserved for the interface; the agent allocates peers from the next address.

```ini
[Interface]
Address = 10.88.0.1/24
ListenPort = 51820
PrivateKey = SERVER_PRIVATE_KEY
SaveConfig = true
```

```bash
sudo apt update
sudo apt install -y wireguard iptables
sudo sysctl -w net.ipv4.ip_forward=1
sudo systemctl enable --now wg-quick@wg0
sudo wg show wg0
```

## Docker deployment

1. Create the node in **Commercial Panel → Nodes** and save the one-time Node ID
   and token.
2. Copy `.env.example` to `.env` and enter the panel URL, credential, endpoint,
   interface, and address pool.
3. Start the agent:

```bash
docker compose --env-file .env -f compose.example.yaml up -d --build
docker compose --env-file .env -f compose.example.yaml logs -f wg-node
```

For bootstrap registration, leave `WG_NODE_ID` and `WG_NODE_TOKEN` empty and
set `WG_NODE_BOOTSTRAP_TOKEN` to the panel's temporary bootstrap token. Remove
the bootstrap token from the panel after all intended nodes have registered.

The central Docker stack already includes `wg-node-local`; do not start this
standalone Compose service a second time on the panel server.

## Native binary deployment

Build on a machine with Go, then copy the resulting binary to the node. The
runtime server does not need Go installed.

```bash
cd wg-node
CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o wg-node .
sudo install -m 0755 wg-node /usr/local/bin/wg-node
sudo install -m 0700 -d /etc/wg-node/outbounds /var/lib/wg-node
sudo install -m 0600 config.example.json /etc/wg-node/config.json
sudo install -m 0600 .env.example /etc/wg-node.env
sudo install -m 0644 wg-node.service /etc/systemd/system/wg-node.service
sudo nano /etc/wg-node/config.json
sudo nano /etc/wg-node.env
sudo systemctl daemon-reload
sudo systemctl enable --now wg-node
sudo journalctl -u wg-node -f
```

## Multiple server interfaces

Define per-interface pools and public endpoints in `config.json`:

```json
{
  "default_interface": "wg0",
  "address_pool": "10.88.0.0/24",
  "public_endpoint": "vpn.example.com:51820",
  "interface_pools": {
    "wg0": "10.88.0.0/24",
    "wg1": "10.89.0.0/24"
  },
  "interface_endpoints": {
    "wg0": "vpn.example.com:51820",
    "wg1": "vpn.example.com:51821"
  }
}
```

Every active WireGuard interface is reported to the panel. Node Groups target a
specific `node / interface` pair, so one subscription can receive multiple
configurations from one or several servers.

## WireGuard outbounds

Create outbounds from **Commercial Panel → Outbounds**. An outbound routes only
the selected source interface pool through an upstream WireGuard client tunnel.
It does not replace the node's main default route.

The pasted upstream config must contain `AllowedIPs = 0.0.0.0/0`. Before writing
the config, the agent removes `PreUp`, `PostUp`, `PreDown`, `PostDown`, `DNS`,
`Table`, and `SaveConfig`, then injects `Table = off`. This prevents pasted
operator hooks from executing and keeps route ownership in the agent. The
current implementation supports IPv4 source pools.

Useful checks:

```bash
sudo wg show
sudo ip rule show
sudo ip route show table all
sudo iptables -t nat -S POSTROUTING
```

## Traffic accounting

WireGuard exposes cumulative counters. The agent sends a session ID, sequence,
RX bytes, and TX bytes; the panel records only the delta and ignores exact or
stale duplicates. Missing reports are recovered by the next cumulative report.
Usage is `RX + TX`, shared by every configuration in the subscription, and is
displayed as GiB (`1024^3` bytes).

## Recovery and retries

At startup the agent reapplies enabled peers from its persistent state and
removes locally disabled peers from the running interface. Every heartbeat also
sends a peer inventory. If `state.json` is lost during a reinstall, the panel
queues recovery jobs with the original peer address and key instead of silently
allocating a different configuration. The original preshared key is delivered
only in the authenticated recovery job, so already-downloaded client configs
remain valid. Active outbounds are reapplied as well, and unknown local peers or
outbounds are removed.

Node execution failures are retried by the panel up to three times. After the
cause is fixed, an operator can use **Subscriptions → Retry** for a peer that is
still in `error` state.

## TLS

Use HTTPS in production. Use `WG_NODE_CA_CERT` for a private CA. For mTLS, set
both `WG_NODE_CLIENT_CERT` and `WG_NODE_CLIENT_KEY` and enforce that certificate
at the reverse proxy. `WG_NODE_INSECURE_TLS=true` is only for temporary testing.
