import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import sqlalchemy as db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import modules.CommercialSubscriptions as commercial_module  # noqa: E402


class CommercialCatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.engine = db.create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        self.patches = [
            mock.patch.dict(os.environ, {"CONFIGURATION_PATH": self.temp_directory.name}),
            mock.patch.object(commercial_module, "CreateEngine", return_value=self.engine),
            mock.patch.object(
                commercial_module, "GenerateWireguardPrivateKey",
                return_value=(True, "A" * 43 + "="),
            ),
            mock.patch.object(
                commercial_module, "GenerateWireguardPublicKey",
                return_value=(True, "B" * 43 + "="),
            ),
        ]
        for patcher in self.patches:
            patcher.start()
        self.manager = commercial_module.CommercialSubscriptions()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.engine.dispose()
        self.temp_directory.cleanup()

    def test_package_subscription_provisions_every_group_node_and_keeps_snapshot(self):
        germany = self.manager.create_node(
            "germany-1", "Germany", "de.example.com:51820", 100,
        )
        finland = self.manager.create_node(
            "finland-1", "Finland", "fi.example.com:51820", 100,
        )
        group = self.manager.create_node_group(
            "Europe Duo", [germany["node_id"], finland["node_id"]], "Two locations",
        )
        package = self.manager.create_package(
            "30 GB / 30 days", group["node_group_id"], 30, 30, 490000, "IRT",
        )

        created = self.manager.create_subscription_from_package(
            "client-1", package["package_id"], "Customer plan",
        )

        self.assertEqual(created["provisioned_nodes"], 2)
        subscription = self.manager.list_subscriptions()[0]
        self.assertEqual(subscription["QuotaGB"], 30)
        self.assertEqual(len(subscription["Peers"]), 2)
        self.assertEqual(subscription["Plan"]["NodeGroupName"], "Europe Duo")
        self.assertEqual(subscription["Plan"]["Price"], 490000.0)
        self.assertIsNotNone(subscription["ExpiresAt"])

        self.manager.update_package(
            package["package_id"], {"price": 590000, "quota_gb": 40},
        )
        self.assertEqual(self.manager.list_packages()[0]["QuotaGB"], 40)
        self.assertEqual(
            self.manager.list_subscriptions()[0]["Plan"]["Price"], 490000.0,
        )

    def test_node_group_requires_valid_nodes(self):
        with self.assertRaisesRegex(ValueError, "valid host and port"):
            self.manager.create_node("invalid-endpoint", "Test", "example.com", 1)
        with self.assertRaisesRegex(ValueError, "At least one node"):
            self.manager.create_node_group("Empty", [])
        with self.assertRaisesRegex(ValueError, "Node does not exist"):
            self.manager.create_node_group("Unknown", ["missing-node"])

    def _subscription_peer(self, quota_gb=1):
        node = self.manager.create_node(
            "traffic-node", "Test", "test.example.com:51820", 100,
        )
        group = self.manager.create_node_group("Traffic", [node["node_id"]])
        package = self.manager.create_package(
            "Traffic package", group["node_group_id"], quota_gb, 30, 1, "USD",
        )
        created = self.manager.create_subscription_from_package(
            "client-traffic", package["package_id"],
        )
        peer = self.manager.list_subscriptions()[0]["Peers"][0]
        return node, created, peer

    def test_traffic_accounting_is_idempotent_and_handles_counter_resets(self):
        node, created, peer = self._subscription_peer()
        peer_id = peer["SubscriptionPeerID"]
        node_id = node["node_id"]

        first = [{
            "subscription_peer_id": peer_id, "session_id": "session-a", "sequence": 1,
            "rx_bytes": 100, "tx_bytes": 50,
        }]
        self.assertEqual(self.manager.record_traffic(node_id, first), 1)
        self.assertEqual(self.manager.record_traffic(node_id, first), 0)

        # A skipped report is recovered from cumulative WireGuard counters.
        self.assertEqual(self.manager.record_traffic(node_id, [{
            "subscription_peer_id": peer_id, "session_id": "session-a", "sequence": 3,
            "rx_bytes": 250, "tx_bytes": 90,
        }]), 1)
        # An interface reset lowers counters; the new counters are counted once.
        self.assertEqual(self.manager.record_traffic(node_id, [{
            "subscription_peer_id": peer_id, "session_id": "session-a", "sequence": 4,
            "rx_bytes": 20, "tx_bytes": 10,
        }]), 1)
        # A process restart with unchanged kernel counters adds no usage.
        self.assertEqual(self.manager.record_traffic(node_id, [{
            "subscription_peer_id": peer_id, "session_id": "session-b", "sequence": 1,
            "rx_bytes": 20, "tx_bytes": 10,
        }]), 1)
        # A delayed report from the prior session must never switch the baseline back.
        self.assertEqual(self.manager.record_traffic(node_id, [{
            "subscription_peer_id": peer_id, "session_id": "session-a", "sequence": 5,
            "rx_bytes": 1000, "tx_bytes": 1000,
        }]), 0)

        subscription = self.manager.list_subscriptions()[0]
        self.assertEqual(subscription["UsedBytes"], 370)
        self.manager.update_subscription(created["subscription_id"], {"reset_usage": True})
        self.assertEqual(self.manager.record_traffic(node_id, [{
            "subscription_peer_id": peer_id, "session_id": "session-b", "sequence": 2,
            "rx_bytes": 20, "tx_bytes": 10,
        }]), 1)
        self.assertEqual(self.manager.list_subscriptions()[0]["UsedBytes"], 0)
        self.manager.record_traffic(node_id, [{
            "subscription_peer_id": peer_id, "session_id": "session-b", "sequence": 3,
            "rx_bytes": 30, "tx_bytes": 15,
        }])
        self.assertEqual(self.manager.list_subscriptions()[0]["UsedBytes"], 15)
        with self.engine.connect() as conn:
            report_count = conn.execute(
                db.select(db.func.count()).select_from(self.manager.traffic_reports)
            ).scalar_one()
        self.assertEqual(report_count, 2)

    def test_quota_crossing_disables_every_active_peer(self):
        node, created, peer = self._subscription_peer()
        with self.engine.begin() as conn:
            conn.execute(self.manager.subscriptions.update().values(QuotaBytes=100))
            conn.execute(self.manager.subscription_peers.update().values(Status="active"))

        self.manager.record_traffic(node["node_id"], [{
            "subscription_peer_id": peer["SubscriptionPeerID"],
            "session_id": "quota-session", "sequence": 1,
            "rx_bytes": 80, "tx_bytes": 30,
        }])
        subscription = self.manager.list_subscriptions()[0]
        self.assertEqual(subscription["Status"], "quota_exceeded")
        jobs = self.manager.lease_jobs(node["node_id"])
        self.assertIn("DISABLE_PEER", [job["Operation"] for job in jobs])

    def test_expiration_queues_disable_even_while_peer_is_still_provisioning(self):
        node, created, _peer = self._subscription_peer()
        with self.engine.begin() as conn:
            conn.execute(
                self.manager.subscriptions.update().values(
                    ExpiresAt=datetime.now() - timedelta(seconds=1)
                ).where(
                    self.manager.subscriptions.c.SubscriptionID == created["subscription_id"]
                )
            )
        self.manager.enforce_limits()
        subscription = self.manager.list_subscriptions()[0]
        self.assertEqual(subscription["Status"], "expired")
        operations = [job["Operation"] for job in self.manager.lease_jobs(node["node_id"])]
        self.assertEqual(operations, ["CREATE_PEER", "DISABLE_PEER"])

    def test_interface_targets_create_one_config_per_interface_and_enforce_capacity(self):
        node = self.manager.create_node("multi-interface", "DE", "de.example.com:51820", 2)
        self.manager.heartbeat(node["node_id"], {
            "agent_version": "test", "public_endpoint": "de.example.com:51820",
            "interfaces": [
                {"name": "wg0", "address_pool": "10.80.0.0/24", "status": "up"},
                {"name": "wg1", "address_pool": "10.81.0.0/24", "status": "up"},
            ],
        })
        group = self.manager.create_node_group("Both interfaces", targets=[
            {"node_id": node["node_id"], "interface": "wg0"},
            {"node_id": node["node_id"], "interface": "wg1"},
        ])
        package = self.manager.create_package(
            "Two configs", group["node_group_id"], 10, 10, 10, "USD",
        )
        created = self.manager.create_subscription_from_package("client-one", package["package_id"])
        self.assertEqual(created["provisioned_configurations"], 2)
        self.assertEqual(
            {peer["InterfaceName"] for peer in self.manager.list_subscriptions()[0]["Peers"]},
            {"wg0", "wg1"},
        )
        with self.assertRaisesRegex(ValueError, "current configuration count"):
            self.manager.update_subscription(created["subscription_id"], {"max_peers": 1})
        with self.assertRaisesRegex(ValueError, "capacity is exhausted"):
            self.manager.create_subscription_from_package("client-two", package["package_id"])

    def test_usage_is_shared_across_nodes_and_counts_rx_plus_tx(self):
        first = self.manager.create_node("first", "DE", "de.example.com:51820", 10)
        second = self.manager.create_node("second", "FI", "fi.example.com:51820", 10)
        group = self.manager.create_node_group(
            "Shared usage", [first["node_id"], second["node_id"]],
        )
        package = self.manager.create_package(
            "Shared quota", group["node_group_id"], 1, 30, 1, "USD",
        )
        self.manager.create_subscription_from_package("shared-client", package["package_id"])
        peers = self.manager.list_subscriptions()[0]["Peers"]
        for index, peer in enumerate(peers, start=1):
            self.manager.record_traffic(peer["NodeID"], [{
                "subscription_peer_id": peer["SubscriptionPeerID"],
                "session_id": f"node-{index}", "sequence": 1,
                "rx_bytes": index * 100, "tx_bytes": index * 10,
            }])
        self.assertEqual(self.manager.list_subscriptions()[0]["UsedBytes"], 330)

    def test_outbound_secret_is_encrypted_and_only_revealed_to_the_node_job(self):
        node = self.manager.create_node("outbound-node", "DE", "de.example.com:51820", 10)
        self.manager.heartbeat(node["node_id"], {
            "interfaces": [{"name": "wg0", "address_pool": "10.90.0.0/24", "status": "up"}],
        })
        configuration = """[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 172.16.0.2/32

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
Endpoint = upstream.example.com:51820
AllowedIPs = 0.0.0.0/0
"""
        created = self.manager.create_outbound(
            node["node_id"], "upstream", "wgo0", "wg0", "10.90.0.0/24", configuration,
        )
        listed = self.manager.list_outbounds()[0]
        self.assertNotIn("ConfigurationEncrypted", listed)
        with self.engine.connect() as conn:
            encrypted = conn.execute(
                self.manager.outbounds.select().where(
                    self.manager.outbounds.c.OutboundID == created["outbound_id"]
                )
            ).mappings().one()["ConfigurationEncrypted"]
        self.assertNotIn("PrivateKey", encrypted)
        job = self.manager.lease_jobs(node["node_id"])[0]
        self.assertEqual(job["Operation"], "APPLY_OUTBOUND")
        self.assertIn("PrivateKey", job["Payload"]["configuration"])
        self.manager.complete_job(node["node_id"], job["JobID"], True)
        self.assertEqual(self.manager.list_outbounds()[0]["Status"], "active")

        # Losing the agent state must re-apply a panel-authoritative outbound;
        # its private configuration is still revealed only in the leased job.
        self.manager.heartbeat(node["node_id"], {
            "agent_session": "outbound-recovery-session",
            "interfaces": [{
                "name": "wg0", "address_pool": "10.90.0.0/24", "status": "up",
            }],
            "peers": [],
            "outbounds": [],
        })
        recovery_jobs = self.manager.lease_jobs(node["node_id"])
        self.assertEqual(len(recovery_jobs), 1)
        self.assertEqual(recovery_jobs[0]["Operation"], "APPLY_OUTBOUND")
        self.assertIn("PrivateKey", recovery_jobs[0]["Payload"]["configuration"])

    def test_node_job_retries_three_times_before_marking_peer_error(self):
        node, _created, peer = self._subscription_peer()
        job = self.manager.lease_jobs(node["node_id"])[0]

        # A malformed success response is handled as a retryable node failure.
        self.manager.complete_job(node["node_id"], job["JobID"], True, {})
        with self.engine.connect() as conn:
            stored = conn.execute(
                self.manager.node_jobs.select().where(
                    self.manager.node_jobs.c.JobID == job["JobID"]
                )
            ).mappings().one()
        self.assertEqual(stored["Status"], "leased")
        self.assertIn("invalid IPv4", stored["ErrorMessage"])

        for attempt in (2, 3):
            with self.engine.begin() as conn:
                conn.execute(
                    self.manager.node_jobs.update().values(
                        LeaseUntil=datetime.now() - timedelta(seconds=1)
                    ).where(self.manager.node_jobs.c.JobID == job["JobID"])
                )
            leased = self.manager.lease_jobs(node["node_id"])
            self.assertEqual([item["JobID"] for item in leased], [job["JobID"]])
            self.manager.complete_job(
                node["node_id"], job["JobID"], False,
                error_message=f"attempt {attempt} failed",
            )

        with self.engine.connect() as conn:
            stored = conn.execute(
                self.manager.node_jobs.select().where(
                    self.manager.node_jobs.c.JobID == job["JobID"]
                )
            ).mappings().one()
            stored_peer = conn.execute(
                self.manager.subscription_peers.select().where(
                    self.manager.subscription_peers.c.SubscriptionPeerID
                    == peer["SubscriptionPeerID"]
                )
            ).mappings().one()
        self.assertEqual(stored["Attempts"], 3)
        self.assertEqual(stored["Status"], "failed")
        self.assertEqual(stored_peer["Status"], "error")

        retry_job_id = self.manager.queue_peer_action(
            peer["SubscriptionPeerID"], "CREATE_PEER",
        )
        retry_jobs = self.manager.lease_jobs(node["node_id"])
        self.assertEqual([item["JobID"] for item in retry_jobs], [retry_job_id])
        self.assertEqual(retry_jobs[0]["Operation"], "CREATE_PEER")

    def test_heartbeat_recovers_lost_state_with_the_same_address(self):
        node, _created, _peer = self._subscription_peer()
        original_job = self.manager.lease_jobs(node["node_id"])[0]
        self.manager.complete_job(node["node_id"], original_job["JobID"], True, {
            "address": "10.88.0.2/24",
            "server_public_key": "C" * 43 + "=",
            "preshared_key": "D" * 43 + "=",
            "endpoint": "test.example.com:51820",
        })
        active_peer = self.manager.list_subscriptions()[0]["Peers"][0]

        heartbeat = {
            "agent_session": "fresh-agent-session",
            "interfaces": [{
                "name": "wg0", "address_pool": "10.88.0.0/24", "status": "up",
            }],
            "peers": [],
        }
        self.manager.heartbeat(node["node_id"], heartbeat)
        self.manager.heartbeat(node["node_id"], heartbeat)
        recovery_jobs = self.manager.lease_jobs(node["node_id"])
        self.assertEqual(len(recovery_jobs), 1)
        self.assertEqual(recovery_jobs[0]["Operation"], "CREATE_PEER")
        self.assertEqual(recovery_jobs[0]["Payload"]["address"], active_peer["Address"])
        self.assertEqual(recovery_jobs[0]["Payload"]["preshared_key"], "D" * 43 + "=")
        self.assertEqual(
            recovery_jobs[0]["Payload"]["subscription_peer_id"],
            active_peer["SubscriptionPeerID"],
        )

    def test_heartbeat_repairs_enabled_state_and_removes_orphans(self):
        node, _created, _peer = self._subscription_peer()
        original_job = self.manager.lease_jobs(node["node_id"])[0]
        self.manager.complete_job(node["node_id"], original_job["JobID"], True, {
            "address": "10.88.0.2/24",
            "server_public_key": "C" * 43 + "=",
            "endpoint": "test.example.com:51820",
        })
        active_peer = self.manager.list_subscriptions()[0]["Peers"][0]
        self.manager.heartbeat(node["node_id"], {
            "agent_session": "drift-session",
            "interfaces": [],
            "peers": [{
                "subscription_peer_id": active_peer["SubscriptionPeerID"],
                "interface": active_peer["InterfaceName"],
                "public_key": active_peer["ClientPublicKey"],
                "address": active_peer["Address"],
                "enabled": False,
            }, {
                "subscription_peer_id": "orphan-peer",
                "interface": "wg0",
                "public_key": "D" * 43 + "=",
                "address": "10.88.0.99/24",
                "enabled": True,
            }],
        })
        jobs = self.manager.lease_jobs(node["node_id"])
        self.assertEqual(
            {job["Operation"] for job in jobs}, {"ENABLE_PEER", "DELETE_PEER"},
        )
        orphan_job = next(job for job in jobs if job["Operation"] == "DELETE_PEER")
        self.assertIsNone(orphan_job["SubscriptionPeerID"])

    def test_public_subscription_requires_token_and_only_returns_active_peers(self):
        node, created, _peer = self._subscription_peer()
        job = self.manager.lease_jobs(node["node_id"])[0]
        self.manager.complete_job(node["node_id"], job["JobID"], True, {
            "address": "10.88.0.2/24",
            "server_public_key": "C" * 43 + "=",
            "preshared_key": "D" * 43 + "=",
            "endpoint": "test.example.com:51820",
        })

        payload, error = self.manager.get_public_subscription(
            created["subscription_id"], created["subscription_token"],
        )
        self.assertIsNone(error)
        self.assertEqual(len(payload["files"]), 1)
        self.assertIn("PrivateKey =", payload["files"][0]["configuration"])
        self.assertNotIn("TokenHash", payload["subscription"])

        payload, error = self.manager.get_public_subscription(
            created["subscription_id"], "wrong-token",
        )
        self.assertIsNone(payload)
        self.assertEqual(error, "Subscription does not exist")


if __name__ == "__main__":
    unittest.main()
