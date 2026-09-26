# راهنمای WGDashboard تجاری و `wg-node`

این راهنما نصب، ارتقا و استفاده از نسخهٔ چندسرورهٔ WGDashboard را توضیح می‌دهد.
ساختار آن از مستندات بخش‌بندی‌شدهٔ [XMPlus](https://docs.xmplus.dev/) الهام گرفته
شده است، اما تمام فرمان‌ها و تنظیمات این فایل مخصوص همین مخزن هستند.

> [!IMPORTANT]
> اگر WGDashboard از قبل روی سرور نصب است، از بخش
> [ارتقای نصب فعلی](#ارتقای-نصب-فعلی-بدون-حذف-اطلاعات) شروع کنید. نصب تازه را روی
> همان سرور اجرا نکنید و هیچ‌وقت برای ارتقا از `docker compose down -v` استفاده
> نکنید؛ گزینهٔ `-v` Volumeهای داده را حذف می‌کند.

> [!NOTE]
> نسخهٔ تجاری فعلاً روی شاخهٔ `codex/wireguard` قرار دارد. دستورهای این راهنما
> همین شاخه را نصب می‌کنند. بعد از Merge شدن نسخهٔ تجاری در `main` می‌توانید نام
> شاخه را در فرمان‌ها به `main` تغییر دهید.

## فهرست

- [معماری ساده](#معماری-ساده)
- [انتخاب مسیر نصب](#انتخاب-مسیر-نصب)
- [ارتقای نصب فعلی بدون حذف اطلاعات](#ارتقای-نصب-فعلی-بدون-حذف-اطلاعات)
- [نصب تازهٔ WGDashboard مرکزی](#نصب-تازهٔ-wgdashboard-مرکزی)
- [انتشار ایمیج‌های اختصاصی](#انتشار-ایمیجهای-اختصاصی)
- [دسترسی به دیتابیس با Adminer](#دسترسی-به-دیتابیس-با-adminer)
- [نصب `wg-node` روی سرور WireGuard](#نصب-wg-node-روی-سرور-wireguard)
- [ساخت کاربر و اشتراک چندسروره](#ساخت-کاربر-و-اشتراک-چندسروره)
- [ساخت Outbound وایرگارد](#ساخت-outbound-وایرگارد)
- [مدل زمان و حجم](#مدل-زمان-و-حجم)
- [بکاپ، به‌روزرسانی و بازگردانی](#بکاپ-بهروزرسانی-و-بازگردانی)
- [عیب‌یابی](#عیبیابی)
- [فرمان‌های روزمره](#فرمانهای-روزمره)
- [پرسش‌های متداول](#پرسشهای-متداول)

## معماری ساده

```text
                         HTTPS
                    ┌──────────────┐
 Admin / Client ───►│ WGDashboard  │
                    │ Control Plane│
                    └──────┬───────┘
                           │
                       PostgreSQL
                           │
              Job polling + Traffic report
                   (خروجی HTTPS از Node)
          ┌────────────────┼────────────────┐
          │                │                │
     ┌────▼────┐      ┌────▼────┐      ┌────▼────┐
     │wg-node 1│      │wg-node 2│      │wg-node 3│
     │WireGuard│      │WireGuard│      │WireGuard│
     └─────────┘      └─────────┘      └─────────┘
```

- پنل مرکزی کاربر، اشتراک، زمان، حجم و Jobها را مدیریت می‌کند.
- PostgreSQL اطلاعات پنل و مصرف را نگه می‌دارد.
- روی هر سرور VPN یک `wg-node` اجرا می‌شود.
- Node فقط به‌صورت خروجی به پنل وصل می‌شود؛ پورت مدیریتی عمومی لازم ندارد.
- کاربر برای هر Node یک فایل WireGuard مجزا دریافت می‌کند.

### پورت‌ها

| محل | پورت | کاربرد |
| --- | --- | --- |
| سرور مرکزی | `10086/TCP` | رابط WGDashboard در حالت دسترسی مستقیم |
| سرور مرکزی | `443/TCP` | حالت پیشنهادی با دامنه و Reverse Proxy |
| هر Node | `51820/UDP` | اتصال کاربران WireGuard |
| سرور مرکزی | `127.0.0.1:8080/TCP` | Adminer؛ فقط از طریق SSH Tunnel |

## انتخاب مسیر نصب

| وضعیت شما | بخش مناسب |
| --- | --- |
| پنل همین حالا با Docker و PostgreSQL اجرا می‌شود | ارتقای نصب فعلی |
| پنل قدیمی از SQLite استفاده می‌کند | ارتقای نصب فعلی با `DATABASE_TYPE=sqlite` |
| هنوز پنل مرکزی نصب نشده | نصب تازهٔ WGDashboard مرکزی |
| پنل مرکزی آماده است و می‌خواهید سرور VPN اضافه کنید | نصب `wg-node` |

---

## ارتقای نصب فعلی بدون حذف اطلاعات

این مسیر برای سروری است که پروژه در `/opt/WGDashboard` قرار دارد. اگر مسیر شما
متفاوت است، همان مسیر واقعی را جایگزین کنید.

### مرحلهٔ ۱: وضعیت فعلی را ثبت کنید

```bash
cd /opt/WGDashboard

git status --short
docker compose --env-file docker/.env -f docker/compose.yaml ps
docker inspect wgdashboard --format '{{range .Mounts}}{{println .Name "->" .Destination}}{{end}}'
docker volume ls
```

خروجی `docker inspect` را نگه دارید. نام Volume مقابل مسیرهای زیر مهم است:

```text
/data
/etc/wireguard
/etc/amnezia/amneziawg
```

### مرحلهٔ ۲: نوع دیتابیس فعلی را تشخیص دهید

```bash
docker exec wgdashboard sh -c \
  "sed -n '/^\[Database\]/,/^\[/p' /data/wg-dashboard.ini"
```

- اگر `type = postgresql` است، مقدار `DATABASE_TYPE=postgresql` را استفاده کنید.
- اگر `type = sqlite` است، برای اولین ارتقا مقدار `DATABASE_TYPE=sqlite` را
  نگه دارید. تغییر مستقیم به PostgreSQL اطلاعات SQLite را منتقل نمی‌کند.

> [!WARNING]
> این پروژه مهاجرت خودکار SQLite به PostgreSQL ندارد. در نصب SQLite ابتدا با
> همان SQLite ارتقا دهید و بعد از تهیهٔ بکاپ، مهاجرت دیتابیس را جدا انجام دهید.

### مرحلهٔ ۳: بکاپ بگیرید

```bash
cd /opt/WGDashboard
umask 077
BACKUP_DIR="/root/wgdashboard-backup-$(date +%F-%H%M%S)"
mkdir -p "$BACKUP_DIR"

cp docker/.env "$BACKUP_DIR/docker.env"
docker inspect wgdashboard > "$BACKUP_DIR/wgdashboard-inspect.json"
docker cp wgdashboard:/data "$BACKUP_DIR/"
docker cp wgdashboard:/etc/wireguard "$BACKUP_DIR/"
docker cp wgdashboard:/etc/amnezia/amneziawg "$BACKUP_DIR/amneziawg"
```

اگر دیتابیس PostgreSQL است، Dump منطقی هم بگیرید:

```bash
docker compose --env-file docker/.env -f docker/compose.yaml \
  exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$BACKUP_DIR/wgdashboard.sql"
```

وجود فایل‌ها را بررسی کنید:

```bash
find "$BACKUP_DIR" -maxdepth 2 -type f -ls
```

### مرحلهٔ ۴: کد را به‌روز کنید

اگر تغییر محلی روی سرور ندارید:

```bash
cd /opt/WGDashboard
git fetch origin codex/wireguard
git switch codex/wireguard
git pull --ff-only origin codex/wireguard
```

اگر `git status` فایل تغییریافته نشان می‌دهد، قبل از Pull آن تغییر را Commit یا
به محل امن منتقل کنید. فایل‌های سرور را با `git reset --hard` پاک نکنید.

### مرحلهٔ ۵: فایل `.env` فعلی را تکمیل کنید

فایل فعلی را با `.env.example` جایگزین نکنید. فقط گزینه‌های جدید را به
`docker/.env` اضافه کنید:

```dotenv
WGD_IMAGE=ghcr.io/hosseinpv1379/wgdashboard:latest
WG_NODE_IMAGE=ghcr.io/hosseinpv1379/wg-node:latest
DATABASE_TYPE=postgresql

WGD_NODE_BOOTSTRAP_TOKEN=
WGD_SUBSCRIPTION_ENCRYPTION_KEY=

AMNEZIA_CONFIG_VOLUME=wgdashboard-commercial_amnezia_config
WIREGUARD_CONFIG_VOLUME=wgdashboard-commercial_wireguard_config
DASHBOARD_DATA_VOLUME=wgdashboard-commercial_dashboard_data
POSTGRES_DATA_VOLUME=wgdashboard-commercial_postgres_data
WG_NODE_LOCAL_DATA_VOLUME=wgdashboard-commercial_wg_node_local_data
WG_NODE_LOCAL_CONFIG_VOLUME=wgdashboard-commercial_wg_node_local_config

WGD_LOCAL_NODE_NAME=panel-local
WGD_LOCAL_NODE_REGION=Local
WGD_LANGUAGE=en-US
PUBLIC_IP=YOUR_SERVER_PUBLIC_IP_OR_HOSTNAME
```

اگر نام Volumeهای مرحلهٔ ۱ متفاوت بود، مقدار سمت راست را دقیقاً با نام قدیمی
عوض کنید. مثال:

```dotenv
DASHBOARD_DATA_VOLUME=docker_dashboard_data
WIREGUARD_CONFIG_VOLUME=docker_wireguard_config
POSTGRES_DATA_VOLUME=docker_postgres_data
```

برای نصب SQLite:

```dotenv
DATABASE_TYPE=sqlite
```

در حالت SQLite سرویس PostgreSQL اجرا می‌شود، ولی پنل تا زمان مهاجرت از فایل
SQLite داخل `/data` استفاده می‌کند.

### مرحلهٔ ۶: تنظیمات Compose را قبل از اجرا بررسی کنید

```bash
docker compose --env-file docker/.env -f docker/compose.yaml config --quiet
docker compose --env-file docker/.env -f docker/compose.yaml config --volumes
```

نام Volumeهای نمایش‌داده‌شده باید با Volumeهای قدیمی شما یکی باشد.

### مرحلهٔ ۷: بدون پاک‌کردن Volumeها ارتقا دهید

نیازی به `down` نیست:

```bash
docker compose --env-file docker/.env -f docker/compose.yaml pull
docker compose --env-file docker/.env -f docker/compose.yaml \
  up -d --remove-orphans
```

در اولین اجرا جدول‌های Node، Interface، Node Group، Package، Outbound، Snapshot
فروش و اشتراک تجاری به‌صورت خودکار ساخته می‌شوند. سرویس `wg-node-local` نیز
Interfaceهای همان سرور پنل را ثبت و مدیریت می‌کند.

### مرحلهٔ ۸: نتیجه را بررسی کنید

```bash
docker compose --env-file docker/.env -f docker/compose.yaml ps
docker compose --env-file docker/.env -f docker/compose.yaml logs --tail=200 wgdashboard
docker compose --env-file docker/.env -f docker/compose.yaml logs --tail=200 wg-node-local
docker compose --env-file docker/.env -f docker/compose.yaml logs --tail=100 postgres
```

سپس پنل را باز کنید:

```text
http://SERVER_IP:10086
```

در منوی کناری باید بخش‌های **Users**، **Nodes**، **Node Groups**، **Outbounds**،
**Packages** و **Subscriptions** نمایش داده شوند. رابط Docker با
`WGD_LANGUAGE=en-US` به انگلیسی نگه داشته می‌شود.

---

## نصب تازهٔ WGDashboard مرکزی

### نیازمندی پیشنهادی

| مورد | حداقل | پیشنهادی |
| --- | --- | --- |
| سیستم‌عامل | Ubuntu 22.04/24.04 64-bit | آخرین Ubuntu LTS |
| CPU | ۱ هسته | ۲ هسته یا بیشتر |
| RAM | ۲ گیگابایت | ۴ گیگابایت |
| Disk | ۱۰ گیگابایت | SSD با فضای بکاپ |

راهنمای رسمی Docker، Ubuntuهای 64-bit پشتیبانی‌شده و روش نصب از مخزن `apt` را
در [Docker Engine for Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
نگه می‌دارد.

### مرحلهٔ ۱: نصب Docker Engine و Compose

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker

sudo docker version
sudo docker compose version
```

### مرحلهٔ ۲: دریافت پروژه

```bash
sudo git clone --branch codex/wireguard --single-branch \
  https://github.com/hosseinpv1379/WGDashboard.git /opt/WGDashboard
cd /opt/WGDashboard
```

### مرحلهٔ ۳: ساخت تنظیمات مرکزی

```bash
cp docker/.env.example docker/.env
nano docker/.env
```

#### قالب سادهٔ `docker/.env`

هر مقدار `CHANGE_ME` را قبل از اجرا عوض کنید:

```dotenv
WGD_IMAGE=ghcr.io/hosseinpv1379/wgdashboard:latest
WG_NODE_IMAGE=ghcr.io/hosseinpv1379/wg-node:latest
VCS_URL=https://github.com/hosseinpv1379/WGDashboard

WGD_ADMIN_USERNAME=admin
WGD_ADMIN_PASSWORD=CHANGE_ME_ADMIN_PASSWORD
WGD_LANGUAGE=en-US

DATABASE_TYPE=postgresql
POSTGRES_USER=wgdashboard
POSTGRES_PASSWORD=CHANGE_ME_DATABASE_PASSWORD

WGD_NODE_BOOTSTRAP_TOKEN=
WGD_SUBSCRIPTION_ENCRYPTION_KEY=

AMNEZIA_CONFIG_VOLUME=wgdashboard-commercial_amnezia_config
WIREGUARD_CONFIG_VOLUME=wgdashboard-commercial_wireguard_config
DASHBOARD_DATA_VOLUME=wgdashboard-commercial_dashboard_data
POSTGRES_DATA_VOLUME=wgdashboard-commercial_postgres_data
WG_NODE_LOCAL_DATA_VOLUME=wgdashboard-commercial_wg_node_local_data
WG_NODE_LOCAL_CONFIG_VOLUME=wgdashboard-commercial_wg_node_local_config

WGD_LOCAL_NODE_NAME=panel-local
WGD_LOCAL_NODE_REGION=Local
WGD_LOCAL_NODE_CAPACITY=0
WG_NODE_INTERFACE=wg0
WG_NODE_REPORT_SECONDS=10

DB_ADMIN_BIND=127.0.0.1
DB_ADMIN_PORT=8080

TZ=Asia/Tehran
WGD_PORT=10086
WG_PORT=51820
WG_NETWORK=10.0.0.1
WG_SUBNET=24
WG_AUTOSTART=wg0
GLOBAL_DNS=1.1.1.1
PUBLIC_IP=YOUR_SERVER_PUBLIC_IP
```

برای تولید رمزهای تصادفی:

```bash
openssl rand -base64 36
```

`WGD_SUBSCRIPTION_ENCRYPTION_KEY` می‌تواند خالی بماند. در این حالت پنل کلید
معتبر را داخل Volume و در مسیر `/data/subscription.key` می‌سازد. این فایل باید
همراه بکاپ نگهداری شود.

### مرحلهٔ ۴: دریافت ایمیج و اجرا

```bash
cd /opt/WGDashboard
docker compose --env-file docker/.env -f docker/compose.yaml pull
docker compose --env-file docker/.env -f docker/compose.yaml up -d
```

ایمیج‌های پنل و Node از سورس همین مخزن در GitHub Actions ساخته می‌شوند؛ بنابراین
سرور فقط آن‌ها را دانلود می‌کند و درگیر Build سنگین فرانت‌اند و AmneziaWG نیست.

### مرحلهٔ ۵: بررسی سلامت

```bash
docker compose --env-file docker/.env -f docker/compose.yaml ps
docker compose --env-file docker/.env -f docker/compose.yaml logs -f wgdashboard
```

در یک Terminal دیگر:

```bash
curl -fsS http://127.0.0.1:10086/healthz
```

ورود مستقیم:

```text
http://SERVER_IP:10086
```

## انتشار ایمیج‌های اختصاصی

Workflow مسیر `.github/workflows/docker.yml` با هر Push به شاخهٔ `main` دو
ایمیج `linux/amd64` و `linux/arm64` را مستقیماً از سورس همین مخزن می‌سازد:

```text
ghcr.io/hosseinpv1379/wgdashboard:latest
ghcr.io/hosseinpv1379/wg-node:latest
```

برای فعال‌شدن `latest` ابتدا تغییرات شاخهٔ `codex/wireguard` را در `main` Merge
و Push کنید. سپس در تب **Actions** منتظر سبزشدن **Publish container images**
بمانید. در صفحهٔ **Packages** هر دو Package را باز کنید و از
**Package settings → Change visibility** روی **Public** بگذارید تا سرورها بدون
Login بتوانند Pull کنند. اگر Package خصوصی بماند، روی هر سرور با PAT دارای
مجوز `read:packages` وارد شوید:

```bash
echo 'YOUR_GITHUB_PAT' | docker login ghcr.io -u hosseinpv1379 --password-stdin
```

Compose اصلی فقط ایمیج منتشرشده را Pull می‌کند. برای Build آزمایشی از Checkout
فعلی، فایل جداگانهٔ build را اضافه کنید:

```bash
docker compose --env-file docker/.env \
  -f docker/compose.yaml -f docker/compose.build.yaml \
  up -d --build
```

این فرمان فقط برای توسعه است؛ نصب عادی سرور باید با `docker/compose.yaml` انجام
شود.

برای محیط عملیاتی، دامنه و HTTPS را با Nginx یا Caddy جلوی پورت `10086` قرار
دهید. `wg-node` باید از URL امنی مانند `https://panel.example.com` استفاده کند.

---

## دسترسی به دیتابیس با Adminer

Adminer به‌صورت پیش‌فرض فقط روی `127.0.0.1:8080` سرور مرکزی گوش می‌دهد. این
پورت را مستقیماً روی اینترنت باز نکنید.

روی کامپیوتر خودتان SSH Tunnel بسازید:

```bash
ssh -L 8080:127.0.0.1:8080 root@SERVER_IP
```

بعد در مرورگر باز کنید:

```text
http://127.0.0.1:8080
```

اطلاعات ورود:

| فیلد | مقدار |
| --- | --- |
| System | `PostgreSQL` |
| Server | `postgres` |
| Username | مقدار `POSTGRES_USER` |
| Password | مقدار `POSTGRES_PASSWORD` |
| Database | `wgdashboard` |

---

## نصب `wg-node` روی سرور WireGuard

این بخش فقط روی **Nodeهای Remote** تکرار می‌شود. در نصب Docker مرکزی، سرویس
`wg-node-local` به‌طور خودکار ساخته می‌شود، Credential را از Volume پنل
می‌خواند و `wg0` همان سرور پنل را به بخش Nodes اضافه می‌کند؛ بنابراین روی سرور
مرکزی Agent جداگانه نصب نکنید.

### مدل پیشنهادی: Docker

- سیستم‌عامل پیشنهادی: Ubuntu 22.04/24.04 64-bit
- دسترسی `root`
- یک دامنه یا IP عمومی
- پورت UDP اختصاصی، معمولاً `51820`
- دسترسی خروجی HTTPS به پنل مرکزی

### مرحلهٔ ۱: نصب WireGuard

فرمان رسمی Ubuntu در [راهنمای WireGuard](https://www.wireguard.com/install/):

```bash
sudo apt update
sudo apt install -y wireguard iptables
```

Forwarding را فعال کنید:

```bash
sudo tee /etc/sysctl.d/99-wg-node.conf >/dev/null <<'EOF'
net.ipv4.ip_forward=1
EOF
sudo sysctl --system
```

نام کارت شبکهٔ خروجی را پیدا کنید:

```bash
ip route show default
```

در مثال‌های زیر نام کارت `eth0` است. اگر خروجی شما `ens3` یا نام دیگری بود،
قالب را تغییر دهید.

### مرحلهٔ ۲: ساخت کلید سرور

```bash
sudo install -m 700 -d /etc/wireguard
umask 077
wg genkey | sudo tee /etc/wireguard/server.key | \
  wg pubkey | sudo tee /etc/wireguard/server.pub
sudo cat /etc/wireguard/server.pub
```

#### قالب مجزای `/etc/wireguard/wg0.conf`

مقدار `SERVER_PRIVATE_KEY` را از `/etc/wireguard/server.key` جایگزین کنید و نام
`eth0` را با کارت شبکهٔ واقعی عوض کنید:

```ini
[Interface]
Address = 10.88.0.1/24
ListenPort = 51820
PrivateKey = SERVER_PRIVATE_KEY
SaveConfig = true

PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
```

فایل را امن و Interface را فعال کنید:

```bash
sudo chmod 600 /etc/wireguard/wg0.conf /etc/wireguard/server.key
sudo systemctl enable --now wg-quick@wg0
sudo wg show wg0
```

پورت را در Firewall و Firewall ارائه‌دهندهٔ سرور باز کنید:

```bash
sudo ufw allow 51820/udp
```

### مرحلهٔ ۳: Node را در پنل بسازید

در WGDashboard:

1. وارد بخش **Nodes** شوید.
2. در بخش **Add WireGuard node** نام، Region، Endpoint و Capacity را وارد کنید.
3. Endpoint نمونه: `de1.example.com:51820`.
4. روی **Create node** بزنید.
5. `Node ID` و `Node Token` را همان لحظه ذخیره کنید؛ Token دوباره نمایش داده
   نمی‌شود.

### مرحلهٔ ۴: پروژه و تنظیمات Node

اگر Docker روی Node نصب نیست، همان روش رسمی بخش نصب پنل مرکزی را اجرا کنید.

```bash
sudo git clone --branch codex/wireguard --single-branch \
  https://github.com/hosseinpv1379/WGDashboard.git /opt/WGDashboard
cd /opt/WGDashboard/wg-node
cp .env.example .env
nano .env
```

#### قالب ساده و مجزای `wg-node/.env`

```dotenv
WG_PANEL_URL=https://panel.example.com

WG_NODE_ID=NODE_ID_FROM_PANEL
WG_NODE_TOKEN=NODE_TOKEN_FROM_PANEL
WG_NODE_BOOTSTRAP_TOKEN=

WG_NODE_NAME=germany-1
WG_NODE_REGION=Germany
WG_NODE_PUBLIC_ENDPOINT=de1.example.com:51820

WG_NODE_INTERFACE=wg0
WG_NODE_ADDRESS_POOL=10.88.0.0/24
WG_NODE_CAPACITY=500

WG_NODE_POLL_SECONDS=5
WG_NODE_REPORT_SECONDS=15
WG_NODE_PRESHARED_KEY=true
WG_NODE_INSECURE_TLS=false
```

نکات مهم:

- `WG_PANEL_URL` باید آدرس بیرونی پنل باشد. اگر پنل Prefix دارد، Prefix را هم
  بنویسید؛ مثال: `https://panel.example.com/wgd`.
- `WG_NODE_ADDRESS_POOL` باید با شبکهٔ `Address` در `wg0.conf` هماهنگ باشد.
- آدرس `.1` برای Interface رزرو است و Agent از `.2` به بعد اختصاص می‌دهد.
- `WG_NODE_PUBLIC_ENDPOINT` حتماً باید شامل پورت UDP باشد.
- در Production مقدار `WG_NODE_INSECURE_TLS=true` نگذارید.

### مرحلهٔ ۵: اجرای Agent

```bash
cd /opt/WGDashboard/wg-node
docker compose --env-file .env -f compose.example.yaml pull
docker compose --env-file .env -f compose.example.yaml up -d
```

بررسی:

```bash
docker compose --env-file .env -f compose.example.yaml ps
docker compose --env-file .env -f compose.example.yaml logs -f wg-node
sudo wg show wg0
```

حداکثر بعد از حدود یک دقیقه، وضعیت Node در پنل باید `online` شود.

### مدل جایگزین: ثبت خودکار با Bootstrap Token

در سرور مرکزی یک مقدار تصادفی بسازید:

```bash
openssl rand -hex 32
```

آن را در `docker/.env` مرکزی قرار دهید:

```dotenv
WGD_NODE_BOOTSTRAP_TOKEN=LONG_RANDOM_VALUE
```

پنل مرکزی را Recreate کنید:

```bash
cd /opt/WGDashboard
docker compose --env-file docker/.env -f docker/compose.yaml up -d
```

روی Node، `WG_NODE_ID` و `WG_NODE_TOKEN` را خالی و همان مقدار را تنظیم کنید:

```dotenv
WG_NODE_ID=
WG_NODE_TOKEN=
WG_NODE_BOOTSTRAP_TOKEN=LONG_RANDOM_VALUE
```

Agent بعد از ثبت، Credential را در Volume خود ذخیره می‌کند. پس از ثبت همهٔ
Nodeها، Bootstrap Token را از پنل مرکزی خالی کنید.

### مدل جایگزین: فایل باینری مستقل با systemd

`wg-node` یک فایل باینری مستقل است و در زمان اجرا به Go نیاز ندارد. Go فقط روی
ماشینی که Binary را Build می‌کند لازم است:

```bash
cd /opt/WGDashboard/wg-node
CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o wg-node .
sudo install -m 0755 wg-node /usr/local/bin/wg-node
sudo install -m 0700 -d /etc/wg-node/outbounds /var/lib/wg-node
sudo cp config.example.json /etc/wg-node/config.json
sudo cp wg-node.service /etc/systemd/system/wg-node.service
sudo install -m 0600 .env /etc/wg-node.env
sudo chmod 600 /etc/wg-node/config.json
sudo nano /etc/wg-node/config.json
sudo chmod 600 /etc/wg-node.env
sudo systemctl daemon-reload
sudo systemctl enable --now wg-node
sudo journalctl -u wg-node -f
```

فایل‌های این مدل عمداً از هم جدا هستند:

| مسیر | کاربرد |
| --- | --- |
| `/usr/local/bin/wg-node` | Binary اجرایی |
| `/etc/wg-node/config.json` | تنظیمات ثابت و چند Interface |
| `/etc/wg-node.env` | Override و Secretهای محیطی |
| `/var/lib/wg-node/credentials.json` | Credential ثبت خودکار |
| `/var/lib/wg-node/state.json` | Session، Peerها و وضعیت Outbound |
| `/etc/wg-node/outbounds/*.conf` | کانفیگ‌های Sanitized شدهٔ Outbound |

---

## ساخت کاربر و اشتراک چندسروره

پنل تجاری شش بخش مستقل دارد: **Users**، **Nodes**، **Node Groups**،
**Outbounds**، **Packages** و **Subscriptions**. ترتیب استاندارد راه‌اندازی به
شکل زیر است.

### ۱. ثبت Nodeها

در بخش **Nodes** هر سرور WireGuard را با نام، Location، Endpoint و Capacity
ثبت کنید و Token ساخته‌شده را در تنظیمات `wg-node` همان سرور قرار دهید.

### ۲. ساخت Node Group

در بخش **Node Groups**، Node و Interfaceهای قابل فروش را انتخاب کنید. یک Node
می‌تواند `wg0` و `wg1` داشته باشد و هر Target یک فایل کانفیگ مستقل می‌سازد.
مثلاً گروه `Europe Duo` می‌تواند شامل این دو Target باشد:

```text
germany-1 / wg0  → Germany
finland-1 / wg0  → Finland
```

هر پکیجی که به این گروه متصل شود، برای هر اشتراک دو کانفیگ می‌سازد.

اعضای Node Group پویا هستند و فقط روی فروش‌های جدید اثر نمی‌گذارند:

- اضافه‌کردن Node/Interface، برای تمام اشتراک‌های قبلی آن گروه هم Peer و
  کانفیگ جدید می‌سازد.
- حذف Node/Interface، کانفیگ را فوراً از لینک و پنل اشتراک کنار می‌گذارد و Job
  حذف Peer را برای `wg-node` می‌فرستد.
- انتقال یک Package به Node Group دیگر، اشتراک‌های فروخته‌شده از همان Package
  را نیز به Targetهای گروه جدید منتقل می‌کند؛ حجم، مدت و قیمت Snapshot قبلی
  تغییر نمی‌کنند.
- حذف Node آن را از همهٔ گروه‌ها و اشتراک‌ها خارج می‌کند. Node تا پایان پاک‌شدن
  Peerها وضعیت `revoking` دارد و سپس Token آن باطل می‌شود. Node آفلاین بعد از
  اتصال مجدد Cleanup را انجام می‌دهد.

### ۳. ساخت کاربر

از منوی **Users** یک کاربر بسازید. اشتراک تجاری حتماً به یک User متصل می‌شود.
برای کاربر Local می‌توانید **Reset link** بسازید؛ لینک امن ۳۰ دقیقه اعتبار دارد
و مستقیماً فرم تعیین رمز جدید را باز می‌کند. کاربری که سابقهٔ اشتراک دارد برای
حفظ سوابق فروش از پنل قابل حذف نیست.

### ۴. ساخت Package

در بخش **Packages** یک پکیج بسازید:

| فیلد | توضیح |
| --- | --- |
| Package name | نام تجاری؛ مثلاً `30 GB / 30 days` |
| Node group | لوکیشن‌هایی که خریدار دریافت می‌کند |
| Data | حجم مشترک تمام لوکیشن‌ها؛ `0` یعنی نامحدود |
| Duration | مدت اشتراک بر حسب روز؛ `0` یعنی بدون انقضا |
| Price | قیمت فروش |
| Currency | واحد پول؛ مثلاً `IRT`، `USD` یا `EUR` |
| Status | فقط پکیج `active` قابل فروش است |

مثال: پکیج ۳۰ گیگابایت، ۳۰ روزه، ۳۰۰ هزار تومان و متصل به گروه دو لوکیشنی.

### ۵. فروش و ساخت Subscription

در بخش **Subscriptions** فقط User و Package را انتخاب کنید و **Create and
provision** را بزنید. پنل به‌صورت خودکار:

1. حجم، مدت، قیمت و گروه پکیج را به‌عنوان Snapshot فروش ذخیره می‌کند.
2. تاریخ انقضا را از زمان ایجاد به‌علاوهٔ مدت پکیج محاسبه می‌کند.
3. برای تمام Node/Interfaceهای گروه Peer و Job ساخت ایجاد می‌کند.
4. یک لینک Subscription واحد شامل تمام فایل‌های کانفیگ می‌سازد.

اگر بعداً قیمت یا حجم Package تغییر کند، مشخصات اشتراک‌های فروخته‌شدهٔ قبلی
تغییر نمی‌کند. Node آفلاین نیز Job خود را بعد از آنلاین‌شدن دریافت می‌کند.

لینک Subscription فقط هنگام ساخت یا Rotate نمایش داده می‌شود؛ آن را ذخیره کنید.

### ۶. وضعیت ساخت کانفیگ

وضعیت هر Peer ابتدا `provisioning` و پس از اجرای Job توسط `wg-node` برابر
`active` می‌شود. برای اشتراک‌های قدیمی و دستی همچنان امکان انتخاب Node و
Provision دستی وجود دارد. خطای موقت هر Job تا سه بار Retry می‌شود؛ اگر پس از سه
بار همچنان `error` بود، مشکل Node را رفع و دکمهٔ **Retry** همان Peer را بزنید.

Heartbeat علاوه بر Interfaceها، موجودی Peerهای Agent را هم تطبیق می‌دهد. اگر
فایل state یک Node در اثر نصب مجدد از بین برود، پنل Peer فعال را با همان IP و
Public Key و Preshared Key قبلی بازسازی و Outbound فعال را دوباره Apply می‌کند؛
در نتیجه کانفیگ دانلودشدهٔ کاربر عوض نمی‌شود. Peer/Outbound یتیم یا وضعیت اشتباه
enable/disable نیز خودکار اصلاح می‌شود.

### ۷. دریافت کانفیگ‌ها

خروجی JSON:

```text
https://panel.example.com/sub/SUBSCRIPTION_ID.SECRET_TOKEN
```

دانلود ZIP شامل تمام فایل‌های `.conf`:

```text
https://panel.example.com/sub/SUBSCRIPTION_ID.SECRET_TOKEN?format=zip
```

Client پس از ورود به پرتال خودش نیز کانفیگ‌های فعال را می‌بیند.

### زمان برای Peer معمولی

برای Peerهای قدیمی و غیرتجاری نیز فیلد **Expiration time** در فرم ساخت و ویرایش
وجود دارد. پس از رسیدن زمان، Peer محدود می‌شود. خالی‌گذاشتن فیلد یعنی بدون
انقضا.

---

## ساخت Outbound وایرگارد

Outbound باعث می‌شود فقط کاربران یک Interface مشخص، مثلاً `wg0` با شبکهٔ
`10.88.0.0/24`، از یک تونل WireGuard بالادستی خارج شوند. Route پیش‌فرض خود
سرور و ارتباط Agent با پنل تغییر نمی‌کند.

1. در **Nodes** صبر کنید Interface موردنظر توسط Heartbeat نمایش داده شود.
2. وارد **Outbounds** شوید و Node و Source Interface را انتخاب کنید.
3. نام Interface خروجی مانند `wgo0` را وارد کنید.
4. کانفیگ Client وایرگارد بالادستی را Paste کنید؛ `AllowedIPs` باید شامل
   `0.0.0.0/0` باشد.
5. روی **Create and apply** بزنید و رسیدن وضعیت به `active` را بررسی کنید.

برای جلوگیری از اجرای فرمان دلخواه، Agent گزینه‌های `PreUp`، `PostUp`،
`PreDown`، `PostDown`، `DNS`، `Table` و `SaveConfig` ورودی را حذف و
`Table = off` را خودش اعمال می‌کند. Private Key کانفیگ در PostgreSQL به‌صورت
رمزشده ذخیره می‌شود و فقط هنگام تحویل Job به Node رمزگشایی می‌شود.

در Node بررسی کنید:

```bash
sudo wg show wgo0
sudo ip rule show
sudo ip route show table all | grep wgo0
sudo iptables -t nat -S POSTROUTING | grep wgo0
```

نسخهٔ فعلی Outbound فقط Source Poolهای IPv4 را پشتیبانی می‌کند.

---

## مدل زمان و حجم

### زمان

- زمان بر اساس `TZ` کانتینر مرکزی تفسیر می‌شود.
- مقدار پیشنهادی ایران: `TZ=Asia/Tehran`.
- پنل مرکزی حدود هر ۱۵ ثانیه محدودیت اشتراک‌های تجاری را بررسی می‌کند.
- Peer معمولی حدود هر ۱۰ ثانیه بررسی می‌شود.

### حجم مشترک

مصرف تجاری برابر مجموع `RX + TX` است؛ بنابراین دانلود و آپلود هر دو از سهمیه
کم می‌شوند. مقدار نمایشی پنل `GiB` است (`1 GiB = 1024³ bytes`). مثلاً یک اشتراک
`100 GiB` سه کانفیگ دارد:

```text
Germany    20 GiB
Finland    35 GiB
Turkey     45 GiB
----------------
Total     100 GiB
```

پس از رسیدن مجموع به سقف، وضعیت اشتراک `quota_exceeded` می‌شود و برای تمام
Nodeها Job غیرفعال‌سازی ساخته می‌شود. به‌اندازهٔ فاصلهٔ گزارش‌ها ممکن است مقدار
کمی مصرف اضافه ثبت شود. گزارش تکراری دوباره حساب نمی‌شود، قطع یک گزارش با
counter تجمعی بعدی جبران می‌شود، کاهش counter پس از Restart یک بار محاسبه
می‌شود و **Reset usage** baseline فعلی را حفظ می‌کند تا ترافیک قدیمی دوباره
وارد سهمیه نشود.

برای جلوگیری از رشد بی‌حد دیتابیس، از هر Peer و Session فقط آخرین گزارش خام
نگه داشته می‌شود؛ مقدار تجمیعی دقیق در Peer و Subscription باقی می‌ماند و از هر
Session قدیمی نیز یک رکورد برای جلوگیری از replay حفظ می‌شود.

### وضعیت‌ها

| وضعیت | معنی |
| --- | --- |
| `active` | اشتراک یا Peer فعال است |
| `provisioning` | Job ساخت هنوز کامل نشده |
| `updating` | فرمان فعال/غیرفعال‌سازی در صف است |
| `disabled` | توسط مدیر غیرفعال شده |
| `expired` | زمان اشتراک تمام شده |
| `quota_exceeded` | حجم مشترک تمام شده |
| `error` | اجرای Job روی Node خطا داده |
| `deleted` | Peer حذف شده است |

---

## بکاپ، به‌روزرسانی و بازگردانی

### مواردی که باید بکاپ شوند

```text
PostgreSQL dump
/data
/data/subscription.key
/etc/wireguard
/etc/amnezia/amneziawg
docker/.env
```

> [!CAUTION]
> بدون `/data/subscription.key`، Private Keyهای رمزنگاری‌شدهٔ Subscriptionها
> پس از Restore قابل خواندن نیستند.

### به‌روزرسانی عادی

ابتدا بکاپ بخش ارتقا را بگیرید، سپس:

```bash
cd /opt/WGDashboard
git pull --ff-only origin codex/wireguard
docker compose --env-file docker/.env -f docker/compose.yaml pull
docker compose --env-file docker/.env -f docker/compose.yaml up -d --remove-orphans
```

به‌روزرسانی Agent روی هر Node:

```bash
cd /opt/WGDashboard
git pull --ff-only origin codex/wireguard
cd wg-node
docker compose --env-file .env -f compose.example.yaml pull
docker compose --env-file .env -f compose.example.yaml up -d
```

### بازگردانی PostgreSQL

قبل از Restore، نسخهٔ فعلی را هم Dump کنید. سپس پنل را متوقف کنید:

```bash
cd /opt/WGDashboard
docker compose --env-file docker/.env -f docker/compose.yaml stop wgdashboard

docker compose --env-file docker/.env -f docker/compose.yaml \
  exec -T postgres sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  < /root/wgdashboard-backup-YYYY-MM-DD-HHMMSS/wgdashboard.sql

docker compose --env-file docker/.env -f docker/compose.yaml start wgdashboard
```

اگر Restore روی دیتابیس غیرخالی خطای Duplicate داد، ابتدا وضعیت را بررسی کنید؛
بدون بکاپ، جدول‌ها یا Volume را حذف نکنید.

---

## عیب‌یابی

### پنل باز نمی‌شود: `ERR_CONNECTION_REFUSED`

```bash
cd /opt/WGDashboard
docker compose --env-file docker/.env -f docker/compose.yaml ps
docker compose --env-file docker/.env -f docker/compose.yaml logs --tail=200 wgdashboard
sudo ss -lntp | grep 10086
curl -fsS http://127.0.0.1:10086/healthz
```

اگر روی localhost پاسخ می‌دهد ولی از بیرون باز نمی‌شود، Firewall یا Security
Group ارائه‌دهنده را بررسی کنید.

### پنل باز می‌شود ولی روی Loading می‌ماند

نسخهٔ فعلی برای HTML اصلی Header ضدکش می‌فرستد، درخواست‌های Frontend پس از ۲۰
ثانیه Timeout می‌شوند و احراز هویت redirect حلقه‌ای ایجاد نمی‌کند. بعد از ارتقا:

```bash
curl -fsS http://127.0.0.1:10086/healthz
curl -I http://127.0.0.1:10086/
docker compose --env-file docker/.env -f docker/compose.yaml logs --tail=200 wgdashboard
```

پاسخ `healthz` باید `web: ok` و `database: ok` داشته باشد و Header صفحهٔ اصلی
باید شامل `Cache-Control: no-store` باشد. سپس یک بار Hard Refresh مرورگر انجام
دهید (`Ctrl+Shift+R` یا `Cmd+Shift+R`). اگر Reverse Proxy دارید، کش HTML را در
Nginx/Cloudflare نیز Purge کنید؛ فایل‌های Asset هش‌دار هستند و HTML قدیمی ممکن
است به Bundle حذف‌شده اشاره کند.

### خطای قدیمی Build فرانت‌اند روی `proxy.js`

نسخهٔ فعلی دیگر به فایل `proxy.js` وابسته نیست و روی سرور Build انجام نمی‌شود.
آخرین ایمیج منتشرشده را دریافت کنید:

```bash
cd /opt/WGDashboard
git pull --ff-only origin codex/wireguard
docker compose --env-file docker/.env -f docker/compose.yaml pull wgdashboard
docker compose --env-file docker/.env -f docker/compose.yaml up -d wgdashboard
```

### PostgreSQL ناسالم است

```bash
docker compose --env-file docker/.env -f docker/compose.yaml logs --tail=200 postgres
docker compose --env-file docker/.env -f docker/compose.yaml \
  exec postgres pg_isready -U wgdashboard -d wgdashboard
```

رمز `POSTGRES_PASSWORD` را بعد از ساخته‌شدن Volume بدون برنامهٔ مهاجرت تغییر
ندهید؛ مقدار ذخیره‌شدهٔ دیتابیس با تغییر سادهٔ `.env` عوض نمی‌شود.

### Node در حالت Offline است

روی Node:

```bash
cd /opt/WGDashboard/wg-node
docker compose --env-file .env -f compose.example.yaml ps
docker compose --env-file .env -f compose.example.yaml logs --tail=200 wg-node
curl -i https://panel.example.com/api/node/v1/jobs
sudo wg show wg0
```

پاسخ `401 Unauthorized` از فرمان `curl` بدون Token طبیعی است و نشان می‌دهد مسیر
شبکه تا پنل برقرار است.

موارد زیر را بررسی کنید:

- `WG_PANEL_URL` درست و دارای HTTPS معتبر باشد.
- ساعت سیستم مرکزی و Node درست باشد.
- `WG_NODE_ID` و `WG_NODE_TOKEN` فاصله یا خط جدید نداشته باشند.
- Interface نوشته‌شده در `WG_NODE_INTERFACE` واقعاً فعال باشد.
- گواهی خصوصی فقط با `WG_NODE_CA_CERT` معرفی شود؛ بررسی TLS را غیرفعال نکنید.

### Job در وضعیت Error است

```bash
sudo wg show wg0
sudo wg-quick save wg0
docker compose --env-file .env -f compose.example.yaml logs --tail=200 wg-node
```

خطاهای متداول:

- Interface وجود ندارد.
- Address Pool با `wg0.conf` هماهنگ نیست.
- Public Endpoint پورت ندارد.
- `/etc/wireguard` قابل نوشتن نیست.
- ظرفیت Node تمام شده است.

Agent هر خطای اجرایی را با فاصله تا سه بار تکرار می‌کند. پس از رفع علت، در
**Subscriptions** روی **Retry** بزنید. حذف دستی `state.json` لازم نیست؛ Inventory
Heartbeat وضعیت از‌دست‌رفته را از اطلاعات پنل بازسازی می‌کند.

### اشتراک بعد از ساخت فایل ندارد

- حداقل یک Node آنلاین انتخاب کنید.
- `Maximum servers` باید از تعداد Nodeهای انتخاب‌شده کمتر نباشد.
- Peer باید به وضعیت `active` برسد.
- لاگ Agent را برای خطای Job بررسی کنید.

### Adminer از بیرون باز نمی‌شود

این رفتار پیش‌فرض و عمدی است. Adminer فقط روی localhost قرار دارد. SSH Tunnel
بخش Adminer را استفاده کنید؛ `DB_ADMIN_BIND=0.0.0.0` برای اینترنت عمومی توصیه
نمی‌شود.

---

## فرمان‌های روزمره

### پنل مرکزی

```bash
cd /opt/WGDashboard

# وضعیت
docker compose --env-file docker/.env -f docker/compose.yaml ps

# لاگ زنده
docker compose --env-file docker/.env -f docker/compose.yaml logs -f wgdashboard wg-node-local

# Restart
docker compose --env-file docker/.env -f docker/compose.yaml restart wgdashboard

# دریافت و اجرای آخرین ایمیج
docker compose --env-file docker/.env -f docker/compose.yaml pull
docker compose --env-file docker/.env -f docker/compose.yaml up -d

# توقف بدون حذف اطلاعات
docker compose --env-file docker/.env -f docker/compose.yaml down
```

### Node

```bash
cd /opt/WGDashboard/wg-node

docker compose --env-file .env -f compose.example.yaml ps
docker compose --env-file .env -f compose.example.yaml logs -f wg-node
docker compose --env-file .env -f compose.example.yaml restart wg-node
sudo wg show wg0
```

> [!WARNING]
> برای پنل مرکزی یا Node از `down -v` استفاده نکنید، مگر اینکه عمداً قصد حذف
> تمام داده‌های Persist‌شده را داشته باشید و بکاپ معتبر گرفته باشید.

---

## پرسش‌های متداول

### من پنل را از قبل نصب کرده‌ام؛ الان دقیقاً چه کار کنم؟

به‌ترتیب این چهار کار را انجام دهید:

1. نوع دیتابیس و نام Volumeهای فعلی را ثبت کنید.
2. از `.env`، `/data`، WireGuard و PostgreSQL بکاپ بگیرید.
3. گزینه‌های جدید را بدون جایگزین‌کردن `.env` به آن اضافه کنید.
4. `git pull` و سپس `docker compose ... pull` و `docker compose ... up -d` را
   اجرا کنید.

اجرای `down -v` یا انتخاب Volume جدید باعث می‌شود پنل خالی به‌نظر برسد، حتی اگر
Volume قدیمی هنوز روی دیسک وجود داشته باشد.

### آیا روی هر Node باید خود WGDashboard نصب شود؟

خیر. فقط سرور مرکزی WGDashboard، PostgreSQL و Adminer دارد. هر سرور VPN فقط
WireGuard و Binary مربوط به `wg-node` لازم دارد. در خود سرور مرکزی این Agent
توسط Compose و با نام `wg-node-local` به‌صورت خودکار اجرا می‌شود.

### آیا Node به پورت API ورودی نیاز دارد؟

خیر. Agent با HTTPS به پنل مرکزی وصل می‌شود. فقط پورت UDP سرویس WireGuard برای
کاربران باز می‌شود.

### آیا می‌توان چند Node از یک شبکهٔ خصوصی یکسان استفاده کنند؟

بله، چون هر Node Interface مستقل دارد. روی یک Node نباید دو Peer آدرس یکسان
داشته باشند؛ Agent این موضوع را کنترل می‌کند.

### حجم بین سرورها مشترک است؟

بله. پنل اختلاف شمارنده‌های هر Peer را جمع می‌کند و مجموع را روی Subscription
می‌نویسد.

### Token اشتراک یا Node گم شده است؛ چه کار کنم؟

- برای Subscription از گزینهٔ **Rotate link** استفاده کنید؛ لینک قبلی باطل
  می‌شود.
- Token یک Node قابل بازیابی نیست. Node را Revoke و یک Node/Token جدید بسازید.

### چرا بعد از پایان زمان چند ثانیه اتصال باقی می‌ماند؟

بررسی‌ها دوره‌ای هستند و Agent نیز Jobها را Poll می‌کند. این تأخیر کوتاه طبیعی
است. قطع فوری و کاملاً هم‌زمان بین چند دیتاسنتر به ارتباط Streaming نیاز دارد و
در نسخهٔ فعلی استفاده نمی‌شود.

### آیا Failover و انتخاب خودکار بهترین Node وجود دارد؟

در نسخهٔ فعلی انتخاب Node توسط مدیر انجام می‌شود. Heartbeat، ظرفیت، وضعیت آنلاین،
حجم مشترک و قطع گروهی پیاده‌سازی شده‌اند؛ Failover خودکار و Load Balancing جزو
نسخهٔ فعلی نیستند.

---

## منابع

- [Docker Engine روی Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [نصب رسمی WireGuard](https://www.wireguard.com/install/)
- [الگوی ساختاری مستندات XMPlus](https://docs.xmplus.dev/)
- [مخزن این پروژه](https://github.com/hosseinpv1379/WGDashboard)
