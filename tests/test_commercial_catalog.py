import os
import sys
import tempfile
import unittest
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
        with self.assertRaisesRegex(ValueError, "At least one node"):
            self.manager.create_node_group("Empty", [])
        with self.assertRaisesRegex(ValueError, "Node does not exist"):
            self.manager.create_node_group("Unknown", ["missing-node"])


if __name__ == "__main__":
    unittest.main()
