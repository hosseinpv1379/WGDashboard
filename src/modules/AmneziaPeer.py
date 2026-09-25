import os
import datetime
from flask import current_app
import random
import re
import subprocess
import uuid

from flask import current_app
from .Peer import Peer
from .Utilities import CheckAddress, ValidateDNSAddress, GenerateWireguardPublicKey, ParseOptionalDateTime


class AmneziaPeer(Peer):
    def __init__(self, tableData, configuration):
        super().__init__(tableData, configuration)


    def updatePeer(self, name: str, private_key: str,
                   preshared_key: str,
                   dns_addresses: str,
                   allowed_ip: str,
                   endpoint_allowed_ip: str,
                   mtu: int,
                   keepalive: int,
                   notes: str,
                   quota_gb: float = 0,
                   expires_at=None
                   ) -> tuple[bool, str | None]:

        if not self.configuration.getStatus():
            self.configuration.toggleConfiguration()

        # Before we do any compute, let us check if the given endpoint allowed ip is valid at all
        if not CheckAddress(endpoint_allowed_ip):
            return False, f"Endpoint Allowed IPs format is incorrect"

        peers = []
        for peer in self.configuration.getPeersList():
            # Make sure to exclude your own data when updating since its not really relevant
            if peer.id != self.id:
                continue
            peers.append(peer)

        used_allowed_ips = []
        for peer in peers:
            ips = peer.allowed_ip.split(',')
            ips = [ip.strip() for ip in ips]
            used_allowed_ips.append(ips)

        if allowed_ip in used_allowed_ips:
            return False, "Allowed IP already taken by another peer"

        if not ValidateDNSAddress(dns_addresses):
            return False, f"DNS IP-Address or FQDN is incorrect"

        if isinstance(mtu, str):
            mtu = 0

        if isinstance(keepalive, str):
            keepalive = 0
        
        if mtu not in range(0, 1461):
            return False, "MTU format is not correct"

        if keepalive < 0:
            return False, "Persistent Keepalive format is not correct"

        try:
            quota_gb = float(quota_gb or 0)
        except (TypeError, ValueError):
            return False, "Data quota must be a number"
        if quota_gb < 0:
            return False, "Data quota cannot be negative"

        try:
            expires_at = ParseOptionalDateTime(expires_at)
        except ValueError as exc:
            return False, str(exc)

        if len(private_key) > 0:
            pubKey = GenerateWireguardPublicKey(private_key)
            if not pubKey[0] or pubKey[1] != self.id:
                return False, "Private key does not match with the public key"
    
        try:
            rand = random.Random()
            uid = str(uuid.UUID(int=rand.getrandbits(128), version=4))
            psk_exist = len(preshared_key) > 0

            if psk_exist:
                with open(uid, "w+") as f:
                    f.write(preshared_key)

            newAllowedIPs = allowed_ip.replace(" ", "")
            if not CheckAddress(newAllowedIPs):
                return False, "Allowed IPs entry format is incorrect"

            command = [self.configuration.Protocol, "set", self.configuration.Name, "peer", self.id, "allowed-ips", newAllowedIPs, "preshared-key", uid if psk_exist else "/dev/null"]

            updateAllowedIp = subprocess.check_output(command, stderr=subprocess.STDOUT)

            if psk_exist: os.remove(uid)

            if len(updateAllowedIp.decode().strip("\n")) != 0:
                current_app.logger.error(f"Update peer failed when updating Allowed IPs.\nInput: {newAllowedIPs}\nOutput: {updateAllowedIp.decode().strip('\n')}")
                return False, "Internal server error"

            command = [f"{self.configuration.Protocol}-quick", "save", self.configuration.Name]
            saveConfig = subprocess.check_output(command, stderr=subprocess.STDOUT)

            if f"wg showconf {self.configuration.Name}" not in saveConfig.decode().strip('\n'):
                current_app.logger.error("Update peer failed when saving the configuration")
                return False, "Internal server error"

            with self.configuration.engine.begin() as conn:
                conn.execute(
                    self.configuration.peersTable.update().values({
                        "name": name,
                        "private_key": private_key,
                        "DNS": dns_addresses,
                        "endpoint_allowed_ip": endpoint_allowed_ip,
                        "mtu": mtu,
                        "keepalive": keepalive,
                        "notes": notes,
                        "preshared_key": preshared_key,
                        "quota_gb": quota_gb,
                        "quota_exceeded": 0 if quota_gb == 0 or float(self.total_data or 0) + float(self.cumu_data or 0) < quota_gb else self.quota_exceeded,
                        "quota_exceeded_at": None if quota_gb == 0 or float(self.total_data or 0) + float(self.cumu_data or 0) < quota_gb else self.quota_exceeded_at,
                        "expires_at": expires_at,
                        "expiry_exceeded": 0 if expires_at is None or expires_at > datetime.datetime.now() else self.expiry_exceeded,
                        "expiry_exceeded_at": None if expires_at is None or expires_at > datetime.datetime.now() else self.expiry_exceeded_at,
                    }).where(
                        self.configuration.peersTable.c.id == self.id
                    )
                )
            self.configuration.getPeers()
            return True, None
        except subprocess.CalledProcessError as exc:
            current_app.logger.error(f"Subprocess call failed:\n{exc.output.decode('UTF-8')}")
            return False, "Internal server error"
