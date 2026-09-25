"""Commercial subscriptions and the multi-node WireGuard control plane."""

import hashlib
import ipaddress
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

import sqlalchemy as db
from cryptography.fernet import Fernet, InvalidToken

from .DatabaseConnection import CreateEngine
from .Utilities import GenerateWireguardPrivateKey, GenerateWireguardPublicKey, ParseOptionalDateTime


class CommercialSubscriptions:
    NODE_OFFLINE_AFTER_SECONDS = 60
    JOB_LEASE_SECONDS = 30
    INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_=+.-]{1,64}$")
    WIREGUARD_KEY_PATTERN = re.compile(r"^[A-Za-z0-9+/]{43}=$")

    def __init__(self):
        self.engine = CreateEngine("wgdashboard")
        self.metadata = db.MetaData()
        self._define_tables()
        self.metadata.create_all(self.engine)
        self.fernet = Fernet(self._load_encryption_key())

    def _define_tables(self):
        self.nodes = db.Table(
            "CommercialNodes", self.metadata,
            db.Column("NodeID", db.String(36), primary_key=True),
            db.Column("Name", db.String(255), nullable=False),
            db.Column("Region", db.String(255), nullable=False, server_default=""),
            db.Column("PublicEndpoint", db.String(500), nullable=False, server_default=""),
            db.Column("TokenHash", db.String(64), nullable=False, unique=True),
            db.Column("Status", db.String(32), nullable=False, server_default="offline"),
            db.Column("LastSeenAt", db.DateTime),
            db.Column("Capacity", db.Integer, nullable=False, server_default="0"),
            db.Column("AgentVersion", db.String(64)),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.Column("RevokedAt", db.DateTime),
        )

        self.subscriptions = db.Table(
            "CommercialSubscriptions", self.metadata,
            db.Column("SubscriptionID", db.String(36), primary_key=True),
            db.Column("ClientID", db.String(255), nullable=False, index=True),
            db.Column("Name", db.String(255), nullable=False),
            db.Column("Status", db.String(32), nullable=False, server_default="active"),
            db.Column("QuotaBytes", db.BigInteger, nullable=False, server_default="0"),
            db.Column("UsedBytes", db.BigInteger, nullable=False, server_default="0"),
            db.Column("ExpiresAt", db.DateTime),
            db.Column("MaxPeers", db.Integer, nullable=False, server_default="1"),
            db.Column("TokenHash", db.String(64), nullable=False),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.Column("UpdatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.Column("DisabledAt", db.DateTime),
        )

        self.subscription_peers = db.Table(
            "CommercialSubscriptionPeers", self.metadata,
            db.Column("SubscriptionPeerID", db.String(36), primary_key=True),
            db.Column("SubscriptionID", db.String(36), nullable=False, index=True),
            db.Column("NodeID", db.String(36), nullable=False, index=True),
            db.Column("RemotePeerID", db.String(255), nullable=False),
            db.Column("Name", db.String(255), nullable=False),
            db.Column("InterfaceName", db.String(64), nullable=False, server_default="wg0"),
            db.Column("Address", db.String(255)),
            db.Column("ClientPublicKey", db.String(255), nullable=False),
            db.Column("ClientPrivateKeyEncrypted", db.Text, nullable=False),
            db.Column("ServerPublicKey", db.String(255)),
            db.Column("PresharedKeyEncrypted", db.Text),
            db.Column("Endpoint", db.String(500)),
            db.Column("DNS", db.String(500), nullable=False, server_default="1.1.1.1"),
            db.Column("MTU", db.Integer, nullable=False, server_default="1420"),
            db.Column("AllowedIPs", db.String(500), nullable=False, server_default="0.0.0.0/0, ::/0"),
            db.Column("Status", db.String(32), nullable=False, server_default="provisioning"),
            db.Column("UsedBytes", db.BigInteger, nullable=False, server_default="0"),
            db.Column("LastRxBytes", db.BigInteger, nullable=False, server_default="0"),
            db.Column("LastTxBytes", db.BigInteger, nullable=False, server_default="0"),
            db.Column("LastSequence", db.BigInteger, nullable=False, server_default="0"),
            db.Column("LastSessionID", db.String(64)),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.Column("UpdatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
        )

        self.node_jobs = db.Table(
            "CommercialNodeJobs", self.metadata,
            db.Column("JobID", db.String(36), primary_key=True),
            db.Column("NodeID", db.String(36), nullable=False, index=True),
            db.Column("SubscriptionID", db.String(36), index=True),
            db.Column("SubscriptionPeerID", db.String(36), index=True),
            db.Column("Operation", db.String(32), nullable=False),
            db.Column("Payload", db.Text, nullable=False),
            db.Column("Status", db.String(32), nullable=False, server_default="pending"),
            db.Column("Attempts", db.Integer, nullable=False, server_default="0"),
            db.Column("IdempotencyKey", db.String(255), nullable=False, unique=True),
            db.Column("LeaseUntil", db.DateTime),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.Column("CompletedAt", db.DateTime),
            db.Column("ErrorMessage", db.Text),
        )

        self.traffic_reports = db.Table(
            "CommercialTrafficReports", self.metadata,
            db.Column("ReportID", db.String(36), primary_key=True),
            db.Column("NodeID", db.String(36), nullable=False, index=True),
            db.Column("SubscriptionPeerID", db.String(36), nullable=False, index=True),
            db.Column("SessionID", db.String(64), nullable=False),
            db.Column("SequenceNumber", db.BigInteger, nullable=False),
            db.Column("RxBytes", db.BigInteger, nullable=False),
            db.Column("TxBytes", db.BigInteger, nullable=False),
            db.Column("DeltaBytes", db.BigInteger, nullable=False),
            db.Column("ReportedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.UniqueConstraint(
                "NodeID", "SubscriptionPeerID", "SessionID", "SequenceNumber",
                name="uq_commercial_traffic_report",
            ),
        )

    @staticmethod
    def _hash_token(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _row(row):
        if row is None:
            return None
        result = dict(row)
        for key, value in list(result.items()):
            if isinstance(value, datetime):
                result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        return result

    def _load_encryption_key(self):
        configured = os.getenv("WGD_SUBSCRIPTION_ENCRYPTION_KEY", "").strip()
        if configured:
            return configured.encode("ascii")

        configuration_path = os.getenv("CONFIGURATION_PATH", ".")
        os.makedirs(configuration_path, exist_ok=True)
        key_path = os.path.join(configuration_path, "subscription.key")
        if os.path.exists(key_path):
            with open(key_path, "rb") as key_file:
                return key_file.read().strip()

        key = Fernet.generate_key()
        try:
            file_descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Another process may have created the shared key between the
            # existence check and O_EXCL. Always use the persisted winner.
            with open(key_path, "rb") as key_file:
                return key_file.read().strip()
        with os.fdopen(file_descriptor, "wb") as key_file:
            key_file.write(key)
        return key

    def _encrypt(self, value):
        if value is None or value == "":
            return None
        return self.fernet.encrypt(str(value).encode("utf-8")).decode("ascii")

    def _decrypt(self, value):
        if not value:
            return ""
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Unable to decrypt subscription secret") from exc

    def create_node(self, name, region="", public_endpoint="", capacity=0):
        if not str(name or "").strip():
            raise ValueError("Node name is required")
        try:
            capacity = max(0, int(capacity or 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Node capacity must be a number") from exc

        node_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(36)
        with self.engine.begin() as conn:
            conn.execute(self.nodes.insert().values(
                NodeID=node_id,
                Name=str(name).strip(),
                Region=str(region or "").strip(),
                PublicEndpoint=str(public_endpoint or "").strip(),
                Capacity=capacity,
                TokenHash=self._hash_token(token),
                Status="offline",
            ))
        return {"node_id": node_id, "agent_token": token}

    def list_nodes(self):
        now = datetime.now()
        with self.engine.connect() as conn:
            rows = conn.execute(
                self.nodes.select().where(self.nodes.c.RevokedAt.is_(None)).order_by(self.nodes.c.CreatedAt)
            ).mappings().fetchall()
        result = []
        for row in rows:
            item = self._row(row)
            last_seen = row.get("LastSeenAt")
            item["Status"] = (
                "online" if last_seen and (now - last_seen).total_seconds() <= self.NODE_OFFLINE_AFTER_SECONDS
                else "offline"
            )
            item.pop("TokenHash", None)
            result.append(item)
        return result

    def revoke_node(self, node_id):
        with self.engine.begin() as conn:
            result = conn.execute(
                self.nodes.update().values(Status="revoked", RevokedAt=datetime.now()).where(
                    db.and_(self.nodes.c.NodeID == node_id, self.nodes.c.RevokedAt.is_(None))
                )
            )
        return result.rowcount == 1

    def authenticate_node(self, token):
        if not token:
            return None
        token_hash = self._hash_token(token)
        with self.engine.connect() as conn:
            return conn.execute(
                self.nodes.select().where(
                    db.and_(self.nodes.c.TokenHash == token_hash, self.nodes.c.RevokedAt.is_(None))
                )
            ).mappings().fetchone()

    def heartbeat(self, node_id, payload):
        with self.engine.begin() as conn:
            conn.execute(
                self.nodes.update().values(
                    Status="online",
                    LastSeenAt=datetime.now(),
                    AgentVersion=str(payload.get("agent_version", ""))[:64],
                    PublicEndpoint=str(payload.get("public_endpoint", ""))[:500]
                    or self.nodes.c.PublicEndpoint,
                    Capacity=max(0, int(payload.get("capacity", 0) or 0)),
                ).where(self.nodes.c.NodeID == node_id)
            )

    def create_subscription(self, client_id, name, quota_gb=0, expires_at=None, max_peers=1):
        if not client_id:
            raise ValueError("Client is required")
        if not str(name or "").strip():
            raise ValueError("Subscription name is required")
        try:
            quota_gb = float(quota_gb or 0)
            max_peers = int(max_peers or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("Quota and maximum peers must be valid numbers") from exc
        if quota_gb < 0 or max_peers < 1:
            raise ValueError("Quota cannot be negative and maximum peers must be at least one")

        expires_at = ParseOptionalDateTime(expires_at)
        subscription_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(36)
        with self.engine.begin() as conn:
            conn.execute(self.subscriptions.insert().values(
                SubscriptionID=subscription_id,
                ClientID=client_id,
                Name=str(name).strip(),
                Status="active",
                QuotaBytes=int(quota_gb * 1024 * 1024 * 1024),
                UsedBytes=0,
                ExpiresAt=expires_at,
                MaxPeers=max_peers,
                TokenHash=self._hash_token(token),
                UpdatedAt=datetime.now(),
            ))
        return {
            "subscription_id": subscription_id,
            "subscription_token": token,
            "subscription_path": f"/sub/{subscription_id}.{token}",
        }

    def list_subscriptions(self, client_id=None, include_configs=False):
        query = self.subscriptions.select().order_by(self.subscriptions.c.CreatedAt.desc())
        if client_id:
            query = query.where(self.subscriptions.c.ClientID == client_id)
        with self.engine.connect() as conn:
            subscriptions = conn.execute(query).mappings().fetchall()
            result = []
            for subscription in subscriptions:
                item = self._row(subscription)
                item.pop("TokenHash", None)
                peers = conn.execute(
                    db.select(
                        self.subscription_peers,
                        self.nodes.c.Name.label("NodeName"),
                        self.nodes.c.Region.label("NodeRegion"),
                    ).select_from(
                        self.subscription_peers.outerjoin(
                            self.nodes, self.subscription_peers.c.NodeID == self.nodes.c.NodeID
                        )
                    ).where(self.subscription_peers.c.SubscriptionID == subscription["SubscriptionID"])
                ).mappings().fetchall()
                item["Peers"] = []
                for peer in peers:
                    peer_item = self._row(peer)
                    peer_item.pop("ClientPrivateKeyEncrypted", None)
                    peer_item.pop("PresharedKeyEncrypted", None)
                    if include_configs and peer["Status"] == "active":
                        peer_item["Configuration"] = self._build_configuration(peer)
                    item["Peers"].append(peer_item)
                item["QuotaGB"] = round(int(subscription["QuotaBytes"] or 0) / (1024 ** 3), 4)
                item["UsedGB"] = round(int(subscription["UsedBytes"] or 0) / (1024 ** 3), 4)
                result.append(item)
        return result

    def update_subscription(self, subscription_id, data):
        values = {"UpdatedAt": datetime.now()}
        if "name" in data:
            if not str(data.get("name") or "").strip():
                raise ValueError("Subscription name is required")
            values["Name"] = str(data["name"]).strip()
        if "quota_gb" in data:
            quota_gb = float(data.get("quota_gb") or 0)
            if quota_gb < 0:
                raise ValueError("Quota cannot be negative")
            values["QuotaBytes"] = int(quota_gb * 1024 ** 3)
        if "expires_at" in data:
            values["ExpiresAt"] = ParseOptionalDateTime(data.get("expires_at"))
        if "max_peers" in data:
            max_peers = int(data.get("max_peers") or 1)
            if max_peers < 1:
                raise ValueError("Maximum peers must be at least one")
            values["MaxPeers"] = max_peers
        if data.get("status") in {"active", "disabled"}:
            values["Status"] = data["status"]
            values["DisabledAt"] = None if data["status"] == "active" else datetime.now()
        if data.get("reset_usage") is True:
            values["UsedBytes"] = 0

        with self.engine.begin() as conn:
            existing = conn.execute(
                self.subscriptions.select().where(
                    self.subscriptions.c.SubscriptionID == subscription_id
                )
            ).mappings().fetchone()
            if not existing:
                return False
            result = conn.execute(
                self.subscriptions.update().values(**values).where(
                    self.subscriptions.c.SubscriptionID == subscription_id
                )
            )
            if data.get("reset_usage") is True:
                conn.execute(
                    self.subscription_peers.update().values(
                        UsedBytes=0, UpdatedAt=datetime.now()
                    ).where(self.subscription_peers.c.SubscriptionID == subscription_id)
                )

            desired_status = values.get("Status")
            if desired_status in {"active", "disabled"}:
                target_peer_status = "disabled" if desired_status == "active" else "active"
                operation = "ENABLE_PEER" if desired_status == "active" else "DISABLE_PEER"
                peers = conn.execute(
                    self.subscription_peers.select().where(
                        db.and_(
                            self.subscription_peers.c.SubscriptionID == subscription_id,
                            self.subscription_peers.c.Status == target_peer_status,
                        )
                    )
                ).mappings().fetchall()
                for peer in peers:
                    self._queue_job(
                        conn, peer["NodeID"], operation,
                        {
                            "subscription_peer_id": peer["SubscriptionPeerID"],
                            "interface": peer["InterfaceName"],
                            "public_key": peer["ClientPublicKey"],
                            "address": peer["Address"],
                        },
                        subscription_id=subscription_id,
                        subscription_peer_id=peer["SubscriptionPeerID"],
                        idempotency_key=f"subscription:{operation.lower()}:{peer['SubscriptionPeerID']}:{uuid.uuid4()}",
                    )
                    conn.execute(
                        self.subscription_peers.update().values(
                            Status="updating", UpdatedAt=datetime.now()
                        ).where(self.subscription_peers.c.SubscriptionPeerID == peer["SubscriptionPeerID"])
                    )
        self.enforce_limits()
        return True

    def rotate_subscription_token(self, subscription_id):
        token = secrets.token_urlsafe(36)
        with self.engine.begin() as conn:
            result = conn.execute(
                self.subscriptions.update().values(
                    TokenHash=self._hash_token(token), UpdatedAt=datetime.now()
                ).where(self.subscriptions.c.SubscriptionID == subscription_id)
            )
        if result.rowcount != 1:
            return None
        return {
            "subscription_token": token,
            "subscription_path": f"/sub/{subscription_id}.{token}",
        }

    def _queue_job(self, conn, node_id, operation, payload, subscription_id=None,
                   subscription_peer_id=None, idempotency_key=None):
        idempotency_key = idempotency_key or str(uuid.uuid4())
        existing = conn.execute(
            db.select(self.node_jobs.c.JobID).where(self.node_jobs.c.IdempotencyKey == idempotency_key)
        ).fetchone()
        if existing:
            return existing[0]
        job_id = str(uuid.uuid4())
        conn.execute(self.node_jobs.insert().values(
            JobID=job_id,
            NodeID=node_id,
            SubscriptionID=subscription_id,
            SubscriptionPeerID=subscription_peer_id,
            Operation=operation,
            Payload=json.dumps(payload),
            Status="pending",
            Attempts=0,
            IdempotencyKey=idempotency_key,
        ))
        return job_id

    def provision(self, subscription_id, node_ids, interface_name="wg0", dns="1.1.1.1",
                  mtu=1420, allowed_ips="0.0.0.0/0, ::/0"):
        if not isinstance(node_ids, list) or not node_ids:
            raise ValueError("At least one node is required")
        if not self.INTERFACE_PATTERN.fullmatch(str(interface_name or "")):
            raise ValueError("WireGuard interface name is invalid")
        try:
            mtu = int(mtu or 1420)
        except (TypeError, ValueError) as exc:
            raise ValueError("MTU must be a number") from exc
        if mtu < 576 or mtu > 1500:
            raise ValueError("MTU must be between 576 and 1500")
        try:
            for allowed_ip in str(allowed_ips).split(","):
                ipaddress.ip_network(allowed_ip.strip(), strict=False)
        except ValueError as exc:
            raise ValueError("Allowed IPs are invalid") from exc

        self.enforce_limits()
        with self.engine.begin() as conn:
            subscription = conn.execute(
                self.subscriptions.select().where(self.subscriptions.c.SubscriptionID == subscription_id)
            ).mappings().fetchone()
            if not subscription:
                raise ValueError("Subscription does not exist")
            if subscription["Status"] != "active":
                raise ValueError(f"Subscription is {subscription['Status']}")
            if subscription["ExpiresAt"] and subscription["ExpiresAt"] <= datetime.now():
                raise ValueError("Subscription is expired")
            if (int(subscription["QuotaBytes"] or 0) > 0
                    and int(subscription["UsedBytes"] or 0) >= int(subscription["QuotaBytes"])):
                raise ValueError("Subscription quota is exhausted")
            current_count = conn.execute(
                db.select(db.func.count()).select_from(self.subscription_peers).where(
                    db.and_(
                        self.subscription_peers.c.SubscriptionID == subscription_id,
                        self.subscription_peers.c.Status != "deleted",
                    )
                )
            ).scalar_one()
            unique_node_ids = list(dict.fromkeys(node_ids))
            if current_count + len(unique_node_ids) > int(subscription["MaxPeers"]):
                raise ValueError("Subscription maximum peer count would be exceeded")

            created = []
            for node_id in unique_node_ids:
                node = conn.execute(
                    self.nodes.select().where(
                        db.and_(self.nodes.c.NodeID == node_id, self.nodes.c.RevokedAt.is_(None))
                    )
                ).mappings().fetchone()
                if not node:
                    raise ValueError(f"Node does not exist: {node_id}")
                if (not node["LastSeenAt"] or
                        (datetime.now() - node["LastSeenAt"]).total_seconds()
                        > self.NODE_OFFLINE_AFTER_SECONDS):
                    raise ValueError(f"Node is offline: {node['Name']}")
                node_peer_count = conn.execute(
                    db.select(db.func.count()).select_from(self.subscription_peers).where(
                        db.and_(
                            self.subscription_peers.c.NodeID == node_id,
                            self.subscription_peers.c.Status != "deleted",
                        )
                    )
                ).scalar_one()
                if int(node["Capacity"] or 0) > 0 and node_peer_count >= int(node["Capacity"]):
                    raise ValueError(f"Node capacity is exhausted: {node['Name']}")
                duplicate = conn.execute(
                    self.subscription_peers.select().where(
                        db.and_(
                            self.subscription_peers.c.SubscriptionID == subscription_id,
                            self.subscription_peers.c.NodeID == node_id,
                            self.subscription_peers.c.Status != "deleted",
                        )
                    )
                ).fetchone()
                if duplicate:
                    raise ValueError("This subscription already has a peer on one of the selected nodes")

                private_status, private_key = GenerateWireguardPrivateKey()
                if not private_status:
                    raise RuntimeError("Unable to generate WireGuard private key")
                public_status, public_key = GenerateWireguardPublicKey(private_key)
                if not public_status:
                    raise RuntimeError("Unable to generate WireGuard public key")

                peer_id = str(uuid.uuid4())
                name = f"{subscription['Name']}-{node['Name']}"
                conn.execute(self.subscription_peers.insert().values(
                    SubscriptionPeerID=peer_id,
                    SubscriptionID=subscription_id,
                    NodeID=node_id,
                    RemotePeerID=public_key,
                    Name=name,
                    InterfaceName=interface_name,
                    ClientPublicKey=public_key,
                    ClientPrivateKeyEncrypted=self._encrypt(private_key),
                    DNS=dns,
                    MTU=mtu,
                    AllowedIPs=allowed_ips,
                    Status="provisioning",
                    UpdatedAt=datetime.now(),
                ))
                job_id = self._queue_job(
                    conn, node_id, "CREATE_PEER",
                    {
                        "subscription_peer_id": peer_id,
                        "interface": interface_name,
                        "peer_name": name,
                        "public_key": public_key,
                    },
                    subscription_id=subscription_id,
                    subscription_peer_id=peer_id,
                    idempotency_key=f"create:{peer_id}",
                )
                created.append({"subscription_peer_id": peer_id, "job_id": job_id})
        return created

    def queue_peer_action(self, subscription_peer_id, operation):
        operation = str(operation or "").upper()
        if operation not in {"ENABLE_PEER", "DISABLE_PEER", "DELETE_PEER"}:
            raise ValueError("Unsupported peer operation")
        with self.engine.begin() as conn:
            peer = conn.execute(
                self.subscription_peers.select().where(
                    self.subscription_peers.c.SubscriptionPeerID == subscription_peer_id
                )
            ).mappings().fetchone()
            if not peer:
                raise ValueError("Subscription peer does not exist")
            if operation == "ENABLE_PEER":
                subscription = conn.execute(
                    self.subscriptions.select().where(
                        self.subscriptions.c.SubscriptionID == peer["SubscriptionID"]
                    )
                ).mappings().fetchone()
                if not subscription or subscription["Status"] != "active":
                    raise ValueError("Activate the subscription before enabling this peer")
            job_id = self._queue_job(
                conn, peer["NodeID"], operation,
                {
                    "subscription_peer_id": subscription_peer_id,
                    "interface": peer["InterfaceName"],
                    "public_key": peer["ClientPublicKey"],
                    "address": peer["Address"],
                },
                subscription_id=peer["SubscriptionID"],
                subscription_peer_id=subscription_peer_id,
                idempotency_key=f"{operation.lower()}:{subscription_peer_id}:{uuid.uuid4()}",
            )
            conn.execute(
                self.subscription_peers.update().values(
                    Status="deleting" if operation == "DELETE_PEER" else "updating",
                    UpdatedAt=datetime.now(),
                ).where(self.subscription_peers.c.SubscriptionPeerID == subscription_peer_id)
            )
        return job_id

    def lease_jobs(self, node_id, limit=20):
        now = datetime.now()
        limit = max(1, min(int(limit or 20), 100))
        with self.engine.begin() as conn:
            query = self.node_jobs.select().where(
                db.and_(
                    self.node_jobs.c.NodeID == node_id,
                    db.or_(
                        self.node_jobs.c.Status == "pending",
                        db.and_(self.node_jobs.c.Status == "leased", self.node_jobs.c.LeaseUntil < now),
                    ),
                )
            ).order_by(self.node_jobs.c.CreatedAt).limit(limit)
            if self.engine.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            jobs = conn.execute(query).mappings().fetchall()
            result = []
            for job in jobs:
                conn.execute(
                    self.node_jobs.update().values(
                        Status="leased",
                        LeaseUntil=now + timedelta(seconds=self.JOB_LEASE_SECONDS),
                        Attempts=int(job["Attempts"] or 0) + 1,
                    ).where(self.node_jobs.c.JobID == job["JobID"])
                )
                item = self._row(job)
                item["Payload"] = json.loads(job["Payload"])
                result.append(item)
        return result

    def complete_job(self, node_id, job_id, success, result=None, error_message=None):
        result = result or {}
        now = datetime.now()
        with self.engine.begin() as conn:
            job = conn.execute(
                self.node_jobs.select().where(
                    db.and_(self.node_jobs.c.JobID == job_id, self.node_jobs.c.NodeID == node_id)
                )
            ).mappings().fetchone()
            if not job:
                raise ValueError("Job does not exist")
            if job["Status"] in {"completed", "failed"}:
                return True

            conn.execute(
                self.node_jobs.update().values(
                    Status="completed" if success else "failed",
                    CompletedAt=now,
                    LeaseUntil=None,
                    ErrorMessage=None if success else str(error_message or "Node operation failed")[:4000],
                ).where(self.node_jobs.c.JobID == job_id)
            )

            peer_id = job["SubscriptionPeerID"]
            if not peer_id:
                return True
            peer = conn.execute(
                self.subscription_peers.select().where(
                    self.subscription_peers.c.SubscriptionPeerID == peer_id
                )
            ).mappings().fetchone()
            if not peer:
                return True
            if not success:
                conn.execute(
                    self.subscription_peers.update().values(Status="error", UpdatedAt=now).where(
                        self.subscription_peers.c.SubscriptionPeerID == peer_id
                    )
                )
                return True

            values = {"UpdatedAt": now}
            if job["Operation"] == "CREATE_PEER":
                address = str(result.get("address") or "")
                server_public_key = str(result.get("server_public_key") or "")
                endpoint = str(result.get("endpoint") or "")
                try:
                    ipaddress.ip_interface(address)
                except ValueError as exc:
                    raise ValueError("Node returned an invalid peer address") from exc
                if not self.WIREGUARD_KEY_PATTERN.fullmatch(server_public_key):
                    raise ValueError("Node returned an invalid WireGuard public key")
                if not endpoint or len(endpoint) > 500:
                    raise ValueError("Node returned an invalid public endpoint")
                values.update({
                    "Address": address,
                    "ServerPublicKey": server_public_key,
                    "PresharedKeyEncrypted": self._encrypt(result.get("preshared_key")),
                    "Endpoint": endpoint,
                    "Status": "active",
                })
            elif job["Operation"] == "ENABLE_PEER":
                values["Status"] = "active"
            elif job["Operation"] == "DISABLE_PEER":
                values["Status"] = "disabled"
            elif job["Operation"] == "DELETE_PEER":
                values["Status"] = "deleted"
            conn.execute(
                self.subscription_peers.update().values(**values).where(
                    self.subscription_peers.c.SubscriptionPeerID == peer_id
                )
            )

            subscription = conn.execute(
                self.subscriptions.select().where(
                    self.subscriptions.c.SubscriptionID == peer["SubscriptionID"]
                )
            ).mappings().fetchone()
            corrective_operation = None
            if (job["Operation"] in {"CREATE_PEER", "ENABLE_PEER"}
                    and subscription and subscription["Status"] != "active"):
                corrective_operation = "DISABLE_PEER"
            elif (job["Operation"] == "DISABLE_PEER" and subscription
                    and subscription["Status"] == "active"
                    and (str(job["IdempotencyKey"]).startswith("limit:")
                         or str(job["IdempotencyKey"]).startswith("subscription:"))):
                corrective_operation = "ENABLE_PEER"

            if corrective_operation:
                self._queue_job(
                    conn, peer["NodeID"], corrective_operation,
                    {
                        "subscription_peer_id": peer_id,
                        "interface": peer["InterfaceName"],
                        "public_key": peer["ClientPublicKey"],
                        "address": values.get("Address", peer["Address"]),
                    },
                    subscription_id=peer["SubscriptionID"],
                    subscription_peer_id=peer_id,
                    idempotency_key=(
                        f"reconcile:{corrective_operation.lower()}:{peer_id}:{uuid.uuid4()}"
                    ),
                )
                conn.execute(
                    self.subscription_peers.update().values(
                        Status="updating", UpdatedAt=now
                    ).where(self.subscription_peers.c.SubscriptionPeerID == peer_id)
                )
        return True

    def record_traffic(self, node_id, samples):
        accepted = 0
        with self.engine.begin() as conn:
            for sample in samples if isinstance(samples, list) else []:
                peer_id = sample.get("subscription_peer_id")
                session_id = str(sample.get("session_id") or "")[:64]
                try:
                    sequence = int(sample.get("sequence", 0))
                    rx_bytes = max(0, int(sample.get("rx_bytes", 0)))
                    tx_bytes = max(0, int(sample.get("tx_bytes", 0)))
                except (TypeError, ValueError):
                    continue
                if not peer_id or not session_id or sequence < 1:
                    continue
                duplicate = conn.execute(
                    self.traffic_reports.select().where(
                        db.and_(
                            self.traffic_reports.c.NodeID == node_id,
                            self.traffic_reports.c.SubscriptionPeerID == peer_id,
                            self.traffic_reports.c.SessionID == session_id,
                            self.traffic_reports.c.SequenceNumber == sequence,
                        )
                    )
                ).fetchone()
                if duplicate:
                    continue
                peer = conn.execute(
                    self.subscription_peers.select().where(
                        db.and_(
                            self.subscription_peers.c.SubscriptionPeerID == peer_id,
                            self.subscription_peers.c.NodeID == node_id,
                        )
                    )
                ).mappings().fetchone()
                if not peer:
                    continue

                last_rx = int(peer["LastRxBytes"] or 0)
                last_tx = int(peer["LastTxBytes"] or 0)
                if peer["LastSessionID"] == session_id and sequence <= int(peer["LastSequence"] or 0):
                    continue
                delta_rx = rx_bytes - last_rx if rx_bytes >= last_rx else rx_bytes
                delta_tx = tx_bytes - last_tx if tx_bytes >= last_tx else tx_bytes
                delta = max(0, delta_rx) + max(0, delta_tx)

                conn.execute(self.traffic_reports.insert().values(
                    ReportID=str(uuid.uuid4()),
                    NodeID=node_id,
                    SubscriptionPeerID=peer_id,
                    SessionID=session_id,
                    SequenceNumber=sequence,
                    RxBytes=rx_bytes,
                    TxBytes=tx_bytes,
                    DeltaBytes=delta,
                ))
                conn.execute(
                    self.subscription_peers.update().values(
                        UsedBytes=int(peer["UsedBytes"] or 0) + delta,
                        LastRxBytes=rx_bytes,
                        LastTxBytes=tx_bytes,
                        LastSequence=sequence,
                        LastSessionID=session_id,
                        UpdatedAt=datetime.now(),
                    ).where(self.subscription_peers.c.SubscriptionPeerID == peer_id)
                )
                conn.execute(
                    self.subscriptions.update().values(
                        UsedBytes=self.subscriptions.c.UsedBytes + delta,
                        UpdatedAt=datetime.now(),
                    ).where(self.subscriptions.c.SubscriptionID == peer["SubscriptionID"])
                )
                accepted += 1
        self.enforce_limits()
        return accepted

    def enforce_limits(self):
        now = datetime.now()
        with self.engine.begin() as conn:
            subscriptions = conn.execute(
                self.subscriptions.select().where(self.subscriptions.c.Status == "active")
            ).mappings().fetchall()
            for subscription in subscriptions:
                reason = None
                if subscription["ExpiresAt"] and subscription["ExpiresAt"] <= now:
                    reason = "expired"
                elif int(subscription["QuotaBytes"] or 0) > 0 and int(subscription["UsedBytes"] or 0) >= int(subscription["QuotaBytes"]):
                    reason = "quota_exceeded"
                if not reason:
                    continue
                conn.execute(
                    self.subscriptions.update().values(
                        Status=reason, DisabledAt=now, UpdatedAt=now
                    ).where(self.subscriptions.c.SubscriptionID == subscription["SubscriptionID"])
                )
                peers = conn.execute(
                    self.subscription_peers.select().where(
                        db.and_(
                            self.subscription_peers.c.SubscriptionID == subscription["SubscriptionID"],
                            self.subscription_peers.c.Status == "active",
                        )
                    )
                ).mappings().fetchall()
                for peer in peers:
                    self._queue_job(
                        conn, peer["NodeID"], "DISABLE_PEER",
                        {
                            "subscription_peer_id": peer["SubscriptionPeerID"],
                            "interface": peer["InterfaceName"],
                            "public_key": peer["ClientPublicKey"],
                            "address": peer["Address"],
                        },
                        subscription_id=subscription["SubscriptionID"],
                        subscription_peer_id=peer["SubscriptionPeerID"],
                        idempotency_key=f"limit:{reason}:{subscription['SubscriptionID']}:{peer['SubscriptionPeerID']}",
                    )
                    conn.execute(
                        self.subscription_peers.update().values(Status="updating", UpdatedAt=now).where(
                            self.subscription_peers.c.SubscriptionPeerID == peer["SubscriptionPeerID"]
                        )
                    )

    def get_public_subscription(self, subscription_id, token):
        with self.engine.connect() as conn:
            subscription = conn.execute(
                self.subscriptions.select().where(self.subscriptions.c.SubscriptionID == subscription_id)
            ).mappings().fetchone()
            if not subscription or not secrets.compare_digest(
                subscription["TokenHash"], self._hash_token(token)
            ):
                return None, "Subscription does not exist"
            if subscription["Status"] != "active":
                return None, f"Subscription is {subscription['Status']}"
            if subscription["ExpiresAt"] and subscription["ExpiresAt"] <= datetime.now():
                return None, "Subscription is expired"
            peers = conn.execute(
                self.subscription_peers.select().where(
                    db.and_(
                        self.subscription_peers.c.SubscriptionID == subscription_id,
                        self.subscription_peers.c.Status == "active",
                    )
                ).order_by(self.subscription_peers.c.Name)
            ).mappings().fetchall()
        files = []
        for peer in peers:
            files.append({
                "name": f"{peer['Name']}.conf",
                "node_id": peer["NodeID"],
                "configuration": self._build_configuration(peer),
            })
        subscription_data = self._row(subscription)
        subscription_data.pop("TokenHash", None)
        return {
            "subscription": subscription_data,
            "files": files,
        }, None

    def _build_configuration(self, peer):
        private_key = self._decrypt(peer["ClientPrivateKeyEncrypted"])
        preshared_key = self._decrypt(peer.get("PresharedKeyEncrypted"))
        lines = [
            "[Interface]",
            f"PrivateKey = {private_key}",
            f"Address = {peer['Address']}",
            f"DNS = {peer['DNS']}",
            f"MTU = {peer['MTU']}",
            "",
            "[Peer]",
            f"PublicKey = {peer['ServerPublicKey']}",
        ]
        if preshared_key:
            lines.append(f"PresharedKey = {preshared_key}")
        lines.extend([
            f"AllowedIPs = {peer['AllowedIPs']}",
            f"Endpoint = {peer['Endpoint']}",
            "PersistentKeepalive = 25",
            "",
        ])
        return "\n".join(lines)
