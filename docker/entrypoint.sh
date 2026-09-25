#!/bin/bash

config_file="/data/wg-dashboard.ini"
runtime_pid=""

trap 'stop_service' SIGTERM SIGINT

# Hash password with bcrypt
hash_password() {
  ${WGDASH}/src/venv/bin/python3 -c \
    'import bcrypt, sys; print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt(12)).decode())' \
    "$1"
}

password_matches() {
  ${WGDASH}/src/venv/bin/python3 - "$config_file" "$1" <<'PY'
import bcrypt
import configparser
import sys

parser = configparser.RawConfigParser(strict=False)
parser.read(sys.argv[1], encoding="utf-8")
stored = parser.get("Account", "password", fallback="")
try:
    matches = bcrypt.checkpw(sys.argv[2].encode(), stored.encode())
except (ValueError, TypeError):
    matches = False
raise SystemExit(0 if matches else 1)
PY
}

# Function to set or update section/key/value in the INI file
set_ini() {
  local section="$1" key="$2" value="$3"
  local action

  action=$(${WGDASH}/src/venv/bin/python3 - "$config_file" "$section" "$key" "$value" <<'PY'
import configparser
import os
import sys
import tempfile

path, section, key, value = sys.argv[1:]
parser = configparser.RawConfigParser(strict=False)
parser.optionxform = str
parser.read(path, encoding="utf-8")

if not parser.has_section(section):
    parser.add_section(section)

existed = parser.has_option(section, key)
if existed and parser.get(section, key) == value:
    print("unchanged")
    raise SystemExit(0)

parser.set(section, key, value)
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=os.path.dirname(path), delete=False) as tmp:
    parser.write(tmp)
    temporary_path = tmp.name
os.replace(temporary_path, path)
print("updated" if existed else "added")
PY
  )

  if [[ "$key" == *"password"* ]]; then
    echo "- ${action^} $key (value hidden)"
  elif [[ "$action" == "unchanged" ]]; then
    echo "- $key is already set correctly ($value)"
  else
    echo "- ${action^} $key: $value"
  fi
}

stop_service() {
  echo "[WGDashboard] Stopping WGDashboard..."

  if [[ -f ${WGDASH}/src/gunicorn.pid ]]; then
    runtime_pid=$(cat ${WGDASH}/src/gunicorn.pid)
    echo "Stopping Gunicorn PID: ${runtime_pid}"
    kill -TERM "${runtime_pid}" 2>/dev/null || true
  fi

  if [[ -n "${tail_pid:-}" ]]; then
    kill -TERM "${tail_pid}" 2>/dev/null || true
  fi
  exit 0
}

echo "------------------------- START ----------------------------"
echo "Starting the WGDashboard Docker container."

ensure_installation() {
  # Make the wgd.sh script executable.
  chmod +x "${WGDASH}"/src/wgd.sh
  cd "${WGDASH}"/src || exit

  # Github issue: https://github.com/donaldzou/WGDashboard/issues/723
  echo "Checking for stale pids..."
  if [[ -f ${WGDASH}/src/gunicorn.pid ]]; then
    echo "Found stale pid, removing..."
    rm ${WGDASH}/src/gunicorn.pid
  fi

  # Removing clear shell command from the wgd.sh script to enhance docker logging.
  echo "Removing clear command from wgd.sh for better Docker logging."
  sed -i '/clear/d' ./wgd.sh

  # PERSISTENCE FOR databases directory
  # Create required directories and links
  if [ ! -d "/data/db" ]; then
    echo "Creating database dir"
    mkdir -p /data/db
  fi

  if [[ ! -L "${WGDASH}/src/db" ]] && [[ -d "${WGDASH}/src/db" ]]; then
    echo "Removing ${WGDASH}/src/db since its not a symbolic link."
    rm -rfv "${WGDASH}/src/db"
  fi
  if [[ -L "${WGDASH}/src/db" ]]; then
    echo "${WGDASH}/src/db is a symbolic link."
  else
    ln -sv /data/db "${WGDASH}/src/db"
  fi

  # PERSISTENCE FOR wg-dashboard-oidc-providers.json
  if [ ! -f "/data/wg-dashboard-oidc-providers.json" ]; then
    echo "Creating wg-dashboard-oidc-providers.json file"
    cp -v /tmp/wg-dashboard-oidc-providers.json.template /data/wg-dashboard-oidc-providers.json
  fi
  if [[ ! -L "${WGDASH}/src/wg-dashboard-oidc-providers.json" ]] && [[ -f "${WGDASH}/src/wg-dashboard-oidc-providers.json" ]]; then
    echo "Removing ${WGDASH}/src/wg-dashboard-oidc-providers.json since its not a symbolic link."
    rm -fv "${WGDASH}/src/wg-dashboard-oidc-providers.json"
  fi
  if [[ -L "${WGDASH}/src/wg-dashboard-oidc-providers.json" ]]; then
    echo "${WGDASH}/src/wg-dashboard-oidc-providers.json is a symbolic link."
  else
    ln -sv /data/wg-dashboard-oidc-providers.json "${WGDASH}/src/wg-dashboard-oidc-providers.json"
  fi

  # PERSISTENCE FOR wg-dashboard.ini
  if [ ! -f "${config_file}" ]; then
    echo "Creating wg-dashboard.ini file"
    touch "${config_file}"
  fi
  if [[ ! -L "${WGDASH}/src/wg-dashboard.ini" ]] && [[ -f "${WGDASH}/src/wg-dashboard.ini" ]]; then
    echo "Removing ${WGDASH}/src/wg-dashboard.ini since its not a symbolic link."
    rm -fv "${WGDASH}/src/wg-dashboard.ini"
  fi
  if [[ -L "${WGDASH}/src/wg-dashboard.ini" ]]; then
    echo "${WGDASH}/src/wg-dashboard.ini is a symbolic link."
  else
    ln -sv "${config_file}" "${WGDASH}/src/wg-dashboard.ini"
  fi

  # Setup WireGuard if needed
  if [ -z "$(ls -A /etc/wireguard)" ]; then
    cp -a "/configs/wg0.conf.template" "/etc/wireguard/wg0.conf"

    echo "Setting a secure private key."
    local privateKey
    privateKey=$(wg genkey)
    sed -i "s|^PrivateKey *=.*$|PrivateKey = ${privateKey}|g" /etc/wireguard/wg0.conf

    echo "Done setting template."
  else
    echo "Existing wg0 configuration file found, using that."
  fi

  # A DNS entry belongs in client configurations, not on the server interface.
  # wg-quick hands this value to resolvconf, which replaces Docker's embedded
  # DNS resolver and makes Compose service names such as "postgres" unreachable.
  if [[ -f /etc/wireguard/wg0.conf ]]; then
    sed -i '/^[[:space:]]*DNS[[:space:]]*=/d' /etc/wireguard/wg0.conf
  fi
}

set_envvars() {
  printf "\n------------- SETTING ENVIRONMENT VARIABLES ----------------\n"

  # Check if config file is empty
  if [ ! -s "${config_file}" ]; then
    echo "Config file is empty. Creating initial structure."
  elif [[ ${dynamic_config,,} =~ ^(false|no)$ ]]; then
    echo "Dynamic configuration feature turned off, not changing anything"
    return
  fi

  echo "Checking basic configuration:"
  set_ini Peers peer_global_dns "${global_dns}"

  if [ -z "${public_ip}" ]; then
    public_ip=$(curl -s https://ifconfig.me)
    if [ -z "${public_ip}" ]; then
        echo "Using fallback public IP resolution website"
        public_ip=$(curl -s https://api.ipify.org)
    fi
    if [ -z "${public_ip}" ]; then
        echo "Failed to resolve publicly. Using private address."
        public_ip=$(hostname -i)
    fi
    echo "Automatically detected public IP: ${public_ip}"
  fi

  set_ini Peers remote_endpoint "${public_ip}"
  set_ini Server app_port "${wgd_port}"

  # Account settings - process all parameters
  [[ -n "$username" ]] && echo "Configuring user account:"
  # Basic account variables
  [[ -n "$username" ]] && set_ini Account username "${username}"

  if [[ -n "$password" ]]; then
    if password_matches "${password}"; then
      echo "- password is already set correctly (value hidden)"
    else
      echo "- Setting password"
      set_ini Account password "$(hash_password "${password}")"
    fi
  fi

  # Additional account variables
  [[ -n "$enable_totp" ]] && set_ini Account enable_totp "${enable_totp}"
  [[ -n "$totp_verified" ]] && set_ini Account totp_verified "${totp_verified}"
  [[ -n "$totp_key" ]] && set_ini Account totp_key "${totp_key}"

  # Welcome session
  [[ -n "$welcome_session" ]] && set_ini Other welcome_session "${welcome_session}"
  # If username and password are set but welcome_session isn't, disable it
  if [[ -n "$username" && -n "$password" && -z "$welcome_session" ]]; then
    set_ini Other welcome_session "false"
  fi

  # Autostart WireGuard
  if [[ -n "$wg_autostart" ]]; then
    echo "Configuring WireGuard autostart:"
    set_ini WireGuardConfiguration autostart "${wg_autostart}"
  fi

  # Database (check if any settings need to be configured)
  database_vars=("database_type" "database_host" "database_port" "database_username" "database_password")
  for var in "${database_vars[@]}"; do
    if [ -n "${!var}" ]; then
      echo "Configuring database settings:"
      break
    fi
  done

  # Database (iterate through all possible fields)
  database_fields=("type:database_type" "host:database_host" "port:database_port" 
                "username:database_username" "password:database_password")

  for field_pair in "${database_fields[@]}"; do
    IFS=: read -r field var <<< "$field_pair"
    [[ -n "${!var}" ]] && set_ini Database "$field" "${!var}"
  done

  # Email (check if any settings need to be configured)
  email_vars=("email_server" "email_port" "email_encryption" "email_username" "email_password" "email_from" "email_template")
  for var in "${email_vars[@]}"; do
    if [ -n "${!var}" ]; then
      echo "Configuring email settings:"
      break
    fi
  done

  # Email (iterate through all possible fields)
  email_fields=("server:email_server" "port:email_port" "encryption:email_encryption" 
                "username:email_username" "email_password:email_password" 
                "send_from:email_from" "email_template:email_template")

  for field_pair in "${email_fields[@]}"; do
    IFS=: read -r field var <<< "$field_pair"
    [[ -n "${!var}" ]] && set_ini Email "$field" "${!var}"
  done
}

# Start service and monitor logs
start_and_monitor() {
  printf "\n---------------------- STARTING CORE -----------------------\n"

  # Due to some instances complaining about this, making sure its there every time.
  mkdir -p /dev/net
  mknod /dev/net/tun c 10 200
  chmod 600 /dev/net/tun

  # Actually starting WGDashboard
  echo "Starting WGDashboard directly with Gunicorn..."

  [[ ! -d ${WGDASH}/src/log ]] && mkdir ${WGDASH}/src/log
  [[ ! -d ${WGDASH}/src/download ]] && mkdir ${WGDASH}/src/download

  ${WGDASH}/src/venv/bin/gunicorn --config ${WGDASH}/src/gunicorn.conf.py
  local gunicorn_status=$?

  if [ $gunicorn_status -ne 0 ]; then
    echo "Loading WGDashboard failed... Look above for details."
  fi

  # Wait a second before continuing, to give the python program some time to get ready.
  echo -e "\nEnsuring container continuation."

  local max_rounds="10"
  local round="0"

  # Hang in there for 10s for Gunicorn to get ready
  while true; do
    round=$((round + 1))

    local latest_error=$(ls -t ${WGDASH}/src/log/error_*.log 2> /dev/null | head -n 1)

    if [[ $round -eq $max_rounds ]]; then
      echo "Reached breaking point!"
      break

    fi

    if [[ -z $latest_error ]]; then
      echo -e "Logs not yet present! Retrying in 1 second!"
      sleep 1s

    else
      break

    fi

  done

  if [[ -z $latest_error ]]; then
    echo -e "No error logs founds... Please investigate.\nExiting in 3 minutes..."
    sleep 180s
    exit 1

  else
    tail -f "$latest_error" &
    tail_pid=$!

    wait $tail_pid
  fi
}

# Main execution flow
ensure_installation
set_envvars
start_and_monitor
