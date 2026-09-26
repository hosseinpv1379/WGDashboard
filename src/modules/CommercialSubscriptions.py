"""Commercial subscriptions and the multi-node WireGuard control plane."""

import hashlib
import ipaddress
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import sqlalchemy as db
from cryptography.fernet import Fernet, InvalidToken

from .DatabaseConnection import CreateEngine
from .Utilities import GenerateWireguardPrivateKey, GenerateWireguardPublicKey, ParseOptionalDateTime


class CommercialSubscriptions:
    NODE_OFFLINE_AFTER_SECONDS = 60
    JOB_LEASE_SECONDS = 30
    JOB_RETRY_SECONDS = 5
    JOB_MAX_ATTEMPTS = 3
    INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_=+.-]{1,64}$")
    WIREGUARD_KEY_PATTERN = re.compile(r"^[A-Za-z0-9+/]{43}=$")

    def __init__(self):
        self.engine = CreateEngine("wgdashboard")
        self.metadata = db.MetaData()
        self._define_tables()
        self.metadata.create_all(self.engine)
        self.fernet = Fernet(self._load_encryption_key())
        if str(os.getenv("WGD_LOCAL_NODE_ENABLED", "false")).lower() in {"1", "true", "yes"}:
            self._ensure_local_node_credentials()

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

        self.node_interfaces = db.Table(
            "CommercialNodeInterfaces", self.metadata,
            db.Column(
                "NodeID", db.String(36),
                db.ForeignKey("CommercialNodes.NodeID", ondelete="CASCADE"),
                primary_key=True,
            ),
            db.Column("InterfaceName", db.String(64), primary_key=True),
            db.Column("AddressPool", db.String(255), nullable=False, server_default=""),
            db.Column("PublicEndpoint", db.String(500), nullable=False, server_default=""),
            db.Column("PublicKey", db.String(255), nullable=False, server_default=""),
            db.Column("ListenPort", db.Integer, nullable=False, server_default="0"),
            db.Column("Status", db.String(32), nullable=False, server_default="unknown"),
            db.Column("LastSeenAt", db.DateTime),
            db.Column("UpdatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
        )

        self.node_groups = db.Table(
            "CommercialNodeGroups", self.metadata,
            db.Column("NodeGroupID", db.String(36), primary_key=True),
            db.Column("Name", db.String(255), nullable=False, unique=True),
            db.Column("Description", db.Text, nullable=False, server_default=""),
            db.Column("Status", db.String(32), nullable=False, server_default="active", index=True),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.Column("UpdatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
        )

        self.node_group_members = db.Table(
            "CommercialNodeGroupMembers", self.metadata,
            db.Column(
                "NodeGroupID", db.String(36),
                db.ForeignKey("CommercialNodeGroups.NodeGroupID", ondelete="CASCADE"),
                primary_key=True,
            ),
            db.Column(
                "NodeID", db.String(36),
                db.ForeignKey("CommercialNodes.NodeID", ondelete="RESTRICT"),
                primary_key=True,
            ),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
        )

        # New groups target a concrete interface. The older member table is
        # retained for backwards compatibility with already-installed panels.
        self.node_group_targets = db.Table(
            "CommercialNodeGroupTargets", self.metadata,
            db.Column(
                "NodeGroupID", db.String(36),
                db.ForeignKey("CommercialNodeGroups.NodeGroupID", ondelete="CASCADE"),
                primary_key=True,
            ),
            db.Column(
                "NodeID", db.String(36),
                db.ForeignKey("CommercialNodes.NodeID", ondelete="RESTRICT"),
                primary_key=True,
            ),
            db.Column("InterfaceName", db.String(64), primary_key=True),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
        )

        self.packages = db.Table(
            "CommercialPackages", self.metadata,
            db.Column("PackageID", db.String(36), primary_key=True),
            db.Column(
                "NodeGroupID", db.String(36),
                db.ForeignKey("CommercialNodeGroups.NodeGroupID", ondelete="RESTRICT"),
                nullable=False, index=True,
            ),
            db.Column("Name", db.String(255), nullable=False),
            db.Column("Description", db.Text, nullable=False, server_default=""),
            db.Column("QuotaBytes", db.BigInteger, nullable=False, server_default="0"),
            db.Column("DurationDays", db.Integer, nullable=False, server_default="0"),
            db.Column("Price", db.Numeric(18, 2), nullable=False, server_default="0"),
            db.Column("Currency", db.String(16), nullable=False, server_default="IRT"),
            db.Column("Status", db.String(32), nullable=False, server_default="active", index=True),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.Column("UpdatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
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

        # Package values are copied here at sale time. Existing subscriptions
        # therefore keep their original commercial terms when a package is
        # edited later.
        self.subscription_plans = db.Table(
            "CommercialSubscriptionPlans", self.metadata,
            db.Column(
                "SubscriptionID", db.String(36),
                db.ForeignKey("CommercialSubscriptions.SubscriptionID", ondelete="CASCADE"),
                primary_key=True,
            ),
            db.Column(
                "PackageID", db.String(36),
                db.ForeignKey("CommercialPackages.PackageID", ondelete="RESTRICT"),
                nullable=False, index=True,
            ),
            db.Column("PackageName", db.String(255), nullable=False),
            db.Column("NodeGroupID", db.String(36), nullable=False, index=True),
            db.Column("NodeGroupName", db.String(255), nullable=False),
            db.Column("QuotaBytes", db.BigInteger, nullable=False),
            db.Column("DurationDays", db.Integer, nullable=False),
            db.Column("Price", db.Numeric(18, 2), nullable=False),
            db.Column("Currency", db.String(16), nullable=False),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
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

        self.outbounds = db.Table(
            "CommercialOutbounds", self.metadata,
            db.Column("OutboundID", db.String(36), primary_key=True),
            db.Column(
                "NodeID", db.String(36),
                db.ForeignKey("CommercialNodes.NodeID", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            db.Column("Name", db.String(255), nullable=False),
            db.Column("InterfaceName", db.String(64), nullable=False),
            db.Column("SourceInterface", db.String(64), nullable=False, server_default="wg0"),
            db.Column("SourceAddressPool", db.String(255), nullable=False),
            db.Column("ConfigurationEncrypted", db.Text, nullable=False),
            db.Column("Status", db.String(32), nullable=False, server_default="pending"),
            db.Column("LastError", db.Text),
            db.Column("CreatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.Column("UpdatedAt", db.DateTime, nullable=False, server_default=db.func.now()),
            db.UniqueConstraint("NodeID", "InterfaceName", name="uq_commercial_outbound_interface"),
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
            elif isinstance(value, Decimal):
                result[key] = float(value)
        return result

    @staticmethod
    def _normalise_status(value, allowed=("active", "disabled")):
        status = str(value or "active").strip().lower()
        if status not in allowed:
            raise ValueError(f"Status must be one of: {', '.join(allowed)}")
        return status

    @staticmethod
    def _quota_bytes(quota_gb):
        try:
            quota_gb = Decimal(str(quota_gb or 0))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Quota must be a valid number") from exc
        if quota_gb < 0:
            raise ValueError("Quota cannot be negative")
        return int(quota_gb * 1024 * 1024 * 1024)

    @staticmethod
    def _price(value):
        try:
            price = Decimal(str(value or 0)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Price must be a valid number") from exc
        if price < 0:
            raise ValueError("Price cannot be negative")
        return price

    @staticmethod
    def _endpoint(value):
        endpoint = str(value or "").strip()
        host, separator, port = endpoint.rpartition(":")
        try:
            valid_port = 1 <= int(port) <= 65535
        except (TypeError, ValueError):
            valid_port = False
        if (not host or separator != ":" or not valid_port or len(endpoint) > 500
                or (":" in host and not (host.startswith("[") and host.endswith("]")))
                or any(character.isspace() for character in endpoint)):
            raise ValueError("Public endpoint must include a valid host and port")
        return endpoint

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

    def _ensure_local_node_credentials(self):
        path = os.getenv(
            "WGD_LOCAL_NODE_CREDENTIALS",
            os.path.join(os.getenv("CONFIGURATION_PATH", "."), "local-node-credentials.json"),
        )
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as credentials_file:
                    credentials = json.load(credentials_file)
                if self.authenticate_node(credentials.get("agent_token")):
                    return
            except (OSError, ValueError, TypeError):
                pass

        created = self.create_node(
            os.getenv("WGD_LOCAL_NODE_NAME", "panel-local"),
            os.getenv("WGD_LOCAL_NODE_REGION", "Local"),
            os.getenv("WGD_LOCAL_NODE_ENDPOINT", ""),
            os.getenv("WGD_LOCAL_NODE_CAPACITY", "0"),
        )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as credentials_file:
            json.dump(created, credentials_file)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def create_node(self, name, region="", public_endpoint="", capacity=0):
        if not str(name or "").strip():
            raise ValueError("Node name is required")
        try:
            capacity = max(0, int(capacity or 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Node capacity must be a number") from exc
        public_endpoint = self._endpoint(public_endpoint)

        node_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(36)
        with self.engine.begin() as conn:
            conn.execute(self.nodes.insert().values(
                NodeID=node_id,
                Name=str(name).strip(),
                Region=str(region or "").strip(),
                PublicEndpoint=public_endpoint,
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
                    "online" if last_seen and
                    (now - last_seen).total_seconds() <= self.NODE_OFFLINE_AFTER_SECONDS
                    else "offline"
                )
                interfaces = conn.execute(
                    self.node_interfaces.select().where(
                        self.node_interfaces.c.NodeID == row["NodeID"]
                    ).order_by(self.node_interfaces.c.InterfaceName)
                ).mappings().fetchall()
                item["Interfaces"] = [self._row(interface) for interface in interfaces]
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

    def _validated_nodes(self, conn, node_ids, require_one=True):
        if not isinstance(node_ids, list):
            raise ValueError("Node IDs must be a list")
        unique_ids = list(dict.fromkeys(str(node_id).strip() for node_id in node_ids if node_id))
        if require_one and not unique_ids:
            raise ValueError("At least one node is required")
        nodes = []
        for node_id in unique_ids:
            node = conn.execute(
                self.nodes.select().where(
                    db.and_(self.nodes.c.NodeID == node_id, self.nodes.c.RevokedAt.is_(None))
                )
            ).mappings().fetchone()
            if not node:
                raise ValueError(f"Node does not exist: {node_id}")
            nodes.append(node)
        return nodes

    def _validated_targets(self, conn, targets):
        if not isinstance(targets, list) or not targets:
            raise ValueError("At least one node interface is required")
        result = []
        seen = set()
        for target in targets:
            if not isinstance(target, dict):
                raise ValueError("Node interface target is invalid")
            node_id = str(target.get("node_id") or "").strip()
            interface_name = str(target.get("interface") or "wg0").strip()
            key = (node_id, interface_name)
            if key in seen:
                continue
            if not self.INTERFACE_PATTERN.fullmatch(interface_name):
                raise ValueError("WireGuard interface name is invalid")
            node = self._validated_nodes(conn, [node_id])[0]
            known_interfaces = conn.execute(
                db.select(db.func.count()).select_from(self.node_interfaces).where(
                    self.node_interfaces.c.NodeID == node_id
                )
            ).scalar_one()
            if known_interfaces:
                interface = conn.execute(
                    self.node_interfaces.select().where(
                        db.and_(
                            self.node_interfaces.c.NodeID == node_id,
                            self.node_interfaces.c.InterfaceName == interface_name,
                        )
                    )
                ).fetchone()
                if not interface:
                    raise ValueError(f"Interface does not exist on node {node['Name']}: {interface_name}")
            seen.add(key)
            result.append({"node": node, "interface": interface_name})
        return result

    def _group_target_rows(self, conn, group_id):
        targets = conn.execute(
            db.select(
                self.nodes,
                self.node_group_targets.c.InterfaceName,
            ).select_from(
                self.node_group_targets.join(
                    self.nodes, self.node_group_targets.c.NodeID == self.nodes.c.NodeID,
                )
            ).where(
                db.and_(
                    self.node_group_targets.c.NodeGroupID == group_id,
                    self.nodes.c.RevokedAt.is_(None),
                )
            ).order_by(self.nodes.c.Name, self.node_group_targets.c.InterfaceName)
        ).mappings().fetchall()
        if targets:
            return targets
        return conn.execute(
            db.select(
                self.nodes,
                db.literal("wg0").label("InterfaceName"),
            ).select_from(
                self.node_group_members.join(
                    self.nodes, self.node_group_members.c.NodeID == self.nodes.c.NodeID,
                )
            ).where(
                db.and_(
                    self.node_group_members.c.NodeGroupID == group_id,
                    self.nodes.c.RevokedAt.is_(None),
                )
            ).order_by(self.nodes.c.Name)
        ).mappings().fetchall()

    def create_node_group(self, name, node_ids=None, description="", status="active", targets=None):
        name = str(name or "").strip()
        if not name:
            raise ValueError("Node group name is required")
        status = self._normalise_status(status)
        group_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            duplicate = conn.execute(
                db.select(self.node_groups.c.NodeGroupID).where(
                    db.func.lower(self.node_groups.c.Name) == name.lower()
                )
            ).fetchone()
            if duplicate:
                raise ValueError("A node group with this name already exists")
            validated_targets = self._validated_targets(conn, targets) if targets else [
                {"node": node, "interface": "wg0"}
                for node in self._validated_nodes(conn, node_ids or [])
            ]
            conn.execute(self.node_groups.insert().values(
                NodeGroupID=group_id,
                Name=name,
                Description=str(description or "").strip(),
                Status=status,
                UpdatedAt=datetime.now(),
            ))
            conn.execute(self.node_group_members.insert(), [
                {"NodeGroupID": group_id, "NodeID": node_id}
                for node_id in dict.fromkeys(target["node"]["NodeID"] for target in validated_targets)
            ])
            conn.execute(self.node_group_targets.insert(), [
                {
                    "NodeGroupID": group_id,
                    "NodeID": target["node"]["NodeID"],
                    "InterfaceName": target["interface"],
                }
                for target in validated_targets
            ])
        return {"node_group_id": group_id}

    def list_node_groups(self):
        now = datetime.now()
        with self.engine.connect() as conn:
            groups = conn.execute(
                self.node_groups.select().order_by(self.node_groups.c.CreatedAt.desc())
            ).mappings().fetchall()
            result = []
            for group in groups:
                item = self._row(group)
                members = conn.execute(
                    db.select(self.nodes).select_from(
                        self.node_group_members.join(
                            self.nodes,
                            self.node_group_members.c.NodeID == self.nodes.c.NodeID,
                        )
                    ).where(
                        db.and_(
                            self.node_group_members.c.NodeGroupID == group["NodeGroupID"],
                            self.nodes.c.RevokedAt.is_(None),
                        )
                    ).order_by(self.nodes.c.Name)
                ).mappings().fetchall()
                item["Nodes"] = []
                for member in members:
                    member_item = self._row(member)
                    last_seen = member.get("LastSeenAt")
                    member_item["Status"] = (
                        "online" if last_seen and
                        (now - last_seen).total_seconds() <= self.NODE_OFFLINE_AFTER_SECONDS
                        else "offline"
                    )
                    member_item.pop("TokenHash", None)
                    item["Nodes"].append(member_item)
                item["NodeIDs"] = [member["NodeID"] for member in members]
                item["NodeCount"] = len(members)
                targets = self._group_target_rows(conn, group["NodeGroupID"])
                item["Targets"] = [
                    {
                        "NodeID": target["NodeID"],
                        "NodeName": target["Name"],
                        "NodeRegion": target["Region"],
                        "InterfaceName": target["InterfaceName"],
                    }
                    for target in targets
                ]
                item["TargetKeys"] = [
                    f"{target['NodeID']}::{target['InterfaceName']}" for target in targets
                ]
                item["InterfaceCount"] = len(targets)
                result.append(item)
        return result

    def update_node_group(self, group_id, data):
        values = {"UpdatedAt": datetime.now()}
        with self.engine.begin() as conn:
            existing = conn.execute(
                self.node_groups.select().where(self.node_groups.c.NodeGroupID == group_id)
            ).mappings().fetchone()
            if not existing:
                return False
            if "name" in data:
                name = str(data.get("name") or "").strip()
                if not name:
                    raise ValueError("Node group name is required")
                duplicate = conn.execute(
                    db.select(self.node_groups.c.NodeGroupID).where(
                        db.and_(
                            db.func.lower(self.node_groups.c.Name) == name.lower(),
                            self.node_groups.c.NodeGroupID != group_id,
                        )
                    )
                ).fetchone()
                if duplicate:
                    raise ValueError("A node group with this name already exists")
                values["Name"] = name
            if "description" in data:
                values["Description"] = str(data.get("description") or "").strip()
            if "status" in data:
                values["Status"] = self._normalise_status(data.get("status"))
            if "node_ids" in data:
                nodes = self._validated_nodes(conn, data.get("node_ids"))
                conn.execute(
                    self.node_group_members.delete().where(
                        self.node_group_members.c.NodeGroupID == group_id
                    )
                )
                conn.execute(self.node_group_members.insert(), [
                    {"NodeGroupID": group_id, "NodeID": node["NodeID"]} for node in nodes
                ])
                if "targets" not in data:
                    conn.execute(
                        self.node_group_targets.delete().where(
                            self.node_group_targets.c.NodeGroupID == group_id
                        )
                    )
                    conn.execute(self.node_group_targets.insert(), [
                        {
                            "NodeGroupID": group_id,
                            "NodeID": node["NodeID"],
                            "InterfaceName": "wg0",
                        }
                        for node in nodes
                    ])
            if "targets" in data:
                targets = self._validated_targets(conn, data.get("targets"))
                conn.execute(
                    self.node_group_targets.delete().where(
                        self.node_group_targets.c.NodeGroupID == group_id
                    )
                )
                conn.execute(self.node_group_targets.insert(), [
                    {
                        "NodeGroupID": group_id,
                        "NodeID": target["node"]["NodeID"],
                        "InterfaceName": target["interface"],
                    }
                    for target in targets
                ])
                conn.execute(
                    self.node_group_members.delete().where(
                        self.node_group_members.c.NodeGroupID == group_id
                    )
                )
                conn.execute(self.node_group_members.insert(), [
                    {"NodeGroupID": group_id, "NodeID": node_id}
                    for node_id in dict.fromkeys(target["node"]["NodeID"] for target in targets)
                ])
            conn.execute(
                self.node_groups.update().values(**values).where(
                    self.node_groups.c.NodeGroupID == group_id
                )
            )
        return True

    def create_package(self, name, node_group_id, quota_gb, duration_days, price,
                       currency="IRT", description="", status="active"):
        name = str(name or "").strip()
        if not name:
            raise ValueError("Package name is required")
        try:
            duration_days = int(duration_days or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Duration must be a whole number of days") from exc
        if duration_days < 0:
            raise ValueError("Duration cannot be negative")
        currency = str(currency or "IRT").strip().upper()
        if not re.fullmatch(r"[A-Z0-9_-]{2,16}", currency):
            raise ValueError("Currency code is invalid")
        status = self._normalise_status(status)
        package_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            group = conn.execute(
                self.node_groups.select().where(
                    self.node_groups.c.NodeGroupID == str(node_group_id or "")
                )
            ).mappings().fetchone()
            if not group:
                raise ValueError("Node group does not exist")
            if not self._group_target_rows(conn, group["NodeGroupID"]):
                raise ValueError("Node group must contain at least one node interface")
            conn.execute(self.packages.insert().values(
                PackageID=package_id,
                NodeGroupID=group["NodeGroupID"],
                Name=name,
                Description=str(description or "").strip(),
                QuotaBytes=self._quota_bytes(quota_gb),
                DurationDays=duration_days,
                Price=self._price(price),
                Currency=currency,
                Status=status,
                UpdatedAt=datetime.now(),
            ))
        return {"package_id": package_id}

    def list_packages(self, active_only=False):
        query = db.select(
            self.packages,
            self.node_groups.c.Name.label("NodeGroupName"),
            self.node_groups.c.Status.label("NodeGroupStatus"),
            db.func.count(self.node_group_members.c.NodeID).label("NodeCount"),
        ).select_from(
            self.packages.join(
                self.node_groups,
                self.packages.c.NodeGroupID == self.node_groups.c.NodeGroupID,
            ).outerjoin(
                self.node_group_members,
                self.packages.c.NodeGroupID == self.node_group_members.c.NodeGroupID,
            )
        ).group_by(
            *self.packages.c,
            self.node_groups.c.Name,
            self.node_groups.c.Status,
        ).order_by(self.packages.c.CreatedAt.desc())
        if active_only:
            query = query.where(
                db.and_(
                    self.packages.c.Status == "active",
                    self.node_groups.c.Status == "active",
                )
            )
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().fetchall()
            result = []
            for row in rows:
                item = self._row(row)
                item["QuotaGB"] = round(int(row["QuotaBytes"] or 0) / (1024 ** 3), 4)
                targets = self._group_target_rows(conn, row["NodeGroupID"])
                item["NodeCount"] = len({target["NodeID"] for target in targets})
                item["InterfaceCount"] = len(targets)
                result.append(item)
        return result

    def update_package(self, package_id, data):
        values = {"UpdatedAt": datetime.now()}
        with self.engine.begin() as conn:
            existing = conn.execute(
                self.packages.select().where(self.packages.c.PackageID == package_id)
            ).mappings().fetchone()
            if not existing:
                return False
            if "name" in data:
                name = str(data.get("name") or "").strip()
                if not name:
                    raise ValueError("Package name is required")
                values["Name"] = name
            if "description" in data:
                values["Description"] = str(data.get("description") or "").strip()
            if "quota_gb" in data:
                values["QuotaBytes"] = self._quota_bytes(data.get("quota_gb"))
            if "duration_days" in data:
                try:
                    duration_days = int(data.get("duration_days") or 0)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Duration must be a whole number of days") from exc
                if duration_days < 0:
                    raise ValueError("Duration cannot be negative")
                values["DurationDays"] = duration_days
            if "price" in data:
                values["Price"] = self._price(data.get("price"))
            if "currency" in data:
                currency = str(data.get("currency") or "").strip().upper()
                if not re.fullmatch(r"[A-Z0-9_-]{2,16}", currency):
                    raise ValueError("Currency code is invalid")
                values["Currency"] = currency
            if "status" in data:
                values["Status"] = self._normalise_status(data.get("status"))
            if "node_group_id" in data:
                group = conn.execute(
                    self.node_groups.select().where(
                        self.node_groups.c.NodeGroupID == str(data.get("node_group_id") or "")
                    )
                ).mappings().fetchone()
                if not group:
                    raise ValueError("Node group does not exist")
                if not self._group_target_rows(conn, group["NodeGroupID"]):
                    raise ValueError("Node group must contain at least one node interface")
                values["NodeGroupID"] = group["NodeGroupID"]
            conn.execute(
                self.packages.update().values(**values).where(
                    self.packages.c.PackageID == package_id
                )
            )
        return True

    @staticmethod
    def _validate_outbound_configuration(configuration):
        value = str(configuration or "").strip()
        if not value:
            raise ValueError("WireGuard outbound configuration is required")
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("WireGuard outbound configuration is too large")
        required_patterns = (
            r"(?mi)^\s*\[Interface\]\s*$",
            r"(?mi)^\s*PrivateKey\s*=\s*\S+\s*$",
            r"(?mi)^\s*\[Peer\]\s*$",
            r"(?mi)^\s*PublicKey\s*=\s*\S+\s*$",
            r"(?mi)^\s*Endpoint\s*=\s*\S+\s*$",
            r"(?mi)^\s*AllowedIPs\s*=\s*[^\n]*0\.0\.0\.0/0[^\n]*$",
        )
        if not all(re.search(pattern, value) for pattern in required_patterns):
            raise ValueError("WireGuard outbound configuration is incomplete")
        return value + "\n"

    def create_outbound(self, node_id, name, interface_name, source_interface,
                        source_address_pool, configuration):
        name = str(name or "").strip()
        interface_name = str(interface_name or "").strip()
        source_interface = str(source_interface or "wg0").strip()
        if not name:
            raise ValueError("Outbound name is required")
        if not self.INTERFACE_PATTERN.fullmatch(interface_name):
            raise ValueError("Outbound interface name is invalid")
        if not self.INTERFACE_PATTERN.fullmatch(source_interface):
            raise ValueError("Source interface name is invalid")
        if interface_name == source_interface:
            raise ValueError("Outbound and source interfaces must be different")
        try:
            source_network = ipaddress.ip_network(str(source_address_pool or ""), strict=False)
        except ValueError as exc:
            raise ValueError("Source address pool is invalid") from exc
        if source_network.version != 4:
            raise ValueError("Only IPv4 outbound source pools are currently supported")
        configuration = self._validate_outbound_configuration(configuration)
        outbound_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            target = self._validated_targets(conn, [{
                "node_id": node_id, "interface": source_interface,
            }])[0]
            node = target["node"]
            duplicate = conn.execute(
                self.outbounds.select().where(
                    db.and_(
                        self.outbounds.c.NodeID == node["NodeID"],
                        self.outbounds.c.InterfaceName == interface_name,
                    )
                )
            ).fetchone()
            if duplicate:
                raise ValueError("This outbound interface already exists on the node")
            conn.execute(self.outbounds.insert().values(
                OutboundID=outbound_id,
                NodeID=node["NodeID"],
                Name=name,
                InterfaceName=interface_name,
                SourceInterface=source_interface,
                SourceAddressPool=str(source_network),
                ConfigurationEncrypted=self._encrypt(configuration),
                Status="pending",
                UpdatedAt=datetime.now(),
            ))
            job_id = self._queue_job(
                conn, node["NodeID"], "APPLY_OUTBOUND", {"outbound_id": outbound_id},
                idempotency_key=f"outbound:apply:{outbound_id}:{uuid.uuid4()}",
            )
        return {"outbound_id": outbound_id, "job_id": job_id}

    def list_outbounds(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                db.select(
                    self.outbounds,
                    self.nodes.c.Name.label("NodeName"),
                    self.nodes.c.Region.label("NodeRegion"),
                ).select_from(
                    self.outbounds.join(
                        self.nodes, self.outbounds.c.NodeID == self.nodes.c.NodeID,
                    )
                ).order_by(self.outbounds.c.CreatedAt.desc())
            ).mappings().fetchall()
        result = []
        for row in rows:
            item = self._row(row)
            item.pop("ConfigurationEncrypted", None)
            result.append(item)
        return result

    def subscription_counts_by_client(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                db.select(
                    self.subscriptions.c.ClientID,
                    db.func.count(self.subscriptions.c.SubscriptionID).label("SubscriptionCount"),
                ).group_by(self.subscriptions.c.ClientID)
            ).mappings().fetchall()
        return {row["ClientID"]: int(row["SubscriptionCount"] or 0) for row in rows}

    def client_has_subscriptions(self, client_id):
        with self.engine.connect() as conn:
            return bool(conn.execute(
                db.select(db.func.count()).select_from(self.subscriptions).where(
                    self.subscriptions.c.ClientID == client_id
                )
            ).scalar_one())

    def update_outbound(self, outbound_id, data):
        values = {"UpdatedAt": datetime.now(), "LastError": None, "Status": "pending"}
        with self.engine.begin() as conn:
            outbound = conn.execute(
                self.outbounds.select().where(self.outbounds.c.OutboundID == outbound_id)
            ).mappings().fetchone()
            if not outbound:
                return None
            if "name" in data:
                name = str(data.get("name") or "").strip()
                if not name:
                    raise ValueError("Outbound name is required")
                values["Name"] = name
            if "configuration" in data and str(data.get("configuration") or "").strip():
                values["ConfigurationEncrypted"] = self._encrypt(
                    self._validate_outbound_configuration(data.get("configuration"))
                )
            if "source_interface" in data:
                source_interface = str(data.get("source_interface") or "").strip()
                if not self.INTERFACE_PATTERN.fullmatch(source_interface):
                    raise ValueError("Source interface name is invalid")
                if source_interface == outbound["InterfaceName"]:
                    raise ValueError("Outbound and source interfaces must be different")
                self._validated_targets(conn, [{
                    "node_id": outbound["NodeID"], "interface": source_interface,
                }])
                values["SourceInterface"] = source_interface
            if "source_address_pool" in data:
                try:
                    network = ipaddress.ip_network(
                        str(data.get("source_address_pool") or ""), strict=False,
                    )
                except ValueError as exc:
                    raise ValueError("Source address pool is invalid") from exc
                if network.version != 4:
                    raise ValueError("Only IPv4 outbound source pools are currently supported")
                values["SourceAddressPool"] = str(network)
            conn.execute(
                self.outbounds.update().values(**values).where(
                    self.outbounds.c.OutboundID == outbound_id
                )
            )
            job_id = self._queue_job(
                conn, outbound["NodeID"], "APPLY_OUTBOUND", {"outbound_id": outbound_id},
                idempotency_key=f"outbound:apply:{outbound_id}:{uuid.uuid4()}",
            )
        return job_id

    def queue_outbound_action(self, outbound_id, operation):
        operation = str(operation or "").upper()
        if operation not in {"APPLY_OUTBOUND", "REMOVE_OUTBOUND"}:
            raise ValueError("Unsupported outbound operation")
        with self.engine.begin() as conn:
            outbound = conn.execute(
                self.outbounds.select().where(self.outbounds.c.OutboundID == outbound_id)
            ).mappings().fetchone()
            if not outbound:
                raise ValueError("Outbound does not exist")
            job_id = self._queue_job(
                conn, outbound["NodeID"], operation, {"outbound_id": outbound_id},
                idempotency_key=f"outbound:{operation.lower()}:{outbound_id}:{uuid.uuid4()}",
            )
            conn.execute(
                self.outbounds.update().values(
                    Status="removing" if operation == "REMOVE_OUTBOUND" else "pending",
                    LastError=None,
                    UpdatedAt=datetime.now(),
                ).where(self.outbounds.c.OutboundID == outbound_id)
            )
        return job_id

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
        if not isinstance(payload, dict):
            raise ValueError("Heartbeat payload must be an object")
        now = datetime.now()
        with self.engine.begin() as conn:
            conn.execute(
                self.nodes.update().values(
                    Status="online",
                    LastSeenAt=now,
                    AgentVersion=str(payload.get("agent_version", ""))[:64],
                    PublicEndpoint=str(payload.get("public_endpoint", ""))[:500]
                    or self.nodes.c.PublicEndpoint,
                ).where(self.nodes.c.NodeID == node_id)
            )
            conn.execute(
                self.node_interfaces.update().values(
                    Status="down", UpdatedAt=now,
                ).where(self.node_interfaces.c.NodeID == node_id)
            )
            for interface in payload.get("interfaces", []) if isinstance(payload.get("interfaces", []), list) else []:
                if not isinstance(interface, dict):
                    continue
                name = str(interface.get("name") or "")
                if not self.INTERFACE_PATTERN.fullmatch(name):
                    continue
                address_pool = str(interface.get("address_pool") or "")[:255]
                if address_pool:
                    try:
                        ipaddress.ip_network(address_pool, strict=False)
                    except ValueError:
                        address_pool = ""
                public_key = str(interface.get("public_key") or "")[:255]
                if public_key and not self.WIREGUARD_KEY_PATTERN.fullmatch(public_key):
                    public_key = ""
                try:
                    listen_port = max(0, min(int(interface.get("listen_port") or 0), 65535))
                except (TypeError, ValueError):
                    listen_port = 0
                values = {
                    "AddressPool": address_pool,
                    "PublicEndpoint": str(interface.get("public_endpoint") or "")[:500],
                    "PublicKey": public_key,
                    "ListenPort": listen_port,
                    "Status": "up" if interface.get("status") in {"up", "online"} else "down",
                    "LastSeenAt": now,
                    "UpdatedAt": now,
                }
                existing = conn.execute(
                    self.node_interfaces.select().where(
                        db.and_(
                            self.node_interfaces.c.NodeID == node_id,
                            self.node_interfaces.c.InterfaceName == name,
                        )
                    )
                ).fetchone()
                if existing:
                    conn.execute(
                        self.node_interfaces.update().values(**values).where(
                            db.and_(
                                self.node_interfaces.c.NodeID == node_id,
                                self.node_interfaces.c.InterfaceName == name,
                            )
                        )
                    )
                else:
                    conn.execute(self.node_interfaces.insert().values(
                        NodeID=node_id, InterfaceName=name, **values,
                    ))

            # Agents older than v0.2 do not send an inventory. Never interpret
            # that as an empty node, otherwise an upgrade could recreate every
            # peer at once. A session-scoped inventory is authoritative and lets
            # the panel repair a lost agent state file after a reboot/reinstall.
            agent_session = str(payload.get("agent_session") or "")[:64]
            reported_peers = payload.get("peers")
            if not agent_session or not isinstance(reported_peers, list):
                return

            inventory = {}
            peer_inventory_complete = len(reported_peers) <= 10000
            for item in reported_peers[:10000]:
                if not isinstance(item, dict):
                    continue
                peer_id = str(item.get("subscription_peer_id") or "")[:64]
                interface_name = str(item.get("interface") or "")[:64]
                public_key = str(item.get("public_key") or "")[:255]
                address = str(item.get("address") or "")[:255]
                if (not peer_id or not self.INTERFACE_PATTERN.fullmatch(interface_name)
                        or not self.WIREGUARD_KEY_PATTERN.fullmatch(public_key)):
                    continue
                inventory[peer_id] = {
                    "interface": interface_name,
                    "public_key": public_key,
                    "address": address,
                    "enabled": item.get("enabled") is True,
                }

            peers = conn.execute(
                self.subscription_peers.select().where(
                    self.subscription_peers.c.NodeID == node_id
                )
            ).mappings().fetchall()
            known_ids = {peer["SubscriptionPeerID"] for peer in peers}
            for peer in peers:
                peer_id = peer["SubscriptionPeerID"]
                actual = inventory.get(peer_id)
                if actual and (
                    actual["interface"] != peer["InterfaceName"]
                    or actual["public_key"] != peer["ClientPublicKey"]
                    or (peer["Address"] and actual["address"] != peer["Address"])
                ):
                    # Remove corrupted/stale local state first without changing
                    # the authoritative peer row. The next heartbeat recreates
                    # an active peer with its original address and key.
                    self._queue_job(
                        conn, node_id, "DELETE_PEER",
                        {"subscription_peer_id": peer_id},
                        idempotency_key=f"inventory:{agent_session}:repair-delete:{peer_id}",
                    )
                    continue

                operation = None
                if peer["Status"] == "active":
                    if actual is None and peer_inventory_complete:
                        operation = "CREATE_PEER"
                    elif not actual["enabled"]:
                        operation = "ENABLE_PEER"
                elif peer["Status"] == "disabled" and actual and actual["enabled"]:
                    operation = "DISABLE_PEER"
                elif peer["Status"] in {"deleted", "deleting"} and actual:
                    operation = "DELETE_PEER"
                if not operation:
                    continue

                self._queue_job(
                    conn, node_id, operation,
                    {
                        "subscription_peer_id": peer_id,
                        "interface": peer["InterfaceName"],
                        "public_key": peer["ClientPublicKey"],
                        "address": peer["Address"],
                    },
                    subscription_id=peer["SubscriptionID"],
                    subscription_peer_id=peer_id,
                    idempotency_key=f"inventory:{agent_session}:{operation.lower()}:{peer_id}",
                )
                conn.execute(
                    self.subscription_peers.update().values(
                        Status="deleting" if operation == "DELETE_PEER" else "updating",
                        UpdatedAt=now,
                    ).where(self.subscription_peers.c.SubscriptionPeerID == peer_id)
                )

            # State can contain peers whose subscription was deleted directly
            # from the database. They are safe to remove from this node only.
            for peer_id in inventory.keys() - known_ids:
                self._queue_job(
                    conn, node_id, "DELETE_PEER",
                    {"subscription_peer_id": peer_id},
                    idempotency_key=f"inventory:{agent_session}:orphan-delete:{peer_id}",
                )

            outbound_inventory = {}
            reported_outbounds = payload.get("outbounds")
            if isinstance(reported_outbounds, list):
                outbound_inventory_complete = len(reported_outbounds) <= 1000
                for item in reported_outbounds[:1000]:
                    if not isinstance(item, dict):
                        continue
                    outbound_id = str(item.get("outbound_id") or "")[:64]
                    interface_name = str(item.get("interface") or "")[:64]
                    source_interface = str(item.get("source_interface") or "")[:64]
                    source_pool = str(item.get("source_address_pool") or "")[:255]
                    if (not outbound_id
                            or not self.INTERFACE_PATTERN.fullmatch(interface_name)
                            or not self.INTERFACE_PATTERN.fullmatch(source_interface)):
                        continue
                    try:
                        source_pool = str(ipaddress.ip_network(source_pool, strict=False))
                    except ValueError:
                        continue
                    outbound_inventory[outbound_id] = {
                        "interface": interface_name,
                        "source_interface": source_interface,
                        "source_address_pool": source_pool,
                    }

                outbounds = conn.execute(
                    self.outbounds.select().where(self.outbounds.c.NodeID == node_id)
                ).mappings().fetchall()
                known_outbound_ids = {outbound["OutboundID"] for outbound in outbounds}
                for outbound in outbounds:
                    outbound_id = outbound["OutboundID"]
                    actual = outbound_inventory.get(outbound_id)
                    mismatch = actual and (
                        actual["interface"] != outbound["InterfaceName"]
                        or actual["source_interface"] != outbound["SourceInterface"]
                        or actual["source_address_pool"] != outbound["SourceAddressPool"]
                    )
                    if mismatch:
                        self._queue_job(
                            conn, node_id, "REMOVE_OUTBOUND",
                            {"outbound_id": outbound_id, **actual},
                            idempotency_key=(
                                f"inventory:{agent_session}:outbound-repair-remove:{outbound_id}"
                            ),
                        )
                        continue
                    operation = None
                    if (outbound["Status"] == "active" and actual is None
                            and outbound_inventory_complete):
                        operation = "APPLY_OUTBOUND"
                    elif outbound["Status"] == "disabled" and actual:
                        operation = "REMOVE_OUTBOUND"
                    if operation:
                        self._queue_job(
                            conn, node_id, operation, {"outbound_id": outbound_id},
                            idempotency_key=(
                                f"inventory:{agent_session}:{operation.lower()}:{outbound_id}"
                            ),
                        )
                        conn.execute(
                            self.outbounds.update().values(
                                Status="removing" if operation == "REMOVE_OUTBOUND" else "pending",
                                LastError=None,
                                UpdatedAt=now,
                            ).where(self.outbounds.c.OutboundID == outbound_id)
                        )

                for outbound_id in outbound_inventory.keys() - known_outbound_ids:
                    actual = outbound_inventory[outbound_id]
                    self._queue_job(
                        conn, node_id, "REMOVE_OUTBOUND",
                        {"outbound_id": outbound_id, **actual},
                        idempotency_key=(
                            f"inventory:{agent_session}:orphan-outbound-remove:{outbound_id}"
                        ),
                    )

    def create_subscription(self, client_id, name, quota_gb=0, expires_at=None, max_peers=1):
        if not client_id:
            raise ValueError("Client is required")
        if not str(name or "").strip():
            raise ValueError("Subscription name is required")
        try:
            max_peers = int(max_peers or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("Maximum peers must be a valid number") from exc
        if max_peers < 1:
            raise ValueError("Maximum peers must be at least one")

        expires_at = ParseOptionalDateTime(expires_at)
        subscription_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(36)
        with self.engine.begin() as conn:
            conn.execute(self.subscriptions.insert().values(
                SubscriptionID=subscription_id,
                ClientID=client_id,
                Name=str(name).strip(),
                Status="active",
                QuotaBytes=self._quota_bytes(quota_gb),
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

    def create_subscription_from_package(self, client_id, package_id, name=None):
        if not client_id:
            raise ValueError("Client is required")
        now = datetime.now()
        subscription_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(36)
        created_jobs = []

        with self.engine.begin() as conn:
            package = conn.execute(
                db.select(
                    self.packages,
                    self.node_groups.c.Name.label("NodeGroupName"),
                    self.node_groups.c.Status.label("NodeGroupStatus"),
                ).select_from(
                    self.packages.join(
                        self.node_groups,
                        self.packages.c.NodeGroupID == self.node_groups.c.NodeGroupID,
                    )
                ).where(self.packages.c.PackageID == str(package_id or ""))
            ).mappings().fetchone()
            if not package:
                raise ValueError("Package does not exist")
            if package["Status"] != "active":
                raise ValueError("Package is disabled")
            if package["NodeGroupStatus"] != "active":
                raise ValueError("Package node group is disabled")

            targets = self._group_target_rows(conn, package["NodeGroupID"])
            if not targets:
                raise ValueError("Package node group has no available node interfaces")

            requested_per_node = {}
            for node in targets:
                requested_per_node[node["NodeID"]] = requested_per_node.get(node["NodeID"], 0) + 1
            # Lock capacity rows in a deterministic order on PostgreSQL. Two
            # simultaneous sales must not both observe the same free slot.
            for node_id in sorted(requested_per_node):
                node_query = self.nodes.select().where(
                    db.and_(self.nodes.c.NodeID == node_id, self.nodes.c.RevokedAt.is_(None))
                )
                if self.engine.dialect.name == "postgresql":
                    node_query = node_query.with_for_update()
                node = conn.execute(node_query).mappings().fetchone()
                if not node:
                    raise ValueError(f"Node is no longer available: {node_id}")
                node_peer_count = conn.execute(
                    db.select(db.func.count()).select_from(self.subscription_peers).where(
                        db.and_(
                            self.subscription_peers.c.NodeID == node_id,
                            self.subscription_peers.c.Status != "deleted",
                        )
                    )
                ).scalar_one()
                if (int(node["Capacity"] or 0) > 0 and
                        node_peer_count + requested_per_node[node_id] > int(node["Capacity"])):
                    raise ValueError(f"Node capacity is exhausted: {node['Name']}")

            duration_days = int(package["DurationDays"] or 0)
            expires_at = now + timedelta(days=duration_days) if duration_days > 0 else None
            subscription_name = str(name or "").strip() or package["Name"]
            conn.execute(self.subscriptions.insert().values(
                SubscriptionID=subscription_id,
                ClientID=client_id,
                Name=subscription_name,
                Status="active",
                QuotaBytes=int(package["QuotaBytes"] or 0),
                UsedBytes=0,
                ExpiresAt=expires_at,
                MaxPeers=len(targets),
                TokenHash=self._hash_token(token),
                UpdatedAt=now,
            ))
            conn.execute(self.subscription_plans.insert().values(
                SubscriptionID=subscription_id,
                PackageID=package["PackageID"],
                PackageName=package["Name"],
                NodeGroupID=package["NodeGroupID"],
                NodeGroupName=package["NodeGroupName"],
                QuotaBytes=int(package["QuotaBytes"] or 0),
                DurationDays=duration_days,
                Price=package["Price"],
                Currency=package["Currency"],
            ))

            for node in targets:
                private_status, private_key = GenerateWireguardPrivateKey()
                if not private_status:
                    raise RuntimeError("Unable to generate WireGuard private key")
                public_status, public_key = GenerateWireguardPublicKey(private_key)
                if not public_status:
                    raise RuntimeError("Unable to generate WireGuard public key")

                peer_id = str(uuid.uuid4())
                peer_name = f"{subscription_name}-{node['Name']}-{node['InterfaceName']}"
                conn.execute(self.subscription_peers.insert().values(
                    SubscriptionPeerID=peer_id,
                    SubscriptionID=subscription_id,
                    NodeID=node["NodeID"],
                    RemotePeerID=public_key,
                    Name=peer_name,
                    InterfaceName=node["InterfaceName"],
                    ClientPublicKey=public_key,
                    ClientPrivateKeyEncrypted=self._encrypt(private_key),
                    DNS="1.1.1.1",
                    MTU=1420,
                    AllowedIPs="0.0.0.0/0, ::/0",
                    Status="provisioning",
                    UpdatedAt=now,
                ))
                job_id = self._queue_job(
                    conn, node["NodeID"], "CREATE_PEER",
                    {
                        "subscription_peer_id": peer_id,
                        "interface": node["InterfaceName"],
                        "peer_name": peer_name,
                        "public_key": public_key,
                    },
                    subscription_id=subscription_id,
                    subscription_peer_id=peer_id,
                    idempotency_key=f"create:{peer_id}",
                )
                created_jobs.append({
                    "node_id": node["NodeID"],
                    "subscription_peer_id": peer_id,
                    "job_id": job_id,
                })

        return {
            "subscription_id": subscription_id,
            "subscription_token": token,
            "subscription_path": f"/sub/{subscription_id}.{token}",
            "package_id": package_id,
            "provisioned_nodes": len(created_jobs),
            "provisioned_configurations": len(created_jobs),
            "jobs": created_jobs,
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
                plan = conn.execute(
                    self.subscription_plans.select().where(
                        self.subscription_plans.c.SubscriptionID == subscription["SubscriptionID"]
                    )
                ).mappings().fetchone()
                item["Plan"] = self._row(plan) if plan else None
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
            values["QuotaBytes"] = self._quota_bytes(data.get("quota_gb"))
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
            subscription_query = self.subscriptions.select().where(
                self.subscriptions.c.SubscriptionID == subscription_id
            )
            if self.engine.dialect.name == "postgresql":
                subscription_query = subscription_query.with_for_update()
            existing = conn.execute(subscription_query).mappings().fetchone()
            if not existing:
                return False
            if "MaxPeers" in values:
                peer_count = conn.execute(
                    db.select(db.func.count()).select_from(self.subscription_peers).where(
                        db.and_(
                            self.subscription_peers.c.SubscriptionID == subscription_id,
                            self.subscription_peers.c.Status != "deleted",
                        )
                    )
                ).scalar_one()
                if peer_count > values["MaxPeers"]:
                    raise ValueError(
                        "Maximum peers cannot be lower than the current configuration count"
                    )
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
                operation = "ENABLE_PEER" if desired_status == "active" else "DISABLE_PEER"
                status_filter = (
                    self.subscription_peers.c.Status == "disabled"
                    if desired_status == "active"
                    else self.subscription_peers.c.Status.notin_(("disabled", "deleted", "deleting"))
                )
                peers = conn.execute(
                    self.subscription_peers.select().where(
                        db.and_(
                            self.subscription_peers.c.SubscriptionID == subscription_id,
                            status_filter,
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
        try:
            for dns_server in str(dns).split(","):
                ipaddress.ip_address(dns_server.strip())
        except ValueError as exc:
            raise ValueError("DNS servers must be valid IP addresses") from exc

        self.enforce_limits()
        with self.engine.begin() as conn:
            subscription_query = self.subscriptions.select().where(
                self.subscriptions.c.SubscriptionID == subscription_id
            )
            if self.engine.dialect.name == "postgresql":
                subscription_query = subscription_query.with_for_update()
            subscription = conn.execute(subscription_query).mappings().fetchone()
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
            unique_node_ids = sorted(dict.fromkeys(str(node_id) for node_id in node_ids))
            plan = conn.execute(
                self.subscription_plans.select().where(
                    self.subscription_plans.c.SubscriptionID == subscription_id
                )
            ).mappings().fetchone()
            if plan:
                allowed_node_ids = set(conn.execute(
                    db.select(self.node_group_members.c.NodeID).where(
                        self.node_group_members.c.NodeGroupID == plan["NodeGroupID"]
                    )
                ).scalars().all())
                if any(node_id not in allowed_node_ids for node_id in unique_node_ids):
                    raise ValueError("Package subscriptions can only use nodes from their node group")
            if current_count + len(unique_node_ids) > int(subscription["MaxPeers"]):
                raise ValueError("Subscription maximum peer count would be exceeded")

            created = []
            for node_id in unique_node_ids:
                node_query = self.nodes.select().where(
                    db.and_(self.nodes.c.NodeID == node_id, self.nodes.c.RevokedAt.is_(None))
                )
                if self.engine.dialect.name == "postgresql":
                    node_query = node_query.with_for_update()
                node = conn.execute(node_query).mappings().fetchone()
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
        if operation not in {"CREATE_PEER", "ENABLE_PEER", "DISABLE_PEER", "DELETE_PEER"}:
            raise ValueError("Unsupported peer operation")
        with self.engine.begin() as conn:
            peer = conn.execute(
                self.subscription_peers.select().where(
                    self.subscription_peers.c.SubscriptionPeerID == subscription_peer_id
                )
            ).mappings().fetchone()
            if not peer:
                raise ValueError("Subscription peer does not exist")
            if operation in {"CREATE_PEER", "ENABLE_PEER"}:
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
                    Status=(
                        "deleting" if operation == "DELETE_PEER"
                        else "provisioning" if operation == "CREATE_PEER"
                        else "updating"
                    ),
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
                payload = json.loads(job["Payload"])
                if job["Operation"] == "CREATE_PEER" and job["SubscriptionPeerID"]:
                    peer_secret = conn.execute(
                        db.select(self.subscription_peers.c.PresharedKeyEncrypted).where(
                            self.subscription_peers.c.SubscriptionPeerID
                            == job["SubscriptionPeerID"]
                        )
                    ).scalar_one_or_none()
                    if peer_secret:
                        payload["preshared_key"] = self._decrypt(peer_secret)
                if job["Operation"] in {"APPLY_OUTBOUND", "REMOVE_OUTBOUND"}:
                    outbound = conn.execute(
                        self.outbounds.select().where(
                            self.outbounds.c.OutboundID == payload.get("outbound_id")
                        )
                    ).mappings().fetchone()
                    if outbound:
                        payload.update({
                            "name": outbound["Name"],
                            "interface": outbound["InterfaceName"],
                            "source_interface": outbound["SourceInterface"],
                            "source_address_pool": outbound["SourceAddressPool"],
                        })
                        if job["Operation"] == "APPLY_OUTBOUND":
                            payload["configuration"] = self._decrypt(
                                outbound["ConfigurationEncrypted"]
                            )
                item["Payload"] = payload
                result.append(item)
        return result

    def complete_job(self, node_id, job_id, success, result=None, error_message=None):
        result = result if isinstance(result, dict) else {}
        now = datetime.now()
        with self.engine.begin() as conn:
            job_query = self.node_jobs.select().where(
                db.and_(self.node_jobs.c.JobID == job_id, self.node_jobs.c.NodeID == node_id)
            )
            if self.engine.dialect.name == "postgresql":
                job_query = job_query.with_for_update()
            job = conn.execute(job_query).mappings().fetchone()
            if not job:
                raise ValueError("Job does not exist")
            if job["Status"] in {"completed", "failed"}:
                return True

            # Invalid success data is a failed node attempt, not a permanently
            # completed job. This prevents malformed CREATE_PEER responses from
            # being leased forever after the transaction validation rolls back.
            if success and job["Operation"] == "CREATE_PEER":
                address = str(result.get("address") or "")
                server_public_key = str(result.get("server_public_key") or "")
                preshared_key = str(result.get("preshared_key") or "")
                endpoint = str(result.get("endpoint") or "")
                try:
                    parsed_address = ipaddress.ip_interface(address)
                    if parsed_address.version != 4:
                        raise ValueError
                except ValueError:
                    success = False
                    error_message = "Node returned an invalid IPv4 peer address"
                if success and not self.WIREGUARD_KEY_PATTERN.fullmatch(server_public_key):
                    success = False
                    error_message = "Node returned an invalid WireGuard public key"
                if (success and preshared_key
                        and not self.WIREGUARD_KEY_PATTERN.fullmatch(preshared_key)):
                    success = False
                    error_message = "Node returned an invalid WireGuard preshared key"
                if success:
                    try:
                        endpoint = self._endpoint(endpoint)
                    except ValueError:
                        success = False
                        error_message = "Node returned an invalid public endpoint"

            if not success and int(job["Attempts"] or 0) < self.JOB_MAX_ATTEMPTS:
                conn.execute(
                    self.node_jobs.update().values(
                        Status="leased",
                        CompletedAt=None,
                        LeaseUntil=now + timedelta(seconds=self.JOB_RETRY_SECONDS),
                        ErrorMessage=str(error_message or "Node operation failed")[:4000],
                    ).where(self.node_jobs.c.JobID == job_id)
                )
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
            if job["Operation"] in {"APPLY_OUTBOUND", "REMOVE_OUTBOUND"}:
                payload = json.loads(job["Payload"])
                outbound_id = payload.get("outbound_id")
                if outbound_id:
                    conn.execute(
                        self.outbounds.update().values(
                            Status=(
                                "active" if success and job["Operation"] == "APPLY_OUTBOUND"
                                else "disabled" if success
                                else "error"
                            ),
                            LastError=None if success else str(error_message or "Node operation failed")[:4000],
                            UpdatedAt=now,
                        ).where(self.outbounds.c.OutboundID == outbound_id)
                    )
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
                    "PresharedKeyEncrypted": self._encrypt(preshared_key),
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
        incoming = samples[:1000] if isinstance(samples, list) else []
        # A stable peer order prevents two concurrent batched reports from
        # acquiring PostgreSQL row locks in opposite order.
        incoming = sorted(
            incoming,
            key=lambda sample: str(sample.get("subscription_peer_id") or "")
            if isinstance(sample, dict) else "",
        )
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                # Usage, reset, and limit enforcement all lock subscription
                # rows before peer rows. Stable ordering keeps reports from
                # separate nodes exact without creating lock cycles.
                candidate_peer_ids = sorted({
                    str(sample.get("subscription_peer_id"))
                    for sample in incoming
                    if isinstance(sample, dict) and sample.get("subscription_peer_id")
                })
                if candidate_peer_ids:
                    subscription_ids = sorted(set(conn.execute(
                        db.select(self.subscription_peers.c.SubscriptionID).where(
                            db.and_(
                                self.subscription_peers.c.NodeID == node_id,
                                self.subscription_peers.c.SubscriptionPeerID.in_(
                                    candidate_peer_ids
                                ),
                            )
                        )
                    ).scalars().all()))
                    if subscription_ids:
                        conn.execute(
                            db.select(self.subscriptions.c.SubscriptionID).where(
                                self.subscriptions.c.SubscriptionID.in_(subscription_ids)
                            ).order_by(
                                self.subscriptions.c.SubscriptionID
                            ).with_for_update()
                        ).fetchall()
            for sample in incoming:
                if not isinstance(sample, dict):
                    continue
                peer_id = sample.get("subscription_peer_id")
                session_id = str(sample.get("session_id") or "")[:64]
                try:
                    sequence = int(sample.get("sequence", 0))
                    rx_bytes = int(sample.get("rx_bytes", 0))
                    tx_bytes = int(sample.get("tx_bytes", 0))
                except (TypeError, ValueError):
                    continue
                if (not peer_id or not session_id or sequence < 1 or
                        rx_bytes < 0 or tx_bytes < 0 or
                        rx_bytes > 2 ** 63 - 1 or tx_bytes > 2 ** 63 - 1):
                    continue
                peer_query = self.subscription_peers.select().where(
                    db.and_(
                        self.subscription_peers.c.SubscriptionPeerID == peer_id,
                        self.subscription_peers.c.NodeID == node_id,
                    )
                )
                # PostgreSQL can process reports for separate peers in parallel,
                # but serializes accounting for the same peer. Without this lock,
                # two workers could both calculate a delta from the same baseline.
                if self.engine.dialect.name == "postgresql":
                    peer_query = peer_query.with_for_update()
                peer = conn.execute(peer_query).mappings().fetchone()
                if not peer:
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

                last_rx = int(peer["LastRxBytes"] or 0)
                last_tx = int(peer["LastTxBytes"] or 0)
                if peer["LastSessionID"] == session_id and sequence <= int(peer["LastSequence"] or 0):
                    continue
                if peer["LastSessionID"] and peer["LastSessionID"] != session_id:
                    # Never switch back to a session already seen before. This
                    # protects usage from delayed reports after an agent restart.
                    stale_session = conn.execute(
                        self.traffic_reports.select().where(
                            db.and_(
                                self.traffic_reports.c.NodeID == node_id,
                                self.traffic_reports.c.SubscriptionPeerID == peer_id,
                                self.traffic_reports.c.SessionID == session_id,
                            )
                        ).limit(1)
                    ).fetchone()
                    if stale_session:
                        continue
                delta_rx = rx_bytes - last_rx if rx_bytes >= last_rx else rx_bytes
                delta_tx = tx_bytes - last_tx if tx_bytes >= last_tx else tx_bytes
                delta = max(0, delta_rx) + max(0, delta_tx)
                if delta > 2 ** 63 - 1:
                    continue

                report_id = str(uuid.uuid4())
                conn.execute(self.traffic_reports.insert().values(
                    ReportID=report_id,
                    NodeID=node_id,
                    SubscriptionPeerID=peer_id,
                    SessionID=session_id,
                    SequenceNumber=sequence,
                    RxBytes=rx_bytes,
                    TxBytes=tx_bytes,
                    DeltaBytes=delta,
                ))
                # LastSequence is the idempotency baseline for the active
                # session. Keep only its newest report; retain one row for each
                # older session so a delayed report can never switch back to it.
                # This bounds normal table growth to agent restarts, not one row
                # per peer every reporting interval.
                conn.execute(
                    self.traffic_reports.delete().where(
                        db.and_(
                            self.traffic_reports.c.NodeID == node_id,
                            self.traffic_reports.c.SubscriptionPeerID == peer_id,
                            self.traffic_reports.c.SessionID == session_id,
                            self.traffic_reports.c.ReportID != report_id,
                        )
                    )
                )
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
            query = self.subscriptions.select().where(
                self.subscriptions.c.Status == "active"
            ).order_by(self.subscriptions.c.SubscriptionID)
            if self.engine.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            subscriptions = conn.execute(query).mappings().fetchall()
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
                            self.subscription_peers.c.Status.notin_(("disabled", "deleted", "deleting")),
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
