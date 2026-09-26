import logging
import io
import random, shutil, sqlite3, configparser, hashlib, ipaddress, json, os, secrets, subprocess
import time, re, uuid, bcrypt, psutil, pyotp, threading
import traceback
from uuid import uuid4
from zipfile import ZipFile
from datetime import datetime, timedelta

import sqlalchemy
from jinja2 import Template
from flask import Flask, request, render_template, session, send_file, current_app
from flask_cors import CORS
from icmplib import ping, traceroute
from flask.json.provider import DefaultJSONProvider
from itertools import islice

from sqlalchemy import RowMapping

from modules.Utilities import (
    RegexMatch, StringToBoolean, ValidateDNSAddress,
    GenerateWireguardPublicKey, GenerateWireguardPrivateKey, ParseOptionalDateTime
)
from packaging import version
from modules.Email import EmailSender
from modules.DashboardLogger import DashboardLogger
from modules.PeerJob import PeerJob
from modules.SystemStatus import SystemStatus
from modules.PeerShareLinks import PeerShareLinks
from modules.PeerJobs import PeerJobs
from modules.DashboardConfig import DashboardConfig
from modules.WireguardConfiguration import WireguardConfiguration
from modules.AmneziaConfiguration import AmneziaConfiguration

from client import createClientBlueprint

from logging.config import dictConfig

from modules.DashboardClients import DashboardClients
from modules.DashboardPlugins import DashboardPlugins
from modules.DashboardWebHooks import DashboardWebHooks
from modules.NewConfigurationTemplates import NewConfigurationTemplates
from modules.CommercialSubscriptions import CommercialSubscriptions

class CustomJsonEncoder(DefaultJSONProvider):
    def __init__(self, app):
        super().__init__(app)

    def default(self, o):
        if callable(getattr(o, "toJson", None)):
            return o.toJson()
        if type(o) is RowMapping:
            return dict(o)
        if type(o) is datetime:
            return o.strftime("%Y-%m-%d %H:%M:%S")
        return super().default(self)



'''
Response Object
'''
def ResponseObject(status=True, message=None, data=None, status_code = 200) -> Flask.response_class:
    response = Flask.make_response(app, {
        "status": status,
        "message": message,
        "data": data
    })
    response.status_code = status_code
    response.content_type = "application/json"
    return response

'''
Flask App
'''
_, APP_PREFIX_INIT = DashboardConfig().GetConfig("Server", "app_prefix")
app = Flask("WGDashboard",
            template_folder=os.path.abspath("./static/dist/WGDashboardAdmin"),
            static_folder=os.path.abspath("./static/dist/WGDashboardAdmin"),
            static_url_path=APP_PREFIX_INIT if APP_PREFIX_INIT else '')

def peerInformationBackgroundThread():
    global WireguardConfigurations
    app.logger.info("Background Thread #1 Started")
    app.logger.info("Background Thread #1 PID:" + str(threading.get_native_id()))
    delay = 6
    time.sleep(10)
    while True:
        with app.app_context():
            try:
                curKeys = list(WireguardConfigurations.keys())
                for name in curKeys:
                    if name in WireguardConfigurations.keys() and WireguardConfigurations.get(name) is not None:
                        c = WireguardConfigurations.get(name)
                        if c.getStatus():
                            c.getPeersLatestHandshake()
                            c.getPeersTransfer()
                            c.getPeersEndpoint()
                            c.getPeers()
                            c.enforceExpirations()
                            if DashboardConfig.GetConfig('WireGuardConfiguration', 'peer_tracking')[1] is True:
                                print("[WGDashboard] Tracking Peers")
                                if delay == 6:
                                    if c.configurationInfo.PeerTrafficTracking:
                                        c.logPeersTraffic()
                                    if c.configurationInfo.PeerHistoricalEndpointTracking:
                                        c.logPeersHistoryEndpoint()
                            c.getRestrictedPeersList()
            except Exception as e:
                app.logger.error(f"[WGDashboard] Background Thread #1 Error", e)

        if delay == 6:
            delay = 1
        else:
            delay += 1
        time.sleep(10)

def peerJobScheduleBackgroundThread():
    with app.app_context():
        app.logger.info(f"Background Thread #2 Started")
        app.logger.info(f"Background Thread #2 PID:" + str(threading.get_native_id()))
        time.sleep(10)
        while True:
            try:
                AllPeerJobs.runJob()
                time.sleep(180)
            except Exception as e:
                app.logger.error("Background Thread #2 Error", e)

def commercialSubscriptionBackgroundThread():
    with app.app_context():
        app.logger.info("Commercial subscription enforcement thread started")
        time.sleep(10)
        while True:
            try:
                CommercialSubscriptionManager.enforce_limits()
            except Exception as exc:
                app.logger.error("Commercial subscription enforcement failed", exc_info=exc)
            time.sleep(15)

def gunicornConfig():
    _, app_ip = DashboardConfig.GetConfig("Server", "app_ip")
    _, app_port = DashboardConfig.GetConfig("Server", "app_port")
    return app_ip, app_port

def ProtocolsEnabled() -> list[str]:
    from shutil import which
    protocols = []
    if which('awg') is not None and which('awg-quick') is not None:
        protocols.append("awg")
    if which('wg') is not None and which('wg-quick') is not None:
        protocols.append("wg")
    return protocols

def InitWireguardConfigurationsList(startup: bool = False):
    if os.path.exists(DashboardConfig.GetConfig("Server", "wg_conf_path")[1]):
        confs = os.listdir(DashboardConfig.GetConfig("Server", "wg_conf_path")[1])
        confs.sort()
        for i in confs:
            if RegexMatch("^(.{1,}).(conf)$", i):
                i = i.replace('.conf', '')
                try:
                    if i in WireguardConfigurations.keys():
                        if WireguardConfigurations[i].configurationFileChanged():
                            with app.app_context():
                                WireguardConfigurations[i] = WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, i)
                    else:
                        with app.app_context():
                            WireguardConfigurations[i] = WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, i, startup=startup)
                except WireguardConfiguration.InvalidConfigurationFileException as e:
                    app.logger.error(f"{i} have an invalid configuration file.")

    if "awg" in ProtocolsEnabled():
        confs = os.listdir(DashboardConfig.GetConfig("Server", "awg_conf_path")[1])
        confs.sort()
        for i in confs:
            if RegexMatch("^(.{1,}).(conf)$", i):
                i = i.replace('.conf', '')
                try:
                    if i in WireguardConfigurations.keys():
                        if WireguardConfigurations[i].configurationFileChanged():
                            with app.app_context():
                                WireguardConfigurations[i] = AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, i)
                    else:
                        with app.app_context():
                            WireguardConfigurations[i] = AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, i, startup=startup)
                except WireguardConfiguration.InvalidConfigurationFileException as e:
                    app.logger.error(f"{i} have an invalid configuration file.")

def startThreads():
    bgThread = threading.Thread(target=peerInformationBackgroundThread, daemon=True)
    bgThread.start()
    scheduleJobThread = threading.Thread(target=peerJobScheduleBackgroundThread, daemon=True)
    scheduleJobThread.start()
    commercialThread = threading.Thread(target=commercialSubscriptionBackgroundThread, daemon=True)
    commercialThread.start()

dictConfig({
    'version': 1,
    'formatters': {'default': {
        'format': '[%(asctime)s] [%(levelname)s] in [%(module)s] %(message)s',
    }},
    'root': {
        'level': 'INFO'
    }
})


WireguardConfigurations: dict[str, WireguardConfiguration] = {}
CONFIGURATION_PATH = os.getenv('CONFIGURATION_PATH', '.')

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 5206928
app.secret_key = secrets.token_urlsafe(32)
app.json = CustomJsonEncoder(app)
with app.app_context():
    SystemStatus = SystemStatus()
    DashboardConfig = DashboardConfig()
    EmailSender = EmailSender(DashboardConfig)
    AllPeerShareLinks: PeerShareLinks = PeerShareLinks(DashboardConfig, WireguardConfigurations)
    AllPeerJobs: PeerJobs = PeerJobs(DashboardConfig, WireguardConfigurations, AllPeerShareLinks)
    DashboardLogger: DashboardLogger = DashboardLogger()
    DashboardPlugins: DashboardPlugins = DashboardPlugins(app, WireguardConfigurations)
    DashboardWebHooks: DashboardWebHooks = DashboardWebHooks(DashboardConfig)
    NewConfigurationTemplates: NewConfigurationTemplates = NewConfigurationTemplates()
    InitWireguardConfigurationsList(startup=True)
    DashboardClients: DashboardClients = DashboardClients(WireguardConfigurations)
    CommercialSubscriptionManager = CommercialSubscriptions()
    app.register_blueprint(createClientBlueprint(
        WireguardConfigurations, DashboardConfig, DashboardClients, CommercialSubscriptionManager
    ))

_, APP_PREFIX = DashboardConfig.GetConfig("Server", "app_prefix")
cors = CORS(app, resources={rf"{APP_PREFIX}/api/*": {
    "origins": "*",
    "methods": "DELETE, POST, GET, OPTIONS",
    "allow_headers": ["Content-Type", "Authorization", "wg-dashboard-apikey"]
}})
_, app_ip = DashboardConfig.GetConfig("Server", "app_ip")
_, app_port = DashboardConfig.GetConfig("Server", "app_port")
_, WG_CONF_PATH = DashboardConfig.GetConfig("Server", "wg_conf_path")

'''
API Routes
'''

@app.before_request
def auth_req():
    if request.method.lower() == 'options':
        return ResponseObject(True)        

    appPrefix = APP_PREFIX if len(APP_PREFIX) > 0 else ''
    if (request.path.startswith(f'{appPrefix}/api/node/v1/')
            or request.path.startswith(f'{appPrefix}/sub/')):
        return None

    DashboardConfig.APIAccessed = False    
    authenticationRequired = DashboardConfig.GetConfig("Server", "auth_req")[1]
    d = request.headers
    if authenticationRequired:
        apiKey = d.get('wg-dashboard-apikey')
        apiKeyEnabled = DashboardConfig.GetConfig("Server", "dashboard_api_key")[1]
        if apiKey is not None and len(apiKey) > 0 and apiKeyEnabled:
            apiKeyExist = len(list(filter(lambda x : x.Key == apiKey, DashboardConfig.DashboardAPIKeys))) == 1
            DashboardLogger.log(str(request.url), str(request.remote_addr), Message=f"API Key Access: {('true' if apiKeyExist else 'false')} - Key: {apiKey}")
            if not apiKeyExist:
                DashboardConfig.APIAccessed = False
                response = Flask.make_response(app, {
                    "status": False,
                    "message": "API Key does not exist",
                    "data": None
                })
                response.content_type = "application/json"
                response.status_code = 401
                return response
            DashboardConfig.APIAccessed = True
        else:
            DashboardConfig.APIAccessed = False
            whiteList = [
                # f'/static/', 
                '/healthz',
                f'{appPrefix}/api/health',
                f'{appPrefix}/api/validateAuthentication', 
                f'{appPrefix}/api/authenticate', 
                # f'{appPrefix}/api/getDashboardConfiguration',
                f'{appPrefix}/api/getDashboardTheme', 
                f'{appPrefix}/api/getDashboardVersion', 
                f'{appPrefix}/api/sharePeer/get', 
                f'{appPrefix}/api/isTotpEnabled', 
                f'{appPrefix}/api/locale',
            ]
        

            if (    
                    ("username" not in session or session.get("role") != "admin")
                    and (f"{appPrefix}/" != request.path and f"{appPrefix}" != request.path)
                    and not request.path.startswith(f'{appPrefix}/client')
                    and not request.path.startswith(f'{appPrefix}/img')
                    and not request.path.startswith(f'{appPrefix}/json')
                    and not request.path.startswith(f'{appPrefix}/assets')
                    and request.path not in whiteList
            ):
                response = Flask.make_response(app, {
                    "status": False,
                    "message": "Unauthorized access.",
                    "data": None
                })
                response.content_type = "application/json"
                response.status_code = 401
                return response

@app.route(f'{APP_PREFIX}/api/handshake', methods=["GET", "OPTIONS"])
def API_Handshake():
    return ResponseObject(True)


@app.get(f'{APP_PREFIX}/api/health')
@app.get('/healthz')
def API_Health():
    try:
        with CommercialSubscriptionManager.engine.connect() as connection:
            connection.execute(sqlalchemy.text("SELECT 1"))
        return ResponseObject(data={"web": "ok", "database": "ok"})
    except Exception:
        app.logger.exception("Health check failed")
        return ResponseObject(False, "Service unavailable", status_code=503)


def _authenticated_commercial_node():
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    return CommercialSubscriptionManager.authenticate_node(authorization[7:].strip())


@app.post(f'{APP_PREFIX}/api/node/v1/register')
def API_Node_Register():
    bootstrap_token = os.getenv("WGD_NODE_BOOTSTRAP_TOKEN", "")
    authorization = request.headers.get("Authorization", "")
    provided_token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not bootstrap_token or not secrets.compare_digest(provided_token, bootstrap_token):
        return ResponseObject(False, "Invalid node bootstrap token", status_code=401)
    data = request.get_json(silent=True) or {}
    try:
        created = CommercialSubscriptionManager.create_node(
            data.get("name"), data.get("region", ""), data.get("public_endpoint", ""),
            data.get("capacity", 0),
        )
        return ResponseObject(data=created, status_code=201)
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/node/v1/heartbeat')
def API_Node_Heartbeat():
    node = _authenticated_commercial_node()
    if node is None:
        return ResponseObject(False, "Unauthorized node", status_code=401)
    try:
        CommercialSubscriptionManager.heartbeat(node["NodeID"], request.get_json(silent=True) or {})
        return ResponseObject(data={"node_id": node["NodeID"], "server_time": datetime.now()})
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.get(f'{APP_PREFIX}/api/node/v1/jobs')
def API_Node_Jobs():
    node = _authenticated_commercial_node()
    if node is None:
        return ResponseObject(False, "Unauthorized node", status_code=401)
    jobs = CommercialSubscriptionManager.lease_jobs(node["NodeID"], request.args.get("limit", 20))
    return ResponseObject(data=jobs)


@app.post(f'{APP_PREFIX}/api/node/v1/jobs/<job_id>/result')
def API_Node_Job_Result(job_id):
    node = _authenticated_commercial_node()
    if node is None:
        return ResponseObject(False, "Unauthorized node", status_code=401)
    data = request.get_json(silent=True) or {}
    try:
        CommercialSubscriptionManager.complete_job(
            node["NodeID"], job_id, data.get("success") is True, data.get("result") or {},
            data.get("error"),
        )
        return ResponseObject()
    except ValueError as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/node/v1/traffic')
def API_Node_Traffic():
    node = _authenticated_commercial_node()
    if node is None:
        return ResponseObject(False, "Unauthorized node", status_code=401)
    accepted = CommercialSubscriptionManager.record_traffic(
        node["NodeID"], (request.get_json(silent=True) or {}).get("samples", [])
    )
    return ResponseObject(data={"accepted": accepted})


@app.get(f'{APP_PREFIX}/api/commercial/nodes')
def API_Commercial_Nodes():
    return ResponseObject(data=CommercialSubscriptionManager.list_nodes())


@app.post(f'{APP_PREFIX}/api/commercial/nodes')
def API_Commercial_CreateNode():
    data = request.get_json(silent=True) or {}
    try:
        return ResponseObject(data=CommercialSubscriptionManager.create_node(
            data.get("name"), data.get("region", ""), data.get("public_endpoint", ""),
            data.get("capacity", 0),
        ), status_code=201)
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/commercial/nodes/<node_id>/revoke')
def API_Commercial_RevokeNode(node_id):
    status = CommercialSubscriptionManager.revoke_node(node_id)
    return ResponseObject(status, None if status else "Node does not exist", status_code=200 if status else 404)


@app.get(f'{APP_PREFIX}/api/commercial/node-groups')
def API_Commercial_NodeGroups():
    return ResponseObject(data=CommercialSubscriptionManager.list_node_groups())


@app.post(f'{APP_PREFIX}/api/commercial/node-groups')
def API_Commercial_CreateNodeGroup():
    data = request.get_json(silent=True) or {}
    try:
        return ResponseObject(data=CommercialSubscriptionManager.create_node_group(
            data.get("name"), data.get("node_ids", []), data.get("description", ""),
            data.get("status", "active"), data.get("targets"),
        ), status_code=201)
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/commercial/node-groups/<group_id>')
def API_Commercial_UpdateNodeGroup(group_id):
    try:
        status = CommercialSubscriptionManager.update_node_group(
            group_id, request.get_json(silent=True) or {}
        )
        return ResponseObject(
            status, None if status else "Node group does not exist",
            status_code=200 if status else 404,
        )
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.get(f'{APP_PREFIX}/api/commercial/packages')
def API_Commercial_Packages():
    active_only = str(request.args.get("active_only", "false")).lower() in {"1", "true", "yes"}
    return ResponseObject(data=CommercialSubscriptionManager.list_packages(active_only=active_only))


@app.post(f'{APP_PREFIX}/api/commercial/packages')
def API_Commercial_CreatePackage():
    data = request.get_json(silent=True) or {}
    try:
        return ResponseObject(data=CommercialSubscriptionManager.create_package(
            data.get("name"), data.get("node_group_id"), data.get("quota_gb", 0),
            data.get("duration_days", 0), data.get("price", 0), data.get("currency", "IRT"),
            data.get("description", ""), data.get("status", "active"),
        ), status_code=201)
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/commercial/packages/<package_id>')
def API_Commercial_UpdatePackage(package_id):
    try:
        status = CommercialSubscriptionManager.update_package(
            package_id, request.get_json(silent=True) or {}
        )
        return ResponseObject(
            status, None if status else "Package does not exist",
            status_code=200 if status else 404,
        )
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.get(f'{APP_PREFIX}/api/commercial/outbounds')
def API_Commercial_Outbounds():
    return ResponseObject(data=CommercialSubscriptionManager.list_outbounds())


@app.post(f'{APP_PREFIX}/api/commercial/outbounds')
def API_Commercial_CreateOutbound():
    data = request.get_json(silent=True) or {}
    try:
        return ResponseObject(data=CommercialSubscriptionManager.create_outbound(
            data.get("node_id"), data.get("name"), data.get("interface"),
            data.get("source_interface", "wg0"), data.get("source_address_pool"),
            data.get("configuration"),
        ), status_code=201)
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/commercial/outbounds/<outbound_id>')
def API_Commercial_UpdateOutbound(outbound_id):
    data = request.get_json(silent=True) or {}
    try:
        job_id = CommercialSubscriptionManager.update_outbound(outbound_id, data)
        if not job_id:
            return ResponseObject(False, "Outbound does not exist", status_code=404)
        return ResponseObject(data={"job_id": job_id}, status_code=202)
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/commercial/outbounds/<outbound_id>/action')
def API_Commercial_OutboundAction(outbound_id):
    try:
        job_id = CommercialSubscriptionManager.queue_outbound_action(
            outbound_id, (request.get_json(silent=True) or {}).get("operation")
        )
        return ResponseObject(data={"job_id": job_id}, status_code=202)
    except ValueError as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.get(f'{APP_PREFIX}/api/commercial/users')
def API_Commercial_Users():
    counts = CommercialSubscriptionManager.subscription_counts_by_client()
    users = []
    for client in DashboardClients.GetAllClientsRaw():
        item = dict(client)
        item["SubscriptionCount"] = counts.get(item["ClientID"], 0)
        users.append(item)
    return ResponseObject(data=users)


@app.post(f'{APP_PREFIX}/api/commercial/users/<client_id>/delete')
def API_Commercial_DeleteUser(client_id):
    if CommercialSubscriptionManager.client_has_subscriptions(client_id):
        return ResponseObject(
            False,
            "Users with subscription history cannot be deleted",
            status_code=409,
        )
    if not DashboardClients.GetClient(client_id):
        return ResponseObject(False, "User does not exist", status_code=404)
    status = DashboardClients.DeleteClient(client_id)
    return ResponseObject(status, None if status else "Unable to delete user", status_code=200 if status else 500)


@app.get(f'{APP_PREFIX}/api/commercial/subscriptions')
def API_Commercial_Subscriptions():
    return ResponseObject(data=CommercialSubscriptionManager.list_subscriptions())


@app.post(f'{APP_PREFIX}/api/commercial/subscriptions')
def API_Commercial_CreateSubscription():
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id")
    if not DashboardClients.GetClient(client_id):
        return ResponseObject(False, "Client does not exist", status_code=400)
    try:
        if data.get("package_id"):
            created = CommercialSubscriptionManager.create_subscription_from_package(
                client_id, data.get("package_id"), data.get("name"),
            )
        else:
            created = CommercialSubscriptionManager.create_subscription(
                client_id, data.get("name"), data.get("quota_gb", 0), data.get("expires_at"),
                data.get("max_peers", 1),
            )
        created["subscription_url"] = request.host_url.rstrip("/") + APP_PREFIX + created["subscription_path"]
        return ResponseObject(data=created, status_code=201)
    except (ValueError, TypeError, RuntimeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/commercial/subscriptions/<subscription_id>')
def API_Commercial_UpdateSubscription(subscription_id):
    try:
        status = CommercialSubscriptionManager.update_subscription(
            subscription_id, request.get_json(silent=True) or {}
        )
        return ResponseObject(status, None if status else "Subscription does not exist", status_code=200 if status else 404)
    except (ValueError, TypeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/commercial/subscriptions/<subscription_id>/rotate-token')
def API_Commercial_RotateSubscriptionToken(subscription_id):
    rotated = CommercialSubscriptionManager.rotate_subscription_token(subscription_id)
    if not rotated:
        return ResponseObject(False, "Subscription does not exist", status_code=404)
    rotated["subscription_url"] = request.host_url.rstrip("/") + APP_PREFIX + rotated["subscription_path"]
    return ResponseObject(data=rotated)


@app.post(f'{APP_PREFIX}/api/commercial/subscriptions/<subscription_id>/provision')
def API_Commercial_ProvisionSubscription(subscription_id):
    data = request.get_json(silent=True) or {}
    try:
        jobs = CommercialSubscriptionManager.provision(
            subscription_id, data.get("node_ids", []), data.get("interface", "wg0"),
            data.get("dns", "1.1.1.1"), data.get("mtu", 1420),
            data.get("allowed_ips", "0.0.0.0/0, ::/0"),
        )
        return ResponseObject(data=jobs, status_code=202)
    except (ValueError, TypeError, RuntimeError) as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.post(f'{APP_PREFIX}/api/commercial/peers/<peer_id>/action')
def API_Commercial_PeerAction(peer_id):
    try:
        job_id = CommercialSubscriptionManager.queue_peer_action(
            peer_id, (request.get_json(silent=True) or {}).get("operation")
        )
        return ResponseObject(data={"job_id": job_id}, status_code=202)
    except ValueError as exc:
        return ResponseObject(False, str(exc), status_code=400)


@app.get(f'{APP_PREFIX}/sub/<subscription_access>')
def API_Public_Subscription(subscription_access):
    if "." not in subscription_access:
        return ResponseObject(False, "Invalid subscription link", status_code=404)
    subscription_id, token = subscription_access.split(".", 1)
    payload, error = CommercialSubscriptionManager.get_public_subscription(subscription_id, token)
    if error:
        return ResponseObject(False, error, status_code=404 if "exist" in error else 403)
    if request.args.get("format", "json").lower() != "zip":
        response = ResponseObject(data=payload)
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    archive = io.BytesIO()
    with ZipFile(archive, "w") as zip_file:
        for item in payload["files"]:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", item["name"])
            zip_file.writestr(safe_name, item["configuration"])
    archive.seek(0)
    response = send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"subscription-{subscription_id}.zip",
    )
    response.headers["Cache-Control"] = "no-store, private, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

@app.get(f'{APP_PREFIX}/api/validateAuthentication')
def API_ValidateAuthentication():
    token = request.cookies.get("authToken")
    if DashboardConfig.GetConfig("Server", "auth_req")[1]:
        if token is None or token == "" or "username" not in session or session["username"] != token:
            return ResponseObject(False, "Invalid authentication.")
    return ResponseObject(True)

@app.get(f'{APP_PREFIX}/api/requireAuthentication')
def API_RequireAuthentication():
    return ResponseObject(data=DashboardConfig.GetConfig("Server", "auth_req")[1])

@app.post(f'{APP_PREFIX}/api/authenticate')
def API_AuthenticateLogin():
    data = request.get_json()
    if not DashboardConfig.GetConfig("Server", "auth_req")[1]:
        return ResponseObject(True, DashboardConfig.GetConfig("Other", "welcome_session")[1])
    
    if DashboardConfig.APIAccessed:
        authToken = hashlib.sha256(f"{request.headers.get('wg-dashboard-apikey')}{datetime.now()}".encode()).hexdigest()
        session['role'] = 'admin'
        session['username'] = authToken
        resp = ResponseObject(True, DashboardConfig.GetConfig("Other", "welcome_session")[1])
        resp.set_cookie("authToken", authToken)
        session.permanent = True
        return resp
    valid = bcrypt.checkpw(data['password'].encode("utf-8"),
                           DashboardConfig.GetConfig("Account", "password")[1].encode("utf-8"))
    totpEnabled = DashboardConfig.GetConfig("Account", "enable_totp")[1]
    totpValid = False
    if totpEnabled:
        totpValid = pyotp.TOTP(DashboardConfig.GetConfig("Account", "totp_key")[1]).now() == data['totp']

    if (valid
            and data['username'] == DashboardConfig.GetConfig("Account", "username")[1]
            and ((totpEnabled and totpValid) or not totpEnabled)
    ):
        authToken = hashlib.sha256(f"{data['username']}{datetime.now()}".encode()).hexdigest()
        session['role'] = 'admin'
        session['username'] = authToken
        resp = ResponseObject(True, DashboardConfig.GetConfig("Other", "welcome_session")[1])
        resp.set_cookie("authToken", authToken)
        session.permanent = True
        DashboardLogger.log(str(request.url), str(request.remote_addr), Message=f"Login success: {data['username']}")
        return resp
    DashboardLogger.log(str(request.url), str(request.remote_addr), Message=f"Login failed: {data['username']}")
    if totpEnabled:
        return ResponseObject(False, "Sorry, your username, password or OTP is incorrect.")
    else:
        return ResponseObject(False, "Sorry, your username or password is incorrect.")

@app.get(f'{APP_PREFIX}/api/signout')
def API_SignOut():
    resp = ResponseObject(True, "")
    resp.delete_cookie("authToken")
    session.clear()
    return resp

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurations')
def API_getWireguardConfigurations():
    InitWireguardConfigurationsList()
    return ResponseObject(data=[wc for wc in WireguardConfigurations.values()])

@app.get(f'{APP_PREFIX}/api/newConfigurationTemplates')
def API_NewConfigurationTemplates():
    return ResponseObject(data=NewConfigurationTemplates.GetTemplates())

@app.get(f'{APP_PREFIX}/api/newConfigurationTemplates/createTemplate')
def API_NewConfigurationTemplates_CreateTemplate():
    return ResponseObject(data=NewConfigurationTemplates.CreateTemplate().model_dump())

@app.post(f'{APP_PREFIX}/api/newConfigurationTemplates/updateTemplate')
def API_NewConfigurationTemplates_UpdateTemplate():
    data = request.get_json()
    template = data.get('Template', None)
    if not template:
        return ResponseObject(False, "Please provide template")
    
    status, msg = NewConfigurationTemplates.UpdateTemplate(template)
    return ResponseObject(status, msg)

@app.post(f'{APP_PREFIX}/api/newConfigurationTemplates/deleteTemplate')
def API_NewConfigurationTemplates_DeleteTemplate():
    data = request.get_json()
    template = data.get('Template', None)
    if not template:
        return ResponseObject(False, "Please provide template")

    status, msg = NewConfigurationTemplates.DeleteTemplate(template)
    return ResponseObject(status, msg)

@app.post(f'{APP_PREFIX}/api/addWireguardConfiguration')
def API_addWireguardConfiguration():
    data = request.get_json()
    requiredKeys = [
        "ConfigurationName", "Address", "ListenPort", "PrivateKey", "Protocol"
    ]
    for i in requiredKeys:
        if i not in data.keys():
            return ResponseObject(False, "Please provide all required parameters.")
    
    if data.get("Protocol") not in ProtocolsEnabled():
        return ResponseObject(False, "Please provide a valid protocol: wg / awg.")

    # Check duplicate names, ports, address
    for i in WireguardConfigurations.values():
        if i.Name == data['ConfigurationName']:
            return ResponseObject(False,
                                  f"Already have a configuration with the name \"{data['ConfigurationName']}\"",
                                  "ConfigurationName")

        if str(i.ListenPort) == str(data["ListenPort"]):
            return ResponseObject(False,
                                  f"Already have a configuration with the port \"{data['ListenPort']}\"",
                                  "ListenPort")

        if i.Address == data["Address"]:
            return ResponseObject(False,
                                  f"Already have a configuration with the address \"{data['Address']}\"",
                                  "Address")

    if "Backup" in data.keys():
        path = {
            "wg": DashboardConfig.GetConfig("Server", "wg_conf_path")[1],
            "awg": DashboardConfig.GetConfig("Server", "awg_conf_path")[1]
        }
     
        if (os.path.exists(os.path.join(path['wg'], 'WGDashboard_Backup', data["Backup"])) and
                os.path.exists(os.path.join(path['wg'], 'WGDashboard_Backup', data["Backup"].replace('.conf', '.sql')))):
            protocol = "wg"
        elif (os.path.exists(os.path.join(path['awg'], 'WGDashboard_Backup', data["Backup"])) and
              os.path.exists(os.path.join(path['awg'], 'WGDashboard_Backup', data["Backup"].replace('.conf', '.sql')))):
            protocol = "awg"
        else:
            return ResponseObject(False, "Backup does not exist")
        
        shutil.copy(
            os.path.join(path[protocol], 'WGDashboard_Backup', data["Backup"]),
            os.path.join(path[protocol], f'{data["ConfigurationName"]}.conf')
        )
        WireguardConfigurations[data['ConfigurationName']] = (
            WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, data=data, name=data['ConfigurationName'])) if protocol == 'wg' else (
            AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data=data, name=data['ConfigurationName']))
    else:
        WireguardConfigurations[data['ConfigurationName']] = (
            WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data=data)) if data.get('Protocol') == 'wg' else (
            AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data=data))
    return ResponseObject()

@app.get(f'{APP_PREFIX}/api/toggleWireguardConfiguration')
def API_toggleWireguardConfiguration():
    configurationName = request.args.get('configurationName')
    if configurationName is None or len(
            configurationName) == 0 or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide a valid configuration name", status_code=404)
    toggleStatus, msg = WireguardConfigurations[configurationName].toggleConfiguration()
    return ResponseObject(toggleStatus, msg, WireguardConfigurations[configurationName].Status)

@app.post(f'{APP_PREFIX}/api/updateWireguardConfiguration')
def API_updateWireguardConfiguration():
    data = request.get_json()
    requiredKeys = ["Name"]
    for i in requiredKeys:
        if i not in data.keys():
            return ResponseObject(False, "Please provide these following field: " + ", ".join(requiredKeys))
    name = data.get("Name")
    if name not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    
    status, msg = WireguardConfigurations[name].updateConfigurationSettings(data)
    
    return ResponseObject(status, message=msg, data=WireguardConfigurations[name])

@app.post(f'{APP_PREFIX}/api/updateWireguardConfigurationInfo')
def API_updateWireguardConfigurationInfo():
    data = request.get_json()
    name = data.get('Name')
    key = data.get('Key')
    value = data.get('Value')
    if not all([data, key, name]):
        return ResponseObject(status=False, message="Please provide configuration name, key and value")
    if name not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    
    status, msg, key = WireguardConfigurations[name].updateConfigurationInfo(key, value)
    
    return ResponseObject(status=status, message=msg, data=key)

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurationRawFile')
def API_GetWireguardConfigurationRawFile():
    configurationName = request.args.get('configurationName')
    if configurationName is None or len(
            configurationName) == 0 or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide a valid configuration name", status_code=404)
    
    return ResponseObject(data={
        "path": WireguardConfigurations[configurationName].configPath,
        "content": WireguardConfigurations[configurationName].getRawConfigurationFile()
    })

@app.post(f'{APP_PREFIX}/api/updateWireguardConfigurationRawFile')
def API_UpdateWireguardConfigurationRawFile():
    data = request.get_json()
    configurationName = data.get('configurationName')
    rawConfiguration = data.get('rawConfiguration')
    if configurationName is None or len(
            configurationName) == 0 or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide a valid configuration name")
    if rawConfiguration is None or len(rawConfiguration) == 0:
        return ResponseObject(False, "Please provide content")
    
    status, err = WireguardConfigurations[configurationName].updateRawConfigurationFile(rawConfiguration)

    return ResponseObject(status=status, message=err)

@app.post(f'{APP_PREFIX}/api/deleteWireguardConfiguration')
def API_deleteWireguardConfiguration():
    data = request.get_json()
    if "ConfigurationName" not in data.keys() or data.get("ConfigurationName") is None or data.get("ConfigurationName") not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide the configuration name you want to delete", status_code=404)
    rp =  WireguardConfigurations.pop(data.get("ConfigurationName"))
    
    status = rp.deleteConfiguration()
    if not status:
        WireguardConfigurations[data.get("ConfigurationName")] = rp
    return ResponseObject(status)

@app.post(f'{APP_PREFIX}/api/renameWireguardConfiguration')
def API_renameWireguardConfiguration():
    data = request.get_json()
    keys = ["ConfigurationName", "NewConfigurationName"]
    for k in keys:
        if (k not in data.keys() or data.get(k) is None or len(data.get(k)) == 0 or 
                (k == "ConfigurationName" and data.get(k) not in WireguardConfigurations.keys())): 
            return ResponseObject(False, "Please provide the configuration name you want to rename", status_code=404)
    
    if data.get("NewConfigurationName") in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration name already exist", status_code=400)
    
    rc = WireguardConfigurations.pop(data.get("ConfigurationName"))
    
    status, message = rc.renameConfiguration(data.get("NewConfigurationName"))
    if status:
        WireguardConfigurations[data.get("NewConfigurationName")] = (WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data.get("NewConfigurationName")) if rc.Protocol == 'wg' else AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data.get("NewConfigurationName")))
    else:
        WireguardConfigurations[data.get("ConfigurationName")] = rc
    return ResponseObject(status, message)

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurationRealtimeTraffic')
def API_getWireguardConfigurationRealtimeTraffic():
    configurationName = request.args.get('configurationName')
    if configurationName is None or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    return ResponseObject(data=WireguardConfigurations[configurationName].getRealtimeTrafficUsage())

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurationBackup')
def API_getWireguardConfigurationBackup():
    configurationName = request.args.get('configurationName')
    if configurationName is None or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist",  status_code=404)
    return ResponseObject(data=WireguardConfigurations[configurationName].getBackups())

@app.get(f'{APP_PREFIX}/api/getAllWireguardConfigurationBackup')
def API_getAllWireguardConfigurationBackup():
    data = {
        "ExistingConfigurations": {},
        "NonExistingConfigurations": {}
    }
    existingConfiguration = WireguardConfigurations.keys()
    for i in existingConfiguration:
        b = WireguardConfigurations[i].getBackups(True)
        if len(b) > 0:
            data['ExistingConfigurations'][i] = WireguardConfigurations[i].getBackups(True)
            
    for protocol in ProtocolsEnabled():
        directory = os.path.join(DashboardConfig.GetConfig("Server", f"{protocol}_conf_path")[1], 'WGDashboard_Backup')
        if os.path.exists(directory):
            files = [(file, os.path.getctime(os.path.join(directory, file)))
                     for file in os.listdir(directory) if os.path.isfile(os.path.join(directory, file))]
            files.sort(key=lambda x: x[1], reverse=True)
        
            for f, ct in files:
                if RegexMatch(r"^(.+)_(\d+)\.(conf)$", f):
                    s = re.search(r"^(.+)_(\d+)\.(conf)$", f)
                    name = s.group(1)
                    if name not in existingConfiguration:
                        if name not in data['NonExistingConfigurations'].keys():
                            data['NonExistingConfigurations'][name] = []
                        
                        date = s.group(2)
                        d = {
                            "protocol": protocol,
                            "filename": f,
                            "backupDate": date,
                            "content": open(os.path.join(DashboardConfig.GetConfig("Server", f"{protocol}_conf_path")[1], 'WGDashboard_Backup', f), 'r').read()
                        }
                        if f.replace(".conf", ".sql") in list(os.listdir(directory)):
                            d['database'] = True
                            d['databaseContent'] = open(os.path.join(DashboardConfig.GetConfig("Server", f"{protocol}_conf_path")[1], 'WGDashboard_Backup', f.replace(".conf", ".sql")), 'r').read()
                        data['NonExistingConfigurations'][name].append(d)
    return ResponseObject(data=data)

@app.get(f'{APP_PREFIX}/api/createWireguardConfigurationBackup')
def API_createWireguardConfigurationBackup():
    configurationName = request.args.get('configurationName')
    if configurationName is None or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist",  status_code=404)
    return ResponseObject(status=WireguardConfigurations[configurationName].backupConfigurationFile()[0], 
                          data=WireguardConfigurations[configurationName].getBackups())

@app.post(f'{APP_PREFIX}/api/deleteWireguardConfigurationBackup')
def API_deleteWireguardConfigurationBackup():
    data = request.get_json()
    if ("ConfigurationName" not in data.keys() or 
            "BackupFileName" not in data.keys() or
            len(data['ConfigurationName']) == 0 or 
            len(data['BackupFileName']) == 0):
        return ResponseObject(False, 
        "Please provide configurationName and backupFileName in body",  status_code=400)
    configurationName = data['ConfigurationName']
    backupFileName = data['BackupFileName']
    if configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    
    status = WireguardConfigurations[configurationName].deleteBackup(backupFileName)
    return ResponseObject(status=status, message=(None if status else 'Backup file does not exist'), 
                          status_code = (200 if status else 404))

@app.get(f'{APP_PREFIX}/api/downloadWireguardConfigurationBackup')
def API_downloadWireguardConfigurationBackup():
    configurationName = os.path.basename(request.args.get('configurationName'))
    backupFileName = os.path.basename(request.args.get('backupFileName'))

    if configurationName is None or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)

    status, zip = WireguardConfigurations[configurationName].downloadBackup(backupFileName)

    if not status:
        current_app.logger.error(f"Failed to download a requested backup.\nConfiguration Name: {configurationName}\nBackup File Name: {backupFileName}")
        return ResponseObject(False, "Internal server error", status_code=500)

    return send_file(os.path.join('download', zip), as_attachment=True)

@app.post(f'{APP_PREFIX}/api/restoreWireguardConfigurationBackup')
def API_restoreWireguardConfigurationBackup():
    data = request.get_json()
    if ("ConfigurationName" not in data.keys() or
            "BackupFileName" not in data.keys() or
            len(data['ConfigurationName']) == 0 or
            len(data['BackupFileName']) == 0):
        return ResponseObject(False,
                              "Please provide ConfigurationName and BackupFileName in body", status_code=400)
    configurationName = data['ConfigurationName']
    backupFileName = data['BackupFileName']
    if configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    
    status = WireguardConfigurations[configurationName].restoreBackup(backupFileName)
    return ResponseObject(status=status, message=(None if status else 'Restore backup failed'))
    
@app.get(f'{APP_PREFIX}/api/getDashboardConfiguration')
def API_getDashboardConfiguration():
    return ResponseObject(data=DashboardConfig.toJson())

@app.post(f'{APP_PREFIX}/api/updateDashboardConfigurationItem')
def API_updateDashboardConfigurationItem():
    data = request.get_json()
    if "section" not in data.keys() or "key" not in data.keys() or "value" not in data.keys():
        return ResponseObject(False, "Invalid request.")
    valid, msg = DashboardConfig.SetConfig(
        data["section"], data["key"], data['value'])
    if not valid:
        return ResponseObject(False, msg)
    if data['section'] == "Server":
        if data['key'] == 'wg_conf_path':
            WireguardConfigurations.clear()
            WireguardConfigurations.clear()
            InitWireguardConfigurationsList()
    return ResponseObject(True, data=DashboardConfig.GetConfig(data["section"], data["key"])[1])

@app.get(f'{APP_PREFIX}/api/getDashboardAPIKeys')
def API_getDashboardAPIKeys():
    if DashboardConfig.GetConfig('Server', 'dashboard_api_key'):
        return ResponseObject(data=DashboardConfig.DashboardAPIKeys)
    return ResponseObject(False, "WGDashboard API Keys function is disabled")

@app.post(f'{APP_PREFIX}/api/newDashboardAPIKey')
def API_newDashboardAPIKey():
    data = request.get_json()
    if DashboardConfig.GetConfig('Server', 'dashboard_api_key'):
        try:
            if data['NeverExpire']:
                expiredAt = None
            else:
                expiredAt = datetime.strptime(data['ExpiredAt'], '%Y-%m-%d %H:%M:%S')
            DashboardConfig.createAPIKeys(expiredAt)
            return ResponseObject(True, data=DashboardConfig.DashboardAPIKeys)
        except Exception as e:
            return ResponseObject(False, str(e))
    return ResponseObject(False, "Dashboard API Keys function is disbaled")

@app.post(f'{APP_PREFIX}/api/deleteDashboardAPIKey')
def API_deleteDashboardAPIKey():
    data = request.get_json()
    if DashboardConfig.GetConfig('Server', 'dashboard_api_key'):
        if len(data['Key']) > 0 and len(list(filter(lambda x : x.Key == data['Key'], DashboardConfig.DashboardAPIKeys))) > 0:
            DashboardConfig.deleteAPIKey(data['Key'])
            return ResponseObject(True, data=DashboardConfig.DashboardAPIKeys)
        else:
            return ResponseObject(False, "API Key does not exist", status_code=404)
    return ResponseObject(False, "Dashboard API Keys function is disbaled")
    
@app.post(f'{APP_PREFIX}/api/updatePeerSettings/<configName>')
def API_updatePeerSettings(configName):
    data = request.get_json()
    id = data['id']
    if len(id) > 0 and configName in WireguardConfigurations.keys():
        name = data['name']
        private_key = data['private_key']
        dns_addresses = data['DNS']
        allowed_ip = data['allowed_ip']
        endpoint_allowed_ip = data['endpoint_allowed_ip']
        preshared_key = data['preshared_key']
        mtu = data['mtu']
        keepalive = data['keepalive']
        notes = data.get('notes', '')
        quota_gb = data.get('quota_gb', 0)
        expires_at = data.get('expires_at')
        wireguardConfig = WireguardConfigurations[configName]
        foundPeer, peer = wireguardConfig.searchPeer(id)
        if foundPeer:
            if wireguardConfig.Protocol == 'wg':
                status, msg = peer.updatePeer(name,
                                              private_key,
                                              preshared_key, 
                                              dns_addresses,
                                              allowed_ip,
                                              endpoint_allowed_ip,
                                              mtu,
                                              keepalive,
                                              notes,
                                              quota_gb,
                                              expires_at)
            else:
                status, msg = peer.updatePeer(name,
                                              private_key,
                                              preshared_key,
                                              dns_addresses,
                                              allowed_ip,
                                              endpoint_allowed_ip,
                                              mtu,
                                              keepalive,
                                              notes,
                                              quota_gb,
                                              expires_at)
            wireguardConfig.getPeers()
            if status:
                wireguardConfig.enforceDataQuotas()
                wireguardConfig.enforceExpirations()
            DashboardWebHooks.RunWebHook('peer_updated', {
                "configuration": wireguardConfig.Name,
                "peers": [id]
            })
            return ResponseObject(status, msg)
            
    return ResponseObject(False, "Peer does not exist")

@app.post(f'{APP_PREFIX}/api/resetPeerData/<configName>')
def API_resetPeerData(configName):
    data = request.get_json()
    id = data['id']
    type = data['type']
    if len(id) == 0 or configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration/Peer does not exist")
    wgc = WireguardConfigurations.get(configName)
    foundPeer, peer = wgc.searchPeer(id)
    if not foundPeer:
        return ResponseObject(False, "Configuration/Peer does not exist")
    
    resetStatus = peer.resetDataUsage(type)
    if resetStatus:
        wgc.restrictPeers([id])
        wgc.allowAccessPeers([id])
    
    return ResponseObject(status=resetStatus)

@app.post(f'{APP_PREFIX}/api/deletePeers/<configName>')
def API_deletePeers(configName: str) -> ResponseObject:
    data = request.get_json()
    peers = data['peers']
    if configName in WireguardConfigurations.keys():
        if len(peers) == 0:
            return ResponseObject(False, "Please specify one or more peers", status_code=400)
        configuration = WireguardConfigurations.get(configName)
        status, msg = configuration.deletePeers(peers, AllPeerJobs, AllPeerShareLinks)
        
        # Delete Assignment
        
        for p in peers:
            assignments = DashboardClients.DashboardClientsPeerAssignment.GetAssignedClients(configName, p)
            for c in assignments:
                DashboardClients.DashboardClientsPeerAssignment.UnassignClients(c.AssignmentID)
        
        return ResponseObject(status, msg)

    return ResponseObject(False, "Configuration does not exist", status_code=404)

@app.post(f'{APP_PREFIX}/api/restrictPeers/<configName>')
def API_restrictPeers(configName: str) -> ResponseObject:
    data = request.get_json()
    peers = data['peers']
    if configName in WireguardConfigurations.keys():
        if len(peers) == 0:
            return ResponseObject(False, "Please specify one or more peers")
        configuration = WireguardConfigurations.get(configName)
        status, msg = configuration.restrictPeers(peers)
        return ResponseObject(status, msg)
    return ResponseObject(False, "Configuration does not exist", status_code=404)

@app.post(f'{APP_PREFIX}/api/sharePeer/create')
def API_sharePeer_create():
    data: dict[str, str] = request.get_json()
    Configuration = data.get('Configuration')
    Peer = data.get('Peer')
    ExpireDate = data.get('ExpireDate')
    if Configuration is None or Peer is None:
        return ResponseObject(False, "Please specify configuration and peers")
    activeLink = AllPeerShareLinks.getLink(Configuration, Peer)
    if len(activeLink) > 0:
        return ResponseObject(True, 
                              "This peer is already sharing. Please view data for shared link.",
                                data=activeLink[0]
        )
    status, message = AllPeerShareLinks.addLink(Configuration, Peer, datetime.strptime(ExpireDate, "%Y-%m-%d %H:%M:%S"))
    if not status:
        return ResponseObject(status, message)
    return ResponseObject(data=AllPeerShareLinks.getLinkByID(message))

@app.post(f'{APP_PREFIX}/api/sharePeer/update')
def API_sharePeer_update():
    data: dict[str, str] = request.get_json()
    ShareID: str = data.get("ShareID")
    ExpireDate: str = data.get("ExpireDate")
    
    if not all([ShareID, ExpireDate]):
        return ResponseObject(False, "Please specify ShareID")
    
    if len(AllPeerShareLinks.getLinkByID(ShareID)) == 0:
        return ResponseObject(False, "ShareID does not exist")
    
    status, message = AllPeerShareLinks.updateLinkExpireDate(ShareID, datetime.strptime(ExpireDate, "%Y-%m-%d %H:%M:%S"))
    if not status:
        return ResponseObject(status, message)
    return ResponseObject(data=AllPeerShareLinks.getLinkByID(ShareID))

@app.get(f'{APP_PREFIX}/api/sharePeer/get')
def API_sharePeer_get():
    data = request.args
    ShareID = data.get("ShareID")
    if ShareID is None or len(ShareID) == 0:
        return ResponseObject(False, "Please provide ShareID")
    link = AllPeerShareLinks.getLinkByID(ShareID)
    if len(link) == 0:
        return ResponseObject(False, "This link is either expired to invalid")
    l = link[0]
    if l.Configuration not in WireguardConfigurations.keys():
        return ResponseObject(False, "The peer you're looking for does not exist")
    c = WireguardConfigurations.get(l.Configuration)
    fp, p = c.searchPeer(l.Peer)
    if not fp:
        return ResponseObject(False, "The peer you're looking for does not exist")
    
    return ResponseObject(data=p.downloadPeer())
    
@app.post(f'{APP_PREFIX}/api/allowAccessPeers/<configName>')
def API_allowAccessPeers(configName: str) -> ResponseObject:
    data = request.get_json()
    peers = data['peers']
    if configName in WireguardConfigurations.keys():
        if len(peers) == 0:
            return ResponseObject(False, "Please specify one or more peers")
        configuration = WireguardConfigurations.get(configName)
        status, msg = configuration.allowAccessPeers(peers)
        return ResponseObject(status, msg)
    return ResponseObject(False, "Configuration does not exist")

@app.post(f'{APP_PREFIX}/api/addPeers/<configName>')
def API_addPeers(configName):
    if configName in WireguardConfigurations.keys():
        data: dict = request.get_json()
        try:
            

            bulkAdd: bool = data.get("bulkAdd", False)
            bulkAddAmount: int = data.get('bulkAddAmount', 0)
            preshared_key_bulkAdd: bool = data.get('preshared_key_bulkAdd', False)

            public_key: str = data.get('public_key', "")
            allowed_ips: list[str] = data.get('allowed_ips', [])
            allowed_ips_validation: bool = data.get('allowed_ips_validation', True)
            
            endpoint_allowed_ip: str = data.get('endpoint_allowed_ip', DashboardConfig.GetConfig("Peers", "peer_endpoint_allowed_ip")[1])
            dns_addresses: str = data.get('DNS', DashboardConfig.GetConfig("Peers", "peer_global_DNS")[1])
            
            
            mtu: int = data.get('mtu', None)
            keep_alive: int = data.get('keepalive', None)
            notes: str = data.get('notes', '')
            preshared_key: str = data.get('preshared_key', "")            
            try:
                quota_gb: float = float(data.get('quota_gb') or 0)
            except (TypeError, ValueError):
                return ResponseObject(False, "Data quota must be a number", status_code=400)
            if quota_gb < 0:
                return ResponseObject(False, "Data quota cannot be negative", status_code=400)
            try:
                expires_at = ParseOptionalDateTime(data.get('expires_at'))
            except ValueError as exc:
                return ResponseObject(False, str(exc), status_code=400)
    
            if type(mtu) is not int or mtu < 0 or mtu > 1460:
                default: str = DashboardConfig.GetConfig("Peers", "peer_mtu")[1]
                if default.isnumeric():
                    try:
                        mtu = int(default)
                    except Exception as e:
                        mtu = 0
                else:
                    mtu = 0
            if type(keep_alive) is not int or keep_alive < 0:
                default = DashboardConfig.GetConfig("Peers", "peer_keep_alive")[1]
                if default.isnumeric():
                    try:
                        keep_alive = int(default)
                    except Exception as e:
                        keep_alive = 0
                else:
                    keep_alive = 0
            
            config = WireguardConfigurations.get(configName)
            if not config.getStatus():
                config.toggleConfiguration()
            ipStatus, availableIps = config.getAvailableIP(-1)
            ipCountStatus, numberOfAvailableIPs = config.getNumberOfAvailableIP()
            defaultIPSubnet = list(availableIps.keys())[0]
            if bulkAdd:
                if type(preshared_key_bulkAdd) is not bool:
                    preshared_key_bulkAdd = False
                if type(bulkAddAmount) is not int or bulkAddAmount < 1:
                    return ResponseObject(False, "Please specify amount of peers you want to add")
                if not ipStatus:
                    return ResponseObject(False, "No more available IP can assign")
                if len(availableIps.keys()) == 0:
                    return ResponseObject(False, "This configuration does not have any IP address available")
                if bulkAddAmount > sum(list(numberOfAvailableIPs.values())):
                    return ResponseObject(False,
                            f"The maximum number of peers can add is {sum(list(numberOfAvailableIPs.values()))}")
                keyPairs = []
                addedCount = 0
                for subnet in availableIps.keys():
                    for ip in availableIps[subnet]:
                        newPrivateKey = GenerateWireguardPrivateKey()[1]
                        addedCount += 1
                        keyPairs.append({
                            "private_key": newPrivateKey,
                            "id": GenerateWireguardPublicKey(newPrivateKey)[1],
                            "preshared_key": (GenerateWireguardPrivateKey()[1] if preshared_key_bulkAdd else ""),
                            "allowed_ip": ip,
                            "name": f"BulkPeer_{(addedCount + 1)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                            "DNS": dns_addresses,
                            "endpoint_allowed_ip": endpoint_allowed_ip,
                            "mtu": mtu,
                            "keepalive": keep_alive,
                            "notes": "",
                            "quota_gb": quota_gb,
                            "expires_at": expires_at
                        })
                        if addedCount == bulkAddAmount:
                            break
                    if addedCount == bulkAddAmount:
                        break
                if len(keyPairs) == 0 or (bulkAdd and len(keyPairs) != bulkAddAmount):
                    return ResponseObject(False, "Generating key pairs by bulk failed")
                status, addedPeers, message = config.addPeers(keyPairs)
                return ResponseObject(status=status, message=message, data=addedPeers)
    
            else:
                if config.searchPeer(public_key)[0] is True:
                    return ResponseObject(False, f"This peer already exist")
                name = data.get("name", "")
                private_key = data.get("private_key", "")

                if len(public_key) == 0:
                    if len(private_key) == 0:
                        private_key = GenerateWireguardPrivateKey()[1]
                        public_key = GenerateWireguardPublicKey(private_key)[1]
                    else:
                        public_key = GenerateWireguardPublicKey(private_key)[1]
                else:
                    if len(private_key) > 0:
                        genPub = GenerateWireguardPublicKey(private_key)[1]
                        # Check if provided pubkey match provided private key
                        if public_key != genPub:
                            return ResponseObject(False, "Provided Public Key does not match provided Private Key")
                if len(allowed_ips) == 0:
                    if ipStatus:
                        for subnet in availableIps.keys():
                            for ip in availableIps[subnet]:
                                allowed_ips = [ip]
                                break
                            break  
                    else:
                        return ResponseObject(False, "No more available IP can assign") 

                if allowed_ips_validation:
                    for i in allowed_ips:
                        found = False
                        for subnet in availableIps.keys():
                            try:
                                network = ipaddress.ip_network(subnet, False)
                                ap = ipaddress.ip_network(i)
                            except ValueError as e:
                                return ResponseObject(False, str(e))
                            if network.version == ap.version and ap.subnet_of(network):
                                found = True
                        
                        if not found:
                            return ResponseObject(False, f"This IP is not available: {i}")

                status, addedPeers, message = config.addPeers([
                    {
                        "name": name,
                        "id": public_key,
                        "private_key": private_key,
                        "allowed_ip": ','.join(allowed_ips),
                        "preshared_key": preshared_key,
                        "endpoint_allowed_ip": endpoint_allowed_ip,
                        "DNS": dns_addresses,
                        "mtu": mtu,
                        "keepalive": keep_alive,
                        "notes": notes,
                        "quota_gb": quota_gb,
                        "expires_at": expires_at
                    }]
                )
                return ResponseObject(status=status, message=message, data=addedPeers)
        except Exception as e:
            app.logger.error("Add peers failed", e)
            return ResponseObject(False, f"Add peers failed.")

    return ResponseObject(False, "Configuration does not exist")

@app.get(f"{APP_PREFIX}/api/downloadPeer/<configName>")
def API_downloadPeer(configName):
    data = request.args
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    configuration = WireguardConfigurations[configName]
    peerFound, peer = configuration.searchPeer(data['id'])
    if len(data['id']) == 0 or not peerFound:
        return ResponseObject(False, "Peer does not exist")
    return ResponseObject(data=peer.downloadPeer())

@app.get(f"{APP_PREFIX}/api/downloadAllPeers/<configName>")
def API_downloadAllPeers(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    configuration = WireguardConfigurations[configName]
    peerData = []
    untitledPeer = 0
    for i in configuration.Peers:
        file = i.downloadPeer()
        if file["fileName"] == "UntitledPeer":
            file["fileName"] = str(untitledPeer) + "_" + file["fileName"]
            untitledPeer += 1
        peerData.append(file)
    return ResponseObject(data=peerData)

@app.get(f"{APP_PREFIX}/api/getAvailableIPs/<configName>")
def API_getAvailableIPs(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    status, ips = WireguardConfigurations.get(configName).getAvailableIP()
    return ResponseObject(status=status, data=ips)

@app.get(f"{APP_PREFIX}/api/getNumberOfAvailableIPs/<configName>")
def API_getNumberOfAvailableIPs(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    status, ips = WireguardConfigurations.get(configName).getNumberOfAvailableIP()
    return ResponseObject(status=status, data=ips)

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurationInfo')
def API_getConfigurationInfo():
    configurationName = request.args.get("configurationName")
    if not configurationName or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide configuration name")
    return ResponseObject(data={
        "configurationInfo": WireguardConfigurations[configurationName],
        "configurationPeers": WireguardConfigurations[configurationName].getPeersList(),
        "configurationRestrictedPeers": WireguardConfigurations[configurationName].getRestrictedPeersList()
    })

@app.get(f'{APP_PREFIX}/api/getPeerHistoricalEndpoints')
def API_GetPeerHistoricalEndpoints():
    configurationName = request.args.get("configurationName")
    id = request.args.get('id')
    if not configurationName or not id:
        return ResponseObject(False, "Please provide configurationName and id")
    fp, p = WireguardConfigurations.get(configurationName).searchPeer(id)
    if fp:
        result = p.getEndpoints()
        geo = {}
        try:
            r = requests.post(f"http://ip-api.com/batch?fields=city,country,lat,lon,query",
                              data=json.dumps([x['endpoint'] for x in result]))
            d = r.json()
            
                
        except Exception as e:
            return ResponseObject(data=result, message="Failed to request IP address geolocation. " + str(e))
        
        return ResponseObject(data={
            "endpoints": p.getEndpoints(),
            "geolocation": d
        })
    return ResponseObject(False, "Peer does not exist")

@app.get(f'{APP_PREFIX}/api/getPeerSessions')
def API_GetPeerSessions():
    configurationName = request.args.get("configurationName")
    id = request.args.get('id')
    try:
        startDate = request.args.get('startDate', None)
        endDate = request.args.get('endDate', None)
        
        if startDate is None:
            endDate = None
        else:
            startDate = datetime.strptime(startDate, "%Y-%m-%d")
            if endDate:
                endDate = datetime.strptime(endDate, "%Y-%m-%d")
                if startDate > endDate:
                    return ResponseObject(False, "startDate must be smaller than endDate")
    except Exception as e:
        return ResponseObject(False, "Dates are invalid")
    if not configurationName or not id:
        return ResponseObject(False, "Please provide configurationName and id")
    fp, p = WireguardConfigurations.get(configurationName).searchPeer(id)
    if fp:
        return ResponseObject(data=p.getSessions(startDate, endDate))
    return ResponseObject(False, "Peer does not exist")

@app.get(f'{APP_PREFIX}/api/getPeerTraffics')
def API_GetPeerTraffics():
    configurationName = request.args.get("configurationName")
    id = request.args.get('id')
    try:
        interval = request.args.get('interval', 30)
        startDate = request.args.get('startDate', None)
        endDate = request.args.get('endDate', None)
        if type(interval) is str:
            if not interval.isdigit():
                return ResponseObject(False, "Interval must be integers in minutes")
            interval = int(interval)
        if startDate is None:
            endDate = None
        else:
            startDate = datetime.strptime(startDate, "%Y-%m-%d")
            if endDate:
                endDate = datetime.strptime(endDate, "%Y-%m-%d")
                if startDate > endDate:
                    return ResponseObject(False, "startDate must be smaller than endDate")
    except Exception as e:
        return ResponseObject(False, "Dates are invalid" + e)
    if not configurationName or not id:
        return ResponseObject(False, "Please provide configurationName and id")
    fp, p = WireguardConfigurations.get(configurationName).searchPeer(id)
    if fp:
        return ResponseObject(data=p.getTraffics(interval, startDate, endDate))
    return ResponseObject(False, "Peer does not exist")

@app.get(f'{APP_PREFIX}/api/getPeerTrackingTableCounts')
def API_GetPeerTrackingTableCounts():
    configurationName = request.args.get("configurationName")
    if configurationName and configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    
    if configurationName:
        c = WireguardConfigurations.get(configurationName)
        return ResponseObject(data={
            "TrafficTrackingTableSize": c.getTransferTableSize(),
            "HistoricalTrackingTableSize": c.getHistoricalEndpointTableSize()
        })
    
    d = {}
    for i in WireguardConfigurations.keys():
        c = WireguardConfigurations.get(i)
        d[i] = {
            "TrafficTrackingTableSize": c.getTransferTableSize(),
            "HistoricalTrackingTableSize": c.getHistoricalEndpointTableSize()
        }
    return ResponseObject(data=d)

@app.get(f'{APP_PREFIX}/api/downloadPeerTrackingTable')
def API_DownloadPeerTackingTable():
    configurationName = request.args.get("configurationName")
    table = request.args.get('table')
    if configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    if table not in ['TrafficTrackingTable', 'HistoricalTrackingTable']:
        return ResponseObject(False, "Table does not exist")
    c = WireguardConfigurations.get(configurationName)
    return ResponseObject(
        data=c.downloadTransferTable() if table == 'TrafficTrackingTable' 
        else c.downloadHistoricalEndpointTable())

@app.post(f'{APP_PREFIX}/api/deletePeerTrackingTable')
def API_DeletePeerTrackingTable():
    data = request.get_json()
    configurationName = data.get('configurationName')
    table = data.get('table')
    if not configurationName or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    if not table or table not in ['TrafficTrackingTable', 'HistoricalTrackingTable']:
        return ResponseObject(False, "Table does not exist")
    c = WireguardConfigurations.get(configurationName)
    return ResponseObject(
        status=c.deleteTransferTable() if table == 'TrafficTrackingTable'
        else c.deleteHistoryEndpointTable())

@app.get(f'{APP_PREFIX}/api/getDashboardTheme')
def API_getDashboardTheme():
    return ResponseObject(data=DashboardConfig.GetConfig("Server", "dashboard_theme")[1])

@app.get(f'{APP_PREFIX}/api/getDashboardVersion')
def API_getDashboardVersion():
    return ResponseObject(data=DashboardConfig.GetConfig("Server", "version")[1])

@app.post(f'{APP_PREFIX}/api/savePeerScheduleJob')
def API_savePeerScheduleJob():
    data = request.json
    if "Job" not in data.keys():
        return ResponseObject(False, "Please specify job")
    job: dict = data['Job']
    if "Peer" not in job.keys() or "Configuration" not in job.keys():
        return ResponseObject(False, "Please specify peer and configuration")
    configuration = WireguardConfigurations.get(job['Configuration'])
    if configuration is None:
        return ResponseObject(False, "Configuration does not exist")
    f, fp = configuration.searchPeer(job['Peer'])
    if not f:
        return ResponseObject(False, "Peer does not exist")
    
    
    s, p = AllPeerJobs.saveJob(PeerJob(
        job['JobID'], job['Configuration'], job['Peer'], job['Field'], job['Operator'], job['Value'],
        job['CreationDate'], job['ExpireDate'], job['Action']))
    if s:
        return ResponseObject(s, data=p)
    return ResponseObject(s, message=p)

@app.post(f'{APP_PREFIX}/api/deletePeerScheduleJob')
def API_deletePeerScheduleJob():
    data = request.json
    if "Job" not in data.keys():
        return ResponseObject(False, "Please specify job")
    job: dict = data['Job']
    if "Peer" not in job.keys() or "Configuration" not in job.keys():
        return ResponseObject(False, "Please specify peer and configuration")
    configuration = WireguardConfigurations.get(job['Configuration'])
    if configuration is None:
        return ResponseObject(False, "Configuration does not exist")
    # f, fp = configuration.searchPeer(job['Peer'])
    # if not f:
    #     return ResponseObject(False, "Peer does not exist")

    s, p = AllPeerJobs.deleteJob(PeerJob(
        job['JobID'], job['Configuration'], job['Peer'], job['Field'], job['Operator'], job['Value'],
        job['CreationDate'], job['ExpireDate'], job['Action']))
    if s:
        return ResponseObject(s)
    return ResponseObject(s, message=p)

@app.get(f'{APP_PREFIX}/api/getPeerScheduleJobLogs/<configName>')
def API_getPeerScheduleJobLogs(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    data = request.args.get("requestAll")
    requestAll = False
    if data is not None and data == "true":
        requestAll = True
    return ResponseObject(data=AllPeerJobs.getPeerJobLogs(configName))

'''
Tools
'''

@app.get(f'{APP_PREFIX}/api/ping/getAllPeersIpAddress')
def API_ping_getAllPeersIpAddress():
    ips = {}
    for c in WireguardConfigurations.values():
        cips = {}
        for p in c.Peers:
            allowed_ip = p.allowed_ip.replace(" ", "").split(",")
            parsed = []
            for x in allowed_ip:
                try:
                    ip = ipaddress.ip_network(x, strict=False)
                except ValueError as e:
                    app.logger.error(f"Failed to parse IP address of {p.id} - {c.Name}")
                host = list(ip.hosts())
                if len(host) == 1:
                    parsed.append(str(host[0]))
            endpoint = p.endpoint.replace(" ", "").replace("(none)", "")
            if len(p.name) > 0:
                cips[f"{p.name} - {p.id}"] = {
                    "allowed_ips": parsed,
                    "endpoint": endpoint
                }
            else:
                cips[f"{p.id}"] = {
                    "allowed_ips": parsed,
                    "endpoint": endpoint
                }
        ips[c.Name] = cips
    return ResponseObject(data=ips)

import requests

@app.get(f'{APP_PREFIX}/api/ping/execute')
def API_ping_execute():
    if "ipAddress" in request.args.keys() and "count" in request.args.keys():
        ip = request.args['ipAddress']
        count = request.args['count']
        try:
            if ip is not None and len(ip) > 0 and count is not None and count.isnumeric():
                result = ping(ip, count=int(count), source=None)
                data = {
                    "address": result.address,
                    "is_alive": result.is_alive,
                    "min_rtt": result.min_rtt,
                    "avg_rtt": result.avg_rtt,
                    "max_rtt": result.max_rtt,
                    "package_sent": result.packets_sent,
                    "package_received": result.packets_received,
                    "package_loss": result.packet_loss,
                    "geo": None
                }
                try:
                    r = requests.get(f"http://ip-api.com/json/{result.address}?field=city")
                    data['geo'] = r.json()
                except Exception as e:
                    pass
                return ResponseObject(data=data)
            return ResponseObject(False, "Please specify an IP Address (v4/v6)")
        except Exception as exp:
            return ResponseObject(False, exp)
    return ResponseObject(False, "Please provide ipAddress and count")


@app.get(f'{APP_PREFIX}/api/traceroute/execute')
def API_traceroute_execute():
    if "ipAddress" in request.args.keys() and len(request.args.get("ipAddress")) > 0:
        ipAddress = request.args.get('ipAddress')
        try:
            tracerouteResult = traceroute(ipAddress, timeout=1, max_hops=64)
            result = []
            for hop in tracerouteResult:
                if len(result) > 1:
                    skipped = False
                    for i in range(result[-1]["hop"] + 1, hop.distance):
                        result.append(
                            {
                                "hop": i,
                                "ip": "*",
                                "avg_rtt": "*",
                                "min_rtt": "*",
                                "max_rtt": "*"
                            }
                        )
                        skip = True
                    if skipped: continue
                result.append(
                    {
                        "hop": hop.distance,
                        "ip": hop.address,
                        "avg_rtt": hop.avg_rtt,
                        "min_rtt": hop.min_rtt,
                        "max_rtt": hop.max_rtt
                    })
            try:
                r = requests.post(f"http://ip-api.com/batch?fields=city,country,lat,lon,query",
                                  data=json.dumps([x['ip'] for x in result]))
                d = r.json()
                for i in range(len(result)):
                    result[i]['geo'] = d[i]

                return ResponseObject(data=result)

            except Exception as e:
                app.logger.error(f"Failed to gather the geolocation data: {e}")
                return ResponseObject(data=result, message="Failed to request IP address geolocation")
    
        except Exception as e:
            app.logger.error(f"Failed to execute the traceroute: {e}")
            return ResponseObject(data=[], message="Failed to traceroute the given parameter")
    else:
        return ResponseObject(False, "Please provide ipAddress")

@app.get(f'{APP_PREFIX}/api/getDashboardUpdate')
def API_getDashboardUpdate():
    import urllib.request as req
    try:
        r = req.urlopen("https://api.github.com/repos/WGDashboard/WGDashboard/releases/latest", timeout=5).read()
        data = dict(json.loads(r))
        tagName = data.get('tag_name')
        htmlUrl = data.get('html_url')
        if tagName is not None and htmlUrl is not None:
            if version.parse(tagName) > version.parse(DashboardConfig.DashboardVersion):
                return ResponseObject(message=f"{tagName} is now available for update!", data=htmlUrl)
            else:
                return ResponseObject(message="You're on the latest version")
        return ResponseObject(False)
    except Exception as e:
        return ResponseObject(False, f"Request to GitHub API failed.")

'''
Sign Up
'''

@app.get(f'{APP_PREFIX}/api/isTotpEnabled')
def API_isTotpEnabled():
    return (
        ResponseObject(data=DashboardConfig.GetConfig("Account", "enable_totp")[1] and DashboardConfig.GetConfig("Account", "totp_verified")[1]))


@app.get(f'{APP_PREFIX}/api/Welcome_GetTotpLink')
def API_Welcome_GetTotpLink():
    if not DashboardConfig.GetConfig("Account", "totp_verified")[1]:
        DashboardConfig.SetConfig("Account", "totp_key", pyotp.random_base32(), True)
        return ResponseObject(
            data=pyotp.totp.TOTP(DashboardConfig.GetConfig("Account", "totp_key")[1]).provisioning_uri(
                issuer_name="WGDashboard"))
    return ResponseObject(False)


@app.post(f'{APP_PREFIX}/api/Welcome_VerifyTotpLink')
def API_Welcome_VerifyTotpLink():
    data = request.get_json()
    totp = pyotp.TOTP(DashboardConfig.GetConfig("Account", "totp_key")[1]).now()
    if totp == data['totp']:
        DashboardConfig.SetConfig("Account", "totp_verified", "true")
        DashboardConfig.SetConfig("Account", "enable_totp", "true")
    return ResponseObject(totp == data['totp'])

@app.post(f'{APP_PREFIX}/api/Welcome_Finish')
def API_Welcome_Finish():
    data = request.get_json()
    if DashboardConfig.GetConfig("Other", "welcome_session")[1]:
        if data["username"] == "":
            return ResponseObject(False, "Username cannot be blank.")

        if data["newPassword"] == "" or len(data["newPassword"]) < 8:
            return ResponseObject(False, "Password must be at least 8 characters")

        updateUsername, updateUsernameErr = DashboardConfig.SetConfig("Account", "username", data["username"])
        updatePassword, updatePasswordErr = DashboardConfig.SetConfig("Account", "password",
                                                                      {
                                                                          "newPassword": data["newPassword"],
                                                                          "repeatNewPassword": data["repeatNewPassword"],
                                                                          "currentPassword": "admin"
                                                                      })
        if not updateUsername or not updatePassword:
            return ResponseObject(False, f"{updateUsernameErr},{updatePasswordErr}".strip(","))

        DashboardConfig.SetConfig("Other", "welcome_session", False)
    return ResponseObject()

class Locale:
    def __init__(self):
        self.localePath = './static/locales/'
        self.activeLanguages = {}
        with open(os.path.join(f"{self.localePath}supported_locales.json"), "r") as f:
            self.activeLanguages = sorted(json.loads(''.join(f.readlines())), key=lambda x : x['lang_name'])
        
    def getLanguage(self) -> dict | None:
        currentLanguage = DashboardConfig.GetConfig("Server", "dashboard_language")[1]
        if currentLanguage == "en":
            return None
        if os.path.exists(os.path.join(f"{self.localePath}{currentLanguage}.json")):
            with open(os.path.join(f"{self.localePath}{currentLanguage}.json"), "r") as f:
                return dict(json.loads(''.join(f.readlines())))
        else:
            return None
    
    def updateLanguage(self, lang_id):
        if not os.path.exists(os.path.join(f"{self.localePath}{lang_id}.json")):
            DashboardConfig.SetConfig("Server", "dashboard_language", "en-US")
        else:
            DashboardConfig.SetConfig("Server", "dashboard_language", lang_id)
        
Locale = Locale()

@app.get(f'{APP_PREFIX}/api/locale')
def API_Locale_CurrentLang():    
    return ResponseObject(data=Locale.getLanguage())

@app.get(f'{APP_PREFIX}/api/locale/available')
def API_Locale_Available():
    return ResponseObject(data=Locale.activeLanguages)
        
@app.post(f'{APP_PREFIX}/api/locale/update')
def API_Locale_Update():
    data = request.get_json()
    if 'lang_id' not in data.keys():
        return ResponseObject(False, "Please specify a lang_id")
    Locale.updateLanguage(data['lang_id'])
    return ResponseObject(data=Locale.getLanguage())

@app.get(f'{APP_PREFIX}/api/email/ready')
def API_Email_Ready():
    return ResponseObject(EmailSender.is_ready())

@app.post(f'{APP_PREFIX}/api/email/send')
def API_Email_Send():
    data = request.get_json()
    if "Receiver" not in data.keys() or "Subject" not in data.keys():
        return ResponseObject(False, "Please at least specify receiver and subject")
    body = data.get('Body', '')
    subject = data.get('Subject','')
    download = None
    if ("ConfigurationName" in data.keys() 
            and "Peer" in data.keys()):
        if data.get('ConfigurationName') in WireguardConfigurations.keys():
            configuration = WireguardConfigurations.get(data.get('ConfigurationName'))
            attachmentName = ""
            if configuration is not None:
                fp, p = configuration.searchPeer(data.get('Peer'))
                if fp:
                    template = Template(body)
                    download = p.downloadPeer()
                    body = template.render(peer=p.toJson(), configurationFile=download)
                    subject = Template(data.get('Subject', '')).render(peer=p.toJson(), configurationFile=download)
                    if data.get('IncludeAttachment', False):
                        u = str(uuid4())
                        attachmentName = f'{u}.conf'
                        with open(os.path.join('./attachments', attachmentName,), 'w+') as f:
                            f.write(download['file'])   
                        
    
    s, m = EmailSender.send(data.get('Receiver'), subject, body,  
                            data.get('IncludeAttachment', False), (attachmentName if download else ''))
    return ResponseObject(s, m)

@app.post(f'{APP_PREFIX}/api/email/preview')
def API_Email_PreviewBody():
    data = request.get_json()
    subject = data.get('Subject', '')
    body = data.get('Body', '')
    
    if ("ConfigurationName" not in data.keys() 
            or "Peer" not in data.keys() or data.get('ConfigurationName') not in WireguardConfigurations.keys()):
        return ResponseObject(False, "Please specify configuration and peer")
    
    configuration = WireguardConfigurations.get(data.get('ConfigurationName'))
    fp, p = configuration.searchPeer(data.get('Peer'))
    if not fp:
        return ResponseObject(False, "Peer does not exist")

    try:
        template = Template(body)
        download = p.downloadPeer()
        return ResponseObject(data={
            "Body": Template(body).render(peer=p.toJson(), configurationFile=download),
            "Subject": Template(subject).render(peer=p.toJson(), configurationFile=download)
        })
    except Exception as e:
        return ResponseObject(False, message=str(e))

@app.get(f'{APP_PREFIX}/api/systemStatus')
def API_SystemStatus():
    return ResponseObject(data=SystemStatus)

@app.get(f'{APP_PREFIX}/api/protocolsEnabled')
def API_ProtocolsEnabled():
    return ResponseObject(data=ProtocolsEnabled())

'''
OIDC Controller
'''
@app.get(f'{APP_PREFIX}/api/oidc/toggle')
def API_OIDC_Toggle():
    data = request.args
    if not data.get('mode'):
        return ResponseObject(False, "Please provide mode")
    mode = data.get('mode')
    if mode == 'Client':
        DashboardConfig.SetConfig("OIDC", "client_enable", 
                                  not DashboardConfig.GetConfig("OIDC", "client_enable")[1])
    elif mode == 'Admin':
        DashboardConfig.SetConfig("OIDC", "admin_enable",
                                  not DashboardConfig.GetConfig("OIDC", "admin_enable")[1])
    else:
        return ResponseObject(False, "Mode does not exist")
    return ResponseObject()

@app.get(f'{APP_PREFIX}/api/oidc/status')
def API_OIDC_Status():
    data = request.args
    if not data.get('mode'):
        return ResponseObject(False, "Please provide mode")
    mode = data.get('mode')
    if mode == 'Client':
        return ResponseObject(data=DashboardConfig.GetConfig("OIDC", "client_enable")[1])
    elif mode == 'Admin':
        return ResponseObject(data=DashboardConfig.GetConfig("OIDC", "admin_enable")[1])
    return ResponseObject(False, "Mode does not exist")

'''
Client Controller
'''

@app.get(f'{APP_PREFIX}/api/clients/toggleStatus')
def API_Clients_ToggleStatus():
    DashboardConfig.SetConfig("Clients", "enable",
                              not DashboardConfig.GetConfig("Clients", "enable")[1])
    return ResponseObject(data=DashboardConfig.GetConfig("Clients", "enable")[1])


@app.get(f'{APP_PREFIX}/api/clients/allClients')
def API_Clients_AllClients():
    return ResponseObject(data=DashboardClients.GetAllClients())

@app.get(f'{APP_PREFIX}/api/clients/allClientsRaw')
def API_Clients_AllClientsRaw():
    return ResponseObject(data=DashboardClients.GetAllClientsRaw())

@app.post(f'{APP_PREFIX}/api/clients/createClient')
def API_Clients_CreateClient():
    data = request.get_json() or {}
    status, message = DashboardClients.SignUp(
        data.get('Email', ''),
        data.get('Password', ''),
        data.get('ConfirmPassword', ''),
        data.get('Name'),
    )
    return ResponseObject(status=status, message=message, status_code=200 if status else 400)

@app.post(f'{APP_PREFIX}/api/clients/assignClient')
def API_Clients_AssignClient():
    data = request.get_json()
    configurationName = data.get('ConfigurationName')
    id = data.get('Peer')
    client = data.get('ClientID')
    if not all([configurationName, id, client]):
        return ResponseObject(False, "Please provide all required fields")
    if not DashboardClients.GetClient(client):
        return ResponseObject(False, "Client does not exist")
    
    status, data = DashboardClients.AssignClient(configurationName, id, client)
    if not status:
        return ResponseObject(status, message="Client already assiged to this peer")
    
    return ResponseObject(data=data)

@app.post(f'{APP_PREFIX}/api/clients/unassignClient')
def API_Clients_UnassignClient():
    data = request.get_json()
    assignmentID = data.get('AssignmentID')
    if not assignmentID:
        return ResponseObject(False, "Please provide AssignmentID")
    return ResponseObject(status=DashboardClients.UnassignClient(assignmentID))

@app.get(f'{APP_PREFIX}/api/clients/assignedClients')
def API_Clients_AssignedClients():
    data = request.args
    configurationName = data.get('ConfigurationName')
    peerID = data.get('Peer')
    if not all([configurationName, peerID]):
        return ResponseObject(False, "Please provide all required fields")
    return ResponseObject(
        data=DashboardClients.GetAssignedPeerClients(configurationName, peerID))

@app.get(f'{APP_PREFIX}/api/clients/allConfigurationsPeers')
def API_Clients_AllConfigurationsPeers():
    c = {}
    for (key, val) in WireguardConfigurations.items():
        c[key] = list(map(lambda x : {
            "id": x.id,
            "name": x.name
        }, val.Peers))
    
    return ResponseObject(
        data=c
    )

@app.get(f'{APP_PREFIX}/api/clients/assignedPeers')
def API_Clients_AssignedPeers():
    data = request.args
    clientId = data.get("ClientID")
    if not clientId:
        return ResponseObject(False, "Please provide ClientID")
    if not DashboardClients.GetClient(clientId):
        return ResponseObject(False, "Client does not exist")
    d = DashboardClients.GetClientAssignedPeersGrouped(clientId)
    if d is None:
        return ResponseObject(False, "Client does not exist")
    return ResponseObject(data=d)

@app.post(f'{APP_PREFIX}/api/clients/generatePasswordResetLink')
def API_Clients_GeneratePasswordResetLink():
    data = request.get_json()
    clientId = data.get("ClientID")
    if not clientId:
        return ResponseObject(False, "Please provide ClientID")
    if not DashboardClients.GetClient(clientId):
        return ResponseObject(False, "Client does not exist")
    
    token = DashboardClients.GenerateClientPasswordResetToken(clientId)
    if token:
        return ResponseObject(data=token)
    return ResponseObject(False, "Failed to generate link")

@app.post(f'{APP_PREFIX}/api/clients/updateProfileName')
def API_Clients_UpdateProfile():
    data = request.get_json()
    clientId = data.get("ClientID")
    if not clientId:
        return ResponseObject(False, "Please provide ClientID")
    if not DashboardClients.GetClient(clientId):
        return ResponseObject(False, "Client does not exist")
    
    value = data.get('Name')
    return ResponseObject(status=DashboardClients.UpdateClientProfile(clientId, value))

@app.post(f'{APP_PREFIX}/api/clients/deleteClient')
def API_Clients_DeleteClient():
    data = request.get_json()
    clientId = data.get("ClientID")
    if not clientId:
        return ResponseObject(False, "Please provide ClientID")
    if not DashboardClients.GetClient(clientId):
        return ResponseObject(False, "Client does not exist")
    return ResponseObject(status=DashboardClients.DeleteClient(clientId))   

@app.get(f'{APP_PREFIX}/api/webHooks/getWebHooks')
def API_WebHooks_GetWebHooks():
    return ResponseObject(data=DashboardWebHooks.GetWebHooks())

@app.get(f'{APP_PREFIX}/api/webHooks/createWebHook')
def API_WebHooks_createWebHook():
    return ResponseObject(data=DashboardWebHooks.CreateWebHook().model_dump(
        exclude={'CreationDate'}
    ))

@app.post(f'{APP_PREFIX}/api/webHooks/updateWebHook')
def API_WebHooks_UpdateWebHook():
    data = request.get_json()
    status, msg = DashboardWebHooks.UpdateWebHook(data)
    return ResponseObject(status, msg)

@app.post(f'{APP_PREFIX}/api/webHooks/deleteWebHook')
def API_WebHooks_DeleteWebHook():
    data = request.get_json()
    status, msg = DashboardWebHooks.DeleteWebHook(data)
    return ResponseObject(status, msg)

@app.get(f'{APP_PREFIX}/api/webHooks/getWebHookSessions')
def API_WebHooks_GetWebHookSessions():
    webhookID = request.args.get('WebHookID')
    if not webhookID:
        return ResponseObject(False, "Please provide WebHookID")
    
    webHook = DashboardWebHooks.SearchWebHookByID(webhookID)
    if not webHook:
        return ResponseObject(False, "Webhook does not exist")
    
    return ResponseObject(data=DashboardWebHooks.GetWebHookSessions(webHook))
    

'''
Index Page
'''

@app.get(f'{APP_PREFIX}/')
def index():
    response = current_app.make_response(render_template('index.html', APP_PREFIX=APP_PREFIX))
    # index.html contains content-hashed asset names. Caching it across a
    # deployment can leave the browser requesting bundles removed by the new
    # image, which looks like an endless loading screen.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

if __name__ == "__main__":
    startThreads()
    DashboardPlugins.startThreads()
    app.run(host=app_ip, debug=False, port=app_port)
