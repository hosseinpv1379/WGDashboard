# wg-node

`wg-node` is the outbound-only WireGuard agent for WGDashboard commercial
subscriptions. It polls authenticated jobs, manages peers on a local WireGuard
interface, and reports byte counters to the central panel.

## Prepare the WireGuard server

The interface must already exist and be active. Its address must be outside the
client allocation range or be the first address in that range. Example:

```ini
[Interface]
Address = 10.88.0.1/24
ListenPort = 51820
PrivateKey = SERVER_PRIVATE_KEY
SaveConfig = true
```

Enable it with `wg-quick up wg0` and open `51820/udp` in the firewall.

## Register and run

1. Open **Subscriptions & Nodes** in WGDashboard.
2. Create a node and copy the Node ID and token. The token is shown once.
3. Copy `.env.example` to `.env` and fill in `WG_NODE_ID` and
   `WG_NODE_TOKEN`.
4. Run:

```bash
docker compose --env-file .env -f compose.example.yaml up -d --build
```

For automatic registration, leave `WG_NODE_ID` and `WG_NODE_TOKEN` empty and
set `WG_NODE_BOOTSTRAP_TOKEN` to the same long random value configured on the
central panel. The generated node credentials are persisted in the
`wg_node_data` volume. Disable the central bootstrap token after registering
the required nodes.

The agent uses outbound HTTPS only. Never enable `WG_NODE_INSECURE_TLS` in
production. Use `WG_NODE_CA_CERT` for a private certificate authority. For
mTLS, configure `WG_NODE_CLIENT_CERT` and `WG_NODE_CLIENT_KEY` and require that
certificate at the panel's reverse proxy.

For native deployment, build with `go build -o wg-node .`, place the binary in
`/usr/local/bin`, copy `wg-node.service` to `/etc/systemd/system`, put the
environment variables in `/etc/wg-node.env`, and enable the service.
