# WGDashboard Docker Explanation:
Author: @DaanSelen<br>

This document delves into how the WGDashboard Docker container has been built.<br>
Of course there are two stages (simply said), one before run-time and one at/after run-time.<br>
The `Dockerfile` describes how the container image is made, and the `entrypoint.sh` is executed after the container is started. <br>
In this example, [WireGuard](https://www.wireguard.com/) is integrated into the container itself, so it should be a run-and-go(/out-of-the-box) experience.<br>
For more details on the source-code specific to this Docker image, refer to the source files, they have lots of comments.

<br>
<img 
  src="https://wgdashboard-resources.tor1.cdn.digitaloceanspaces.com/Logos/Logo-2-Rounded-512x512.png" 
  alt="WG-Dashboard Logo" 
  title="WG-Dashboard Logo"
  style="display: block; margin: 0 auto;"
  width="150"
  height="150"
/>
<br>

# WGDashboard: 🐳 Docker Deployment Guide

This fork publishes its own `wgdashboard` and `wg-node` images to GitHub
Container Registry. The images are compiled from this repository and do not
contain the upstream WGDashboard application image.

### Pull and run

```bash
cp docker/.env.example docker/.env
# Edit docker/.env and replace the example passwords and public IP.
docker compose --env-file docker/.env -f docker/compose.yaml pull
docker compose --env-file docker/.env -f docker/compose.yaml up -d
```

The Compose stack includes PostgreSQL and a built-in `wg-node-local` service.
Database files, dashboard configuration, WireGuard configuration, agent state,
outbound configuration, and AmneziaWG configuration are stored in named volumes
and survive image rebuilds and container replacement. `PUBLIC_IP` is required
because it becomes the customer-facing endpoint of the local node.

### Commercial catalog and remote nodes

Use **Users**, **Nodes**, **Node Groups**, **Outbounds**, **Packages**, and
**Subscriptions** to create timed and metered plans. A node group targets exact
WireGuard interfaces, and each new subscription is provisioned on every target.
The local interface is managed automatically; each remote server runs the Go
agent from `wg-node/`. Outbounds route a selected client pool through an upstream
WireGuard configuration without replacing the server's main default route.

Private client keys are encrypted with a Fernet key stored at
`/data/subscription.key`. This file is inside the persistent `dashboard_data`
volume and must be included in backups. It can also be supplied explicitly with
`WGD_SUBSCRIPTION_ENCRYPTION_KEY`.

### Database web panel

The stack also starts Adminer for browser-based PostgreSQL administration. By
default it listens only on the server itself at `127.0.0.1:8080`.

For local deployment, open `http://127.0.0.1:8080`. For a remote server, use an
SSH tunnel instead of exposing the database panel publicly:

```bash
ssh -L 8080:127.0.0.1:8080 your-user@your-server
```

Then open `http://127.0.0.1:8080` and use:

- System: `PostgreSQL`
- Server: `postgres`
- Username: the `POSTGRES_USER` value from `docker/.env`
- Password: the `POSTGRES_PASSWORD` value from `docker/.env`
- Database: `wgdashboard`

To change the web port, update `DB_ADMIN_PORT` in `docker/.env`. Direct public
exposure is not recommended; if it is unavoidable, set `DB_ADMIN_BIND=0.0.0.0`
and protect the port with a firewall or reverse proxy authentication.

To inspect the service:

```bash
docker compose --env-file docker/.env -f docker/compose.yaml ps
docker compose --env-file docker/.env -f docker/compose.yaml logs -f wgdashboard
```

## 🔄 Updating the Container

Pull the published images and recreate only changed containers:

```bash
docker compose --env-file docker/.env -f docker/compose.yaml pull
docker compose --env-file docker/.env -f docker/compose.yaml up -d --remove-orphans
```

Pushing to the default branch runs `.github/workflows/docker.yml` and publishes:

- `ghcr.io/hosseinpv1379/wgdashboard:latest`
- `ghcr.io/hosseinpv1379/wg-node:latest`

For a public installation, set both package visibilities to **Public** in the
GitHub package settings. For a private package, run `docker login ghcr.io` on
the server with a token that has `read:packages` permission.

To build the current checkout locally instead, add the build overlay:

```bash
docker compose --env-file docker/.env \
  -f docker/compose.yaml -f docker/compose.build.yaml \
  up -d --build
```

---

## ⚙️ Environment Variables

| Variable           | Accepted Values                          | Default                 | Example               | Description                                                             |
| ------------------ | ---------------------------------------- | ----------------------- | --------------------- | ----------------------------------------------------------------------- |
| `dynamic_config`   | true, yes, false, no                     | `true`                  | `true` or `no`        | Turns on or off the dynamic configuration feature, on by default for Docker |
| `tz`               | Timezone                                 | `Europe/Amsterdam`      | `America/New_York`    | Sets the container's timezone. Useful for accurate logs and scheduling. |
| `global_dns`       | IPv4 and IPv6 addresses                  | `9.9.9.9`               | `8.8.8.8`, `1.1.1.1`  | Default DNS for WireGuard clients.                                      |
| `public_ip`        | Public IP address                        | Retrieved automatically | `253.162.134.73`      | Used to generate accurate client configs. Needed if container is NAT’d. |
| `wgd_port`         | Any port that is allowed for the process | `10086`                 | `443`                 | This port is used to set the WGDashboard web port.                      |
| `username`         | Any non‐empty string                     | `-`                     | `admin`               | Username for the WGDashboard web interface account.                     |
| `password`         | Any non‐empty string                     | `-`                     | `s3cr3tP@ss`          | Password for the WGDashboard web interface account (stored hashed).     |
| `enable_totp`      | `true`, `false`                          | `true`                  | `false`               | Enable TOTP‐based two‐factor authentication for the account.            |
| `wg_autostart`     | Wireguard interface name                 | `-    `                 | `wg0`                 | Automatically start the specified WireGuard interface when the container starts |
| `email_server`     | SMTP server address                      | `-`                     | `smtp.gmail.com`      | SMTP server for sending email notifications.                            |
| `email_port`       | SMTP port number                         | `-`                     | `587`                 | Port for connecting to the SMTP server.                                 |
| `email_encryption` | `TLS`, `SSL`, etc.                       | `-`                     | `TLS`                 | Encryption method for email communication.                              |
| `email_username`   | Any non-empty string                     | `-`                     | `user@example.com`    | Username for SMTP authentication.                                       |
| `email_password`   | Any non-empty string                     | `-`                     | `app_password`        | Password for SMTP authentication.                                       |
| `email_from`       | Valid email address                      | `-`                     | `noreply@example.com` | Email address used as the sender for notifications.                     |
| `email_template`   | Path to template file                    | `-`                     | `your-template`       | Custom template for email notifications.                                |
| `database_type`    | `sqlite`, `postgresql`, `mariadb+mariadbconnector`, etc.           | `-` | `postgresql` | Type of [sqlalchemy database engine](https://docs.sqlalchemy.org/en/21/core/engines.html). |
| `database_host`    | Any non-empty string                     | `-`                     | `localhost`           | IP-Address or hostname of the SQL-database server.                       |
| `database_port`    | Any non-empty string (or int for port)   | `-`                     | `5432`                | Port for the database communication.                                     |
| `database_username`| Valid database username                  | `-`                     | `database_user`       | Database user username.                                                  |
| `database_password`| Valid database password                  | `-`                     | `database_password`   | Database user password.                                                  |

---

## 🔐 Port Forwarding Note

When using multiple WireGuard interfaces, remember to **open their respective ports** on the host.

Examples:
```yaml
# Individual mapping
- 51821:51821/udp

# Or port range
- 51820-51830:51820-51830/udp
```

> 🚨 **Security Tip:** Only expose ports you actually use.

---

## 🛠️ Building the Image Yourself

To build from source:

```bash
git clone https://github.com/WGDashboard/WGDashboard.git
cd WGDashboard
docker build . -f docker/Dockerfile -t yourname/wgdashboard:latest
```

Example output:
```shell
docker images

REPOSITORY           TAG       IMAGE ID       CREATED             SIZE
yourname/wgdashboard latest    c96fd96ee3b3   42 minutes ago      314MB
```

---

## 🧱 Dockerfile Overview

Here's a brief overview of the Dockerfile stages used in the image build:

### 1. **Build Tools & Go Compilation**

```Dockerfile
FROM golang:1.24 AS compiler
WORKDIR /go

RUN apt-get update && apt-get install -y ...
RUN git clone ... && make
...
```

### 2. **Binary Copy to Scratch**

```Dockerfile
FROM scratch AS bins
COPY --from=compiler /go/amneziawg-go/amneziawg-go /amneziawg-go
...
```

### 3. **Final Alpine Container Setup**

```Dockerfile
FROM alpine:latest
COPY --from=bins ...
RUN apk update && apk add --no-cache ...
COPY ./src ${WGDASH}/src
COPY ./docker/entrypoint.sh /entrypoint.sh
...
EXPOSE 10086
ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
```

---

## 🚀 Entrypoint Overview

### Major Functions:

- **`ensure_installation`**: Sets up the app, database, and Python environment.
- **`set_envvars`**: Writes `wg-dashboard.ini` and applies environment variables.
- **`start_core`**: Starts the main WGDashboard service.
- **`ensure_blocking`**: Tails the error log to keep the container process alive.

---

## ✅ Final Notes

- Use `docker logs wgdashboard` for troubleshooting.
- Access the web interface via `http://your-ip:10086` (or whichever port you specified in the compose).
- The first time run will auto-generate WireGuard keys and configs (configs are generated from the template).

## Closing remarks:

For feedback please submit an issue to the repository. Or message dselen@nerthus.nl.
