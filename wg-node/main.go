package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const version = "0.2.0"

type Config struct {
	PanelURL              string
	NodeID                string
	NodeToken             string
	BootstrapToken        string
	NodeName              string
	Region                string
	PublicEndpoint        string
	AddressPool           string
	DefaultInterface      string
	Capacity              int
	PollInterval          time.Duration
	ReportInterval        time.Duration
	StatePath             string
	CredentialsPath       string
	CACertificatePath     string
	ClientCertificatePath string
	ClientKeyPath         string
	InsecureTLS           bool
	EnablePreshared       bool
	OutboundDirectory     string
	InterfacePools        map[string]string
	InterfaceEndpoints    map[string]string
}

type FileConfig struct {
	PanelURL              string            `json:"panel_url"`
	NodeID                string            `json:"node_id"`
	NodeToken             string            `json:"node_token"`
	BootstrapToken        string            `json:"bootstrap_token"`
	NodeName              string            `json:"node_name"`
	Region                string            `json:"region"`
	PublicEndpoint        string            `json:"public_endpoint"`
	AddressPool           string            `json:"address_pool"`
	DefaultInterface      string            `json:"default_interface"`
	Capacity              int               `json:"capacity"`
	PollSeconds           int               `json:"poll_seconds"`
	ReportSeconds         int               `json:"report_seconds"`
	StatePath             string            `json:"state_path"`
	CredentialsPath       string            `json:"credentials_path"`
	CACertificatePath     string            `json:"ca_certificate"`
	ClientCertificatePath string            `json:"client_certificate"`
	ClientKeyPath         string            `json:"client_key"`
	InsecureTLS           bool              `json:"insecure_tls"`
	EnablePreshared       *bool             `json:"enable_preshared"`
	OutboundDirectory     string            `json:"outbound_directory"`
	InterfacePools        map[string]string `json:"interface_pools"`
	InterfaceEndpoints    map[string]string `json:"interface_endpoints"`
}

type APIResponse[T any] struct {
	Status  bool   `json:"status"`
	Message string `json:"message"`
	Data    T      `json:"data"`
}

type Credentials struct {
	NodeID string `json:"node_id"`
	Token  string `json:"agent_token"`
}

type Job struct {
	JobID     string         `json:"JobID"`
	Operation string         `json:"Operation"`
	Payload   map[string]any `json:"Payload"`
}

type PeerState struct {
	SubscriptionPeerID string `json:"subscription_peer_id"`
	Interface          string `json:"interface"`
	PublicKey          string `json:"public_key"`
	Address            string `json:"address"`
	PresharedKey       string `json:"preshared_key,omitempty"`
	Enabled            bool   `json:"enabled"`
}

type OutboundState struct {
	OutboundID        string `json:"outbound_id"`
	Interface         string `json:"interface"`
	SourceInterface   string `json:"source_interface"`
	SourceAddressPool string `json:"source_address_pool"`
	RoutingTable      int    `json:"routing_table"`
}

type PersistentState struct {
	SessionID string                    `json:"session_id"`
	Sequence  int64                     `json:"sequence"`
	Peers     map[string]*PeerState     `json:"peers"`
	Outbounds map[string]*OutboundState `json:"outbounds"`
}

type Agent struct {
	config Config
	client *http.Client
	mu     sync.Mutex
	state  PersistentState
}

var interfacePattern = regexp.MustCompile(`^[A-Za-z0-9_=+.-]{1,64}$`)

func main() {
	config, err := loadConfig()
	if err != nil {
		log.Fatal(err)
	}
	client, err := buildHTTPClient(config)
	if err != nil {
		log.Fatal(err)
	}
	agent := &Agent{config: config, client: client}
	if err := agent.loadState(); err != nil {
		log.Fatal(err)
	}
	if err := agent.loadOrRegisterCredentials(); err != nil {
		log.Fatal(err)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	log.Printf("wg-node %s starting as node %s", version, agent.config.NodeID)
	if err := agent.run(ctx); err != nil && !errors.Is(err, context.Canceled) {
		log.Fatal(err)
	}
}

func loadConfig() (Config, error) {
	fileConfig, err := loadFileConfig(env("WG_NODE_CONFIG", "/etc/wg-node/config.json"))
	if err != nil {
		return Config{}, err
	}
	capacity, _ := strconv.Atoi(env("WG_NODE_CAPACITY", strconv.Itoa(fileConfig.Capacity)))
	pollFallback := fileConfig.PollSeconds
	if pollFallback < 1 {
		pollFallback = 5
	}
	reportFallback := fileConfig.ReportSeconds
	if reportFallback < 1 {
		reportFallback = 15
	}
	pollSeconds, _ := strconv.Atoi(env("WG_NODE_POLL_SECONDS", strconv.Itoa(pollFallback)))
	reportSeconds, _ := strconv.Atoi(env("WG_NODE_REPORT_SECONDS", strconv.Itoa(reportFallback)))
	enablePreshared := true
	if fileConfig.EnablePreshared != nil {
		enablePreshared = *fileConfig.EnablePreshared
	}
	if value := os.Getenv("WG_NODE_PRESHARED_KEY"); value != "" {
		enablePreshared = !strings.EqualFold(value, "false")
	}
	insecureTLS := fileConfig.InsecureTLS
	if value := os.Getenv("WG_NODE_INSECURE_TLS"); value != "" {
		insecureTLS = strings.EqualFold(value, "true")
	}
	config := Config{
		PanelURL:              strings.TrimRight(env("WG_PANEL_URL", fileConfig.PanelURL), "/"),
		NodeID:                env("WG_NODE_ID", fileConfig.NodeID),
		NodeToken:             env("WG_NODE_TOKEN", fileConfig.NodeToken),
		BootstrapToken:        env("WG_NODE_BOOTSTRAP_TOKEN", fileConfig.BootstrapToken),
		NodeName:              env("WG_NODE_NAME", first(fileConfig.NodeName, hostname())),
		Region:                env("WG_NODE_REGION", fileConfig.Region),
		PublicEndpoint:        env("WG_NODE_PUBLIC_ENDPOINT", fileConfig.PublicEndpoint),
		AddressPool:           env("WG_NODE_ADDRESS_POOL", first(fileConfig.AddressPool, "10.88.0.0/24")),
		DefaultInterface:      env("WG_NODE_INTERFACE", first(fileConfig.DefaultInterface, "wg0")),
		Capacity:              capacity,
		PollInterval:          time.Duration(max(1, pollSeconds)) * time.Second,
		ReportInterval:        time.Duration(max(5, reportSeconds)) * time.Second,
		StatePath:             env("WG_NODE_STATE", first(fileConfig.StatePath, "/var/lib/wg-node/state.json")),
		CredentialsPath:       env("WG_NODE_CREDENTIALS", first(fileConfig.CredentialsPath, "/var/lib/wg-node/credentials.json")),
		CACertificatePath:     env("WG_NODE_CA_CERT", fileConfig.CACertificatePath),
		ClientCertificatePath: env("WG_NODE_CLIENT_CERT", fileConfig.ClientCertificatePath),
		ClientKeyPath:         env("WG_NODE_CLIENT_KEY", fileConfig.ClientKeyPath),
		InsecureTLS:           insecureTLS,
		EnablePreshared:       enablePreshared,
		OutboundDirectory:     env("WG_NODE_OUTBOUND_DIR", first(fileConfig.OutboundDirectory, "/etc/wg-node/outbounds")),
		InterfacePools:        fileConfig.InterfacePools,
		InterfaceEndpoints:    fileConfig.InterfaceEndpoints,
	}
	if config.InterfacePools == nil {
		config.InterfacePools = map[string]string{}
	}
	if config.InterfaceEndpoints == nil {
		config.InterfaceEndpoints = map[string]string{}
	}
	config.InterfacePools[config.DefaultInterface] = config.AddressPool
	if _, exists := config.InterfaceEndpoints[config.DefaultInterface]; !exists {
		config.InterfaceEndpoints[config.DefaultInterface] = config.PublicEndpoint
	}
	if config.PanelURL == "" {
		return Config{}, errors.New("WG_PANEL_URL is required")
	}
	if config.PublicEndpoint == "" {
		return Config{}, errors.New("WG_NODE_PUBLIC_ENDPOINT is required, for example vpn.example.com:51820")
	}
	if !validEndpoint(config.PublicEndpoint) {
		return Config{}, errors.New("WG_NODE_PUBLIC_ENDPOINT must include a valid host and port")
	}
	if !interfacePattern.MatchString(config.DefaultInterface) {
		return Config{}, errors.New("WG_NODE_INTERFACE is invalid")
	}
	if _, _, err := net.ParseCIDR(config.AddressPool); err != nil {
		return Config{}, fmt.Errorf("invalid WG_NODE_ADDRESS_POOL: %w", err)
	}
	for interfaceName, addressPool := range config.InterfacePools {
		if !interfacePattern.MatchString(interfaceName) {
			return Config{}, fmt.Errorf("invalid interface_pools name %q", interfaceName)
		}
		if _, network, err := net.ParseCIDR(addressPool); err != nil || network.IP.To4() == nil {
			return Config{}, fmt.Errorf("interface_pools[%s] must be a valid IPv4 CIDR", interfaceName)
		}
	}
	for interfaceName, endpoint := range config.InterfaceEndpoints {
		if !interfacePattern.MatchString(interfaceName) || !validEndpoint(endpoint) {
			return Config{}, fmt.Errorf("interface_endpoints[%s] must contain a valid host and port", interfaceName)
		}
	}
	return config, nil
}

func loadFileConfig(path string) (FileConfig, error) {
	var config FileConfig
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return config, nil
	}
	if err != nil {
		return config, fmt.Errorf("read node configuration: %w", err)
	}
	if err := json.Unmarshal(data, &config); err != nil {
		return config, fmt.Errorf("invalid node configuration: %w", err)
	}
	return config, nil
}

func buildHTTPClient(config Config) (*http.Client, error) {
	tlsConfig := &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: config.InsecureTLS} // #nosec G402 -- explicit operator opt-in
	if config.CACertificatePath != "" {
		pem, err := os.ReadFile(config.CACertificatePath)
		if err != nil {
			return nil, err
		}
		pool, err := x509.SystemCertPool()
		if err != nil || pool == nil {
			pool = x509.NewCertPool()
		}
		if !pool.AppendCertsFromPEM(pem) {
			return nil, errors.New("WG_NODE_CA_CERT does not contain a valid certificate")
		}
		tlsConfig.RootCAs = pool
	}
	if config.ClientCertificatePath != "" || config.ClientKeyPath != "" {
		if config.ClientCertificatePath == "" || config.ClientKeyPath == "" {
			return nil, errors.New("WG_NODE_CLIENT_CERT and WG_NODE_CLIENT_KEY must be set together")
		}
		certificate, err := tls.LoadX509KeyPair(config.ClientCertificatePath, config.ClientKeyPath)
		if err != nil {
			return nil, fmt.Errorf("load node mTLS certificate: %w", err)
		}
		tlsConfig.Certificates = []tls.Certificate{certificate}
	}
	return &http.Client{
		Timeout: 20 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig:    tlsConfig,
			MaxIdleConns:       10,
			IdleConnTimeout:    30 * time.Second,
			DisableCompression: false,
		},
	}, nil
}

func (a *Agent) loadState() error {
	a.state = PersistentState{
		SessionID: randomID(),
		Peers:     map[string]*PeerState{},
		Outbounds: map[string]*OutboundState{},
	}
	data, err := os.ReadFile(a.config.StatePath)
	if errors.Is(err, os.ErrNotExist) {
		return a.saveStateLocked()
	}
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, &a.state); err != nil {
		return fmt.Errorf("invalid state file: %w", err)
	}
	if a.state.Peers == nil {
		a.state.Peers = map[string]*PeerState{}
	}
	if a.state.Outbounds == nil {
		a.state.Outbounds = map[string]*OutboundState{}
	}
	for outboundID, outbound := range a.state.Outbounds {
		if outbound == nil || !interfacePattern.MatchString(outbound.Interface) {
			delete(a.state.Outbounds, outboundID)
			continue
		}
		if outbound.RoutingTable < 1 {
			outbound.RoutingTable = routingTable(outbound.Interface)
		}
	}
	a.state.SessionID = randomID()
	a.state.Sequence = 0
	return a.saveStateLocked()
}

func (a *Agent) saveStateLocked() error {
	if err := os.MkdirAll(filepath.Dir(a.config.StatePath), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(a.state, "", "  ")
	if err != nil {
		return err
	}
	temporary := a.config.StatePath + ".tmp"
	if err := os.WriteFile(temporary, data, 0o600); err != nil {
		return err
	}
	return os.Rename(temporary, a.config.StatePath)
}

func (a *Agent) loadOrRegisterCredentials() error {
	if a.config.NodeID != "" && a.config.NodeToken != "" {
		return nil
	}
	if data, err := os.ReadFile(a.config.CredentialsPath); err == nil {
		var credentials Credentials
		if json.Unmarshal(data, &credentials) == nil && credentials.NodeID != "" && credentials.Token != "" {
			a.config.NodeID, a.config.NodeToken = credentials.NodeID, credentials.Token
			return nil
		}
	}
	if a.config.BootstrapToken == "" {
		return errors.New("set WG_NODE_ID and WG_NODE_TOKEN, or provide WG_NODE_BOOTSTRAP_TOKEN for registration")
	}
	payload := map[string]any{
		"name": a.config.NodeName, "region": a.config.Region,
		"public_endpoint": a.config.PublicEndpoint, "capacity": a.config.Capacity,
	}
	var response APIResponse[Credentials]
	if err := a.request(context.Background(), http.MethodPost, "/api/node/v1/register", a.config.BootstrapToken, payload, &response); err != nil {
		return err
	}
	if !response.Status {
		return errors.New(response.Message)
	}
	a.config.NodeID, a.config.NodeToken = response.Data.NodeID, response.Data.Token
	if err := os.MkdirAll(filepath.Dir(a.config.CredentialsPath), 0o700); err != nil {
		return err
	}
	data, _ := json.MarshalIndent(response.Data, "", "  ")
	return os.WriteFile(a.config.CredentialsPath, data, 0o600)
}

func (a *Agent) run(ctx context.Context) error {
	pollTicker := time.NewTicker(a.config.PollInterval)
	reportTicker := time.NewTicker(a.config.ReportInterval)
	defer pollTicker.Stop()
	defer reportTicker.Stop()
	a.restorePeers(ctx)
	a.restoreOutbounds(ctx)
	_ = a.heartbeat(ctx)
	_ = a.pollJobs(ctx)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-pollTicker.C:
			if err := a.heartbeat(ctx); err != nil {
				log.Printf("heartbeat: %v", err)
			}
			if err := a.pollJobs(ctx); err != nil {
				log.Printf("jobs: %v", err)
			}
		case <-reportTicker.C:
			if err := a.reportTraffic(ctx); err != nil {
				log.Printf("traffic: %v", err)
			}
		}
	}
}

func (a *Agent) restorePeers(ctx context.Context) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for peerID, peer := range a.state.Peers {
		if peer == nil || !interfacePattern.MatchString(peer.Interface) || !validWireGuardKey(peer.PublicKey) {
			log.Printf("restore peer %s: invalid persisted peer state", peerID)
			continue
		}
		if peer.Enabled {
			if err := setPeer(ctx, peer); err != nil {
				log.Printf("restore peer %s: %v", peerID, err)
			}
			continue
		}
		if _, err := command(ctx, "wg", "set", peer.Interface, "peer", peer.PublicKey, "remove"); err != nil {
			log.Printf("restore disabled peer %s: %v", peerID, err)
			continue
		}
		if _, err := command(ctx, "wg-quick", "save", peer.Interface); err != nil {
			log.Printf("persist disabled peer %s: %v", peerID, err)
		}
	}
}

func (a *Agent) restoreOutbounds(ctx context.Context) {
	for outboundID, state := range a.state.Outbounds {
		if state == nil || !interfacePattern.MatchString(state.Interface) {
			log.Printf("restore outbound %s: invalid persisted outbound state", outboundID)
			continue
		}
		path := filepath.Join(a.config.OutboundDirectory, state.Interface+".conf")
		if _, err := os.Stat(path); err != nil {
			log.Printf("restore outbound %s: %v", outboundID, err)
			continue
		}
		if _, err := command(ctx, "wg", "show", state.Interface); err != nil {
			if _, err := command(ctx, "wg-quick", "up", path); err != nil {
				log.Printf("restore outbound %s: %v", outboundID, err)
				continue
			}
		}
		if err := configureOutboundRouting(ctx, state); err != nil {
			log.Printf("restore outbound routing %s: %v", outboundID, err)
		}
	}
}

func (a *Agent) heartbeat(ctx context.Context) error {
	payload := map[string]any{
		"agent_version": version, "public_endpoint": a.config.PublicEndpoint,
		"capacity": a.config.Capacity, "agent_session": a.state.SessionID,
		"interfaces": a.discoverInterfaces(ctx), "peers": a.peerInventory(),
		"outbounds": a.outboundInventory(),
	}
	var response APIResponse[map[string]any]
	return a.request(ctx, http.MethodPost, "/api/node/v1/heartbeat", a.config.NodeToken, payload, &response)
}

func (a *Agent) peerInventory() []map[string]any {
	peers := make([]map[string]any, 0, len(a.state.Peers))
	for _, peer := range a.state.Peers {
		peers = append(peers, map[string]any{
			"subscription_peer_id": peer.SubscriptionPeerID,
			"interface":            peer.Interface,
			"public_key":           peer.PublicKey,
			"address":              peer.Address,
			"enabled":              peer.Enabled,
		})
	}
	return peers
}

func (a *Agent) outboundInventory() []map[string]any {
	outbounds := make([]map[string]any, 0, len(a.state.Outbounds))
	for _, outbound := range a.state.Outbounds {
		outbounds = append(outbounds, map[string]any{
			"outbound_id": outbound.OutboundID, "interface": outbound.Interface,
			"source_interface":    outbound.SourceInterface,
			"source_address_pool": outbound.SourceAddressPool,
		})
	}
	return outbounds
}

func (a *Agent) discoverInterfaces(ctx context.Context) []map[string]any {
	output, err := command(ctx, "wg", "show", "interfaces")
	if err != nil {
		log.Printf("interface discovery: %v", err)
		return []map[string]any{}
	}
	outboundInterfaces := map[string]bool{}
	for _, outbound := range a.state.Outbounds {
		outboundInterfaces[outbound.Interface] = true
	}
	interfaces := make([]map[string]any, 0)
	for _, interfaceName := range strings.Fields(output) {
		if !interfacePattern.MatchString(interfaceName) || outboundInterfaces[interfaceName] {
			continue
		}
		publicKey, keyErr := command(ctx, "wg", "show", interfaceName, "public-key")
		listenPort, portErr := command(ctx, "wg", "show", interfaceName, "listen-port")
		addressPool := a.config.InterfacePools[interfaceName]
		if addressPool == "" {
			if addressOutput, addressErr := command(ctx, "ip", "-o", "-4", "addr", "show", "dev", interfaceName); addressErr == nil {
				for _, field := range strings.Fields(addressOutput) {
					if _, network, parseErr := net.ParseCIDR(field); parseErr == nil {
						addressPool = network.String()
						break
					}
				}
			}
		}
		if addressPool == "" && interfaceName == a.config.DefaultInterface {
			addressPool = a.config.AddressPool
		}
		endpoint := a.config.InterfaceEndpoints[interfaceName]
		if endpoint == "" && interfaceName == a.config.DefaultInterface {
			endpoint = a.config.PublicEndpoint
		}
		port, _ := strconv.Atoi(strings.TrimSpace(listenPort))
		status := "up"
		if keyErr != nil || portErr != nil {
			status = "error"
		}
		interfaces = append(interfaces, map[string]any{
			"name": interfaceName, "address_pool": addressPool,
			"public_endpoint": endpoint, "public_key": strings.TrimSpace(publicKey),
			"listen_port": port, "status": status,
		})
	}
	return interfaces
}

func (a *Agent) pollJobs(ctx context.Context) error {
	var response APIResponse[[]Job]
	if err := a.request(ctx, http.MethodGet, "/api/node/v1/jobs?limit=20", a.config.NodeToken, nil, &response); err != nil {
		return err
	}
	if !response.Status {
		return errors.New(response.Message)
	}
	for _, job := range response.Data {
		result, err := a.executeJob(ctx, job)
		body := map[string]any{"success": err == nil, "result": result}
		if err != nil {
			body["error"] = err.Error()
		}
		var completed APIResponse[map[string]any]
		if reportErr := a.request(ctx, http.MethodPost, "/api/node/v1/jobs/"+job.JobID+"/result", a.config.NodeToken, body, &completed); reportErr != nil {
			return reportErr
		}
	}
	return nil
}

func (a *Agent) executeJob(ctx context.Context, job Job) (map[string]any, error) {
	switch job.Operation {
	case "CREATE_PEER":
		return a.createPeer(ctx, job.Payload)
	case "ENABLE_PEER":
		return nil, a.enablePeer(ctx, job.Payload)
	case "DISABLE_PEER":
		return nil, a.disablePeer(ctx, job.Payload, false)
	case "DELETE_PEER":
		return nil, a.disablePeer(ctx, job.Payload, true)
	case "APPLY_OUTBOUND":
		return nil, a.applyOutbound(ctx, job.Payload)
	case "REMOVE_OUTBOUND":
		return nil, a.removeOutbound(ctx, job.Payload)
	default:
		return nil, fmt.Errorf("unsupported operation %q", job.Operation)
	}
}

func (a *Agent) createPeer(ctx context.Context, payload map[string]any) (map[string]any, error) {
	peerID := stringValue(payload, "subscription_peer_id")
	publicKey := stringValue(payload, "public_key")
	interfaceName := stringValue(payload, "interface")
	if interfaceName == "" {
		interfaceName = a.config.DefaultInterface
	}
	if peerID == "" || !validWireGuardKey(publicKey) || !interfacePattern.MatchString(interfaceName) {
		return nil, errors.New("invalid CREATE_PEER payload")
	}
	address := stringValue(payload, "address")
	desiredPresharedKey := stringValue(payload, "preshared_key")
	if desiredPresharedKey != "" && !validWireGuardKey(desiredPresharedKey) {
		return nil, errors.New("CREATE_PEER contains an invalid preshared key")
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if existing := a.state.Peers[peerID]; existing != nil {
		if existing.PublicKey != publicKey || existing.Interface != interfaceName ||
			(address != "" && existing.Address != address) ||
			(desiredPresharedKey != "" && existing.PresharedKey != desiredPresharedKey) {
			return nil, errors.New("CREATE_PEER conflicts with existing local peer state")
		}
		// CREATE_PEER is idempotent. Re-apply the peer as well so a restored
		// agent state can repair a WireGuard interface that lost its runtime state.
		if err := setPeer(ctx, existing); err != nil {
			return nil, err
		}
		existing.Enabled = true
		if err := a.saveStateLocked(); err != nil {
			return nil, err
		}
		serverKey, err := command(ctx, "wg", "show", existing.Interface, "public-key")
		if err != nil {
			return nil, err
		}
		return a.peerResult(existing, strings.TrimSpace(serverKey)), nil
	}
	if _, err := command(ctx, "wg", "show", interfaceName); err != nil {
		return nil, fmt.Errorf("WireGuard interface %s is unavailable: %w", interfaceName, err)
	}
	var err error
	if address != "" {
		if err := a.validateDesiredAddress(ctx, peerID, publicKey, interfaceName, address); err != nil {
			return nil, err
		}
	} else {
		address, err = a.allocateAddress(ctx, interfaceName)
		if err != nil {
			return nil, err
		}
	}
	presharedKey := desiredPresharedKey
	if presharedKey == "" && a.config.EnablePreshared {
		presharedKey, err = command(ctx, "wg", "genpsk")
		if err != nil {
			return nil, err
		}
		presharedKey = strings.TrimSpace(presharedKey)
	}
	peer := &PeerState{
		SubscriptionPeerID: peerID, Interface: interfaceName, PublicKey: publicKey,
		Address: address, PresharedKey: presharedKey, Enabled: false,
	}
	if err := setPeer(ctx, peer); err != nil {
		return nil, err
	}
	peer.Enabled = true
	a.state.Peers[peerID] = peer
	if err := a.saveStateLocked(); err != nil {
		return nil, err
	}
	serverKey, err := command(ctx, "wg", "show", interfaceName, "public-key")
	if err != nil {
		return nil, err
	}
	return a.peerResult(peer, strings.TrimSpace(serverKey)), nil
}

func (a *Agent) validateDesiredAddress(ctx context.Context, peerID, publicKey, interfaceName, address string) error {
	addressPool := a.config.InterfacePools[interfaceName]
	if addressPool == "" {
		addressPool = a.config.AddressPool
	}
	ip, _, err := net.ParseCIDR(address)
	if err != nil || ip.To4() == nil {
		return errors.New("requested peer address is not a valid IPv4 CIDR")
	}
	_, network, err := net.ParseCIDR(addressPool)
	if err != nil || !network.Contains(ip) {
		return errors.New("requested peer address is outside the interface pool")
	}
	for existingID, peer := range a.state.Peers {
		if existingID != peerID && peer.Interface == interfaceName && strings.Split(peer.Address, "/")[0] == ip.String() {
			return errors.New("requested peer address is already assigned")
		}
	}
	if output, err := command(ctx, "wg", "show", interfaceName, "allowed-ips"); err == nil {
		for _, line := range strings.Split(output, "\n") {
			fields := strings.Fields(line)
			if len(fields) < 2 || fields[0] == publicKey {
				continue
			}
			for _, allowedIP := range strings.Split(fields[1], ",") {
				if strings.Split(allowedIP, "/")[0] == ip.String() {
					return errors.New("requested peer address is already present on the interface")
				}
			}
		}
	}
	if ip.Equal(network.IP) {
		return errors.New("requested peer address is the network address")
	}
	firstHost := append(net.IP(nil), network.IP.To4()...)
	incrementIP(firstHost)
	if ip.Equal(firstHost) {
		return errors.New("requested peer address is reserved for the WireGuard interface")
	}
	broadcast := make(net.IP, net.IPv4len)
	for index := range broadcast {
		broadcast[index] = network.IP.To4()[index] | ^network.Mask[index]
	}
	if ip.Equal(broadcast) {
		return errors.New("requested peer address is the broadcast address")
	}
	return nil
}

func (a *Agent) peerResult(peer *PeerState, serverKey string) map[string]any {
	endpoint := a.config.InterfaceEndpoints[peer.Interface]
	if endpoint == "" {
		endpoint = a.config.PublicEndpoint
	}
	return map[string]any{
		"address": peer.Address, "server_public_key": serverKey,
		"preshared_key": peer.PresharedKey, "endpoint": endpoint,
	}
}

func (a *Agent) enablePeer(ctx context.Context, payload map[string]any) error {
	peerID := stringValue(payload, "subscription_peer_id")
	a.mu.Lock()
	defer a.mu.Unlock()
	peer := a.state.Peers[peerID]
	if peer == nil {
		return errors.New("peer is not present in local state")
	}
	if peer.Enabled {
		return nil
	}
	if err := setPeer(ctx, peer); err != nil {
		return err
	}
	peer.Enabled = true
	return a.saveStateLocked()
}

func (a *Agent) disablePeer(ctx context.Context, payload map[string]any, deleteState bool) error {
	peerID := stringValue(payload, "subscription_peer_id")
	a.mu.Lock()
	defer a.mu.Unlock()
	peer := a.state.Peers[peerID]
	if peer == nil {
		return nil
	}
	if peer.Enabled {
		if _, err := command(ctx, "wg", "set", peer.Interface, "peer", peer.PublicKey, "remove"); err != nil {
			return err
		}
		if _, err := command(ctx, "wg-quick", "save", peer.Interface); err != nil {
			return err
		}
		peer.Enabled = false
	}
	if deleteState {
		delete(a.state.Peers, peerID)
	}
	return a.saveStateLocked()
}

func setPeer(ctx context.Context, peer *PeerState) error {
	allowedIP, err := hostRoute(peer.Address)
	if err != nil {
		return err
	}
	args := []string{"set", peer.Interface, "peer", peer.PublicKey, "allowed-ips", allowedIP}
	var temporary string
	if peer.PresharedKey != "" {
		file, err := os.CreateTemp("", "wg-node-psk-*")
		if err != nil {
			return err
		}
		temporary = file.Name()
		_ = os.Chmod(temporary, 0o600)
		if _, err := file.WriteString(peer.PresharedKey + "\n"); err != nil {
			file.Close()
			os.Remove(temporary)
			return err
		}
		file.Close()
		defer os.Remove(temporary)
		args = append(args, "preshared-key", temporary)
	}
	if _, err := command(ctx, "wg", args...); err != nil {
		return err
	}
	_, err = command(ctx, "wg-quick", "save", peer.Interface)
	return err
}

func hostRoute(address string) (string, error) {
	ip, _, err := net.ParseCIDR(address)
	if err != nil {
		return "", fmt.Errorf("invalid peer address %q", address)
	}
	if ip.To4() != nil {
		return ip.String() + "/32", nil
	}
	return ip.String() + "/128", nil
}

func (a *Agent) allocateAddress(ctx context.Context, interfaceName string) (string, error) {
	addressPool := a.config.InterfacePools[interfaceName]
	if addressPool == "" {
		addressPool = a.config.AddressPool
	}
	_, network, err := net.ParseCIDR(addressPool)
	if err != nil || network.IP.To4() == nil {
		return "", errors.New("only an IPv4 WG_NODE_ADDRESS_POOL is currently supported")
	}
	used := map[string]bool{}
	for _, peer := range a.state.Peers {
		used[strings.Split(peer.Address, "/")[0]] = true
	}
	if output, err := command(ctx, "wg", "show", interfaceName, "allowed-ips"); err == nil {
		for _, line := range strings.Split(output, "\n") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				for _, address := range strings.Split(fields[1], ",") {
					used[strings.Split(address, "/")[0]] = true
				}
			}
		}
	}
	base := network.IP.To4()
	broadcast := make(net.IP, net.IPv4len)
	for index := range broadcast {
		broadcast[index] = base[index] | ^network.Mask[index]
	}
	ip := append(net.IP(nil), base...)
	for index := 0; index < 65536; index++ {
		incrementIP(ip)
		if !network.Contains(ip) {
			break
		}
		// Reserve the first host for the WireGuard interface and never allocate
		// the subnet broadcast address (including non-/24 address pools).
		if index == 0 || ip.Equal(broadcast) || used[ip.String()] {
			continue
		}
		ones, _ := network.Mask.Size()
		return fmt.Sprintf("%s/%d", ip.String(), ones), nil
	}
	return "", errors.New("address pool is exhausted")
}

func (a *Agent) applyOutbound(ctx context.Context, payload map[string]any) error {
	outboundID := stringValue(payload, "outbound_id")
	interfaceName := stringValue(payload, "interface")
	sourceInterface := stringValue(payload, "source_interface")
	sourcePool := stringValue(payload, "source_address_pool")
	configuration := stringValue(payload, "configuration")
	if outboundID == "" || !interfacePattern.MatchString(interfaceName) ||
		!interfacePattern.MatchString(sourceInterface) || interfaceName == sourceInterface {
		return errors.New("invalid outbound payload")
	}
	if _, network, err := net.ParseCIDR(sourcePool); err != nil || network.IP.To4() == nil {
		return errors.New("outbound source_address_pool must be a valid IPv4 network")
	}
	if _, err := command(ctx, "wg", "show", sourceInterface); err != nil {
		return fmt.Errorf("source interface %s is unavailable: %w", sourceInterface, err)
	}
	sanitized, err := sanitizeOutboundConfig(configuration)
	if err != nil {
		return err
	}

	a.mu.Lock()
	defer a.mu.Unlock()
	table := routingTable(interfaceName)
	for existingID, existing := range a.state.Outbounds {
		if existingID != outboundID && existing != nil && existing.RoutingTable == table {
			return fmt.Errorf(
				"outbound routing table collision between %s and %s; choose another interface name",
				interfaceName, existing.Interface,
			)
		}
	}
	if previous := a.state.Outbounds[outboundID]; previous != nil {
		a.cleanupOutbound(ctx, previous)
	}
	if err := os.MkdirAll(a.config.OutboundDirectory, 0o700); err != nil {
		return err
	}
	path := filepath.Join(a.config.OutboundDirectory, interfaceName+".conf")
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, []byte(sanitized), 0o600); err != nil {
		return err
	}
	if err := os.Rename(temporary, path); err != nil {
		return err
	}
	_, _ = command(ctx, "wg-quick", "down", path)
	if _, err := command(ctx, "wg-quick", "up", path); err != nil {
		return err
	}
	state := &OutboundState{
		OutboundID: outboundID, Interface: interfaceName, SourceInterface: sourceInterface,
		SourceAddressPool: sourcePool, RoutingTable: table,
	}
	if err := configureOutboundRouting(ctx, state); err != nil {
		a.cleanupOutbound(ctx, state)
		return err
	}
	a.state.Outbounds[outboundID] = state
	return a.saveStateLocked()
}

func (a *Agent) removeOutbound(ctx context.Context, payload map[string]any) error {
	outboundID := stringValue(payload, "outbound_id")
	if outboundID == "" {
		return errors.New("outbound_id is required")
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	state := a.state.Outbounds[outboundID]
	if state == nil {
		interfaceName := stringValue(payload, "interface")
		if !interfacePattern.MatchString(interfaceName) {
			return nil
		}
		state = &OutboundState{
			OutboundID: outboundID, Interface: interfaceName,
			SourceInterface:   stringValue(payload, "source_interface"),
			SourceAddressPool: stringValue(payload, "source_address_pool"),
			RoutingTable:      routingTable(interfaceName),
		}
	}
	a.cleanupOutbound(ctx, state)
	delete(a.state.Outbounds, outboundID)
	return a.saveStateLocked()
}

func (a *Agent) cleanupOutbound(ctx context.Context, state *OutboundState) {
	if state == nil || !interfacePattern.MatchString(state.Interface) {
		return
	}
	if state.RoutingTable < 1 {
		state.RoutingTable = routingTable(state.Interface)
	}
	if state.SourceAddressPool != "" {
		_, _ = command(ctx, "ip", "rule", "del", "from", state.SourceAddressPool, "table", strconv.Itoa(state.RoutingTable), "priority", strconv.Itoa(state.RoutingTable))
	}
	_, _ = command(ctx, "ip", "route", "flush", "table", strconv.Itoa(state.RoutingTable))
	if state.SourceAddressPool != "" {
		deleteIptablesRule(ctx, "nat", "POSTROUTING", "-s", state.SourceAddressPool, "-o", state.Interface, "-j", "MASQUERADE")
		if interfacePattern.MatchString(state.SourceInterface) {
			deleteIptablesRule(ctx, "filter", "FORWARD", "-i", state.SourceInterface, "-o", state.Interface, "-s", state.SourceAddressPool, "-j", "ACCEPT")
			deleteIptablesRule(ctx, "filter", "FORWARD", "-i", state.Interface, "-o", state.SourceInterface, "-d", state.SourceAddressPool, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
		}
	}
	path := filepath.Join(a.config.OutboundDirectory, state.Interface+".conf")
	_, _ = command(ctx, "wg-quick", "down", path)
	_ = os.Remove(path)
}

func configureOutboundRouting(ctx context.Context, state *OutboundState) error {
	table := strconv.Itoa(state.RoutingTable)
	_, _ = command(ctx, "ip", "rule", "del", "from", state.SourceAddressPool, "table", table, "priority", table)
	if _, err := command(ctx, "ip", "route", "replace", "default", "dev", state.Interface, "table", table); err != nil {
		return err
	}
	if _, err := command(ctx, "ip", "rule", "add", "from", state.SourceAddressPool, "table", table, "priority", table); err != nil {
		return err
	}
	if err := ensureIptablesRule(ctx, "nat", "POSTROUTING", "-s", state.SourceAddressPool, "-o", state.Interface, "-j", "MASQUERADE"); err != nil {
		return err
	}
	if err := ensureIptablesRule(ctx, "filter", "FORWARD", "-i", state.SourceInterface, "-o", state.Interface, "-s", state.SourceAddressPool, "-j", "ACCEPT"); err != nil {
		return err
	}
	return ensureIptablesRule(ctx, "filter", "FORWARD", "-i", state.Interface, "-o", state.SourceInterface, "-d", state.SourceAddressPool, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
}

func sanitizeOutboundConfig(configuration string) (string, error) {
	if len(configuration) == 0 || len(configuration) > 64*1024 {
		return "", errors.New("outbound WireGuard configuration is empty or too large")
	}
	blocked := map[string]bool{
		"preup": true, "postup": true, "predown": true, "postdown": true,
		"saveconfig": true, "table": true, "dns": true,
	}
	lines := strings.Split(strings.ReplaceAll(configuration, "\r\n", "\n"), "\n")
	clean := make([]string, 0, len(lines)+1)
	interfaceSeen, peerSeen, privateKeySeen := false, false, false
	publicKeySeen, endpointSeen, defaultRouteSeen := false, false, false
	section := ""
	for _, rawLine := range lines {
		line := strings.TrimSpace(rawLine)
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.ToLower(strings.TrimSpace(strings.Trim(line, "[]")))
			if section != "interface" && section != "peer" {
				return "", fmt.Errorf("unsupported WireGuard section %q", section)
			}
			interfaceSeen = interfaceSeen || section == "interface"
			peerSeen = peerSeen || section == "peer"
			clean = append(clean, rawLine)
			continue
		}
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			clean = append(clean, rawLine)
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			return "", fmt.Errorf("invalid WireGuard configuration line %q", line)
		}
		key := strings.ToLower(strings.TrimSpace(parts[0]))
		value := strings.TrimSpace(parts[1])
		if blocked[key] {
			continue
		}
		if section == "interface" && key == "privatekey" && validWireGuardKey(value) {
			privateKeySeen = true
		}
		if section == "peer" && key == "publickey" && validWireGuardKey(value) {
			publicKeySeen = true
		}
		if section == "peer" && key == "endpoint" && value != "" {
			endpointSeen = true
		}
		if section == "peer" && key == "allowedips" {
			for _, cidr := range strings.Split(value, ",") {
				if strings.TrimSpace(cidr) == "0.0.0.0/0" {
					defaultRouteSeen = true
				}
			}
		}
		clean = append(clean, rawLine)
	}
	if !interfaceSeen || !peerSeen || !privateKeySeen || !publicKeySeen || !endpointSeen || !defaultRouteSeen {
		return "", errors.New("outbound config requires Interface/PrivateKey and Peer/PublicKey/Endpoint/AllowedIPs=0.0.0.0/0")
	}
	result := make([]string, 0, len(clean)+1)
	inserted := false
	for _, line := range clean {
		if !inserted && strings.EqualFold(strings.TrimSpace(line), "[Interface]") {
			result = append(result, line, "Table = off")
			inserted = true
			continue
		}
		result = append(result, line)
	}
	return strings.TrimSpace(strings.Join(result, "\n")) + "\n", nil
}

func routingTable(interfaceName string) int {
	hash := fnv.New32a()
	_, _ = hash.Write([]byte(interfaceName))
	return 20000 + int(hash.Sum32()%10000)
}

func ensureIptablesRule(ctx context.Context, table, chain string, args ...string) error {
	check := append([]string{"-t", table, "-C", chain}, args...)
	if _, err := command(ctx, "iptables", check...); err == nil {
		return nil
	}
	add := append([]string{"-t", table, "-A", chain}, args...)
	_, err := command(ctx, "iptables", add...)
	return err
}

func deleteIptablesRule(ctx context.Context, table, chain string, args ...string) {
	remove := append([]string{"-t", table, "-D", chain}, args...)
	_, _ = command(ctx, "iptables", remove...)
}

func (a *Agent) reportTraffic(ctx context.Context) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.state.Sequence++
	samples := make([]map[string]any, 0, len(a.state.Peers))
	byInterface := map[string]map[string][2]int64{}
	for _, peer := range a.state.Peers {
		if !peer.Enabled {
			continue
		}
		if _, ok := byInterface[peer.Interface]; ok {
			continue
		}
		output, err := command(ctx, "wg", "show", peer.Interface, "transfer")
		if err != nil {
			continue
		}
		values := map[string][2]int64{}
		for _, line := range strings.Split(output, "\n") {
			fields := strings.Fields(line)
			if len(fields) != 3 {
				continue
			}
			rx, err1 := strconv.ParseInt(fields[1], 10, 64)
			tx, err2 := strconv.ParseInt(fields[2], 10, 64)
			if err1 == nil && err2 == nil {
				values[fields[0]] = [2]int64{rx, tx}
			}
		}
		byInterface[peer.Interface] = values
	}
	for _, peer := range a.state.Peers {
		if !peer.Enabled {
			continue
		}
		transfer, ok := byInterface[peer.Interface][peer.PublicKey]
		if !ok {
			continue
		}
		samples = append(samples, map[string]any{
			"subscription_peer_id": peer.SubscriptionPeerID,
			"session_id":           a.state.SessionID, "sequence": a.state.Sequence,
			"rx_bytes": transfer[0], "tx_bytes": transfer[1],
		})
	}
	if err := a.saveStateLocked(); err != nil {
		return err
	}
	if len(samples) == 0 {
		return nil
	}
	for start := 0; start < len(samples); start += 500 {
		end := min(start+500, len(samples))
		var response APIResponse[map[string]any]
		if err := a.request(ctx, http.MethodPost, "/api/node/v1/traffic", a.config.NodeToken, map[string]any{"samples": samples[start:end]}, &response); err != nil {
			return err
		}
	}
	return nil
}

func (a *Agent) request(ctx context.Context, method, path, token string, body any, destination any) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, a.config.PanelURL+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	response, err := a.client.Do(req)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("panel returned HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(data)))
	}
	if destination != nil && len(data) > 0 {
		if err := json.Unmarshal(data, destination); err != nil {
			return err
		}
	}
	return nil
}

func command(ctx context.Context, name string, args ...string) (string, error) {
	commandContext, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(commandContext, name, args...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("%s failed: %s", name, strings.TrimSpace(string(output)))
	}
	return string(output), nil
}

func validWireGuardKey(value string) bool {
	decoded, err := base64.StdEncoding.DecodeString(value)
	return err == nil && len(decoded) == 32
}

func validEndpoint(value string) bool {
	host, portValue, err := net.SplitHostPort(value)
	if err != nil || strings.TrimSpace(host) == "" || strings.ContainsAny(value, "\r\n\t ") {
		return false
	}
	port, err := strconv.Atoi(portValue)
	return err == nil && port >= 1 && port <= 65535
}

func stringValue(payload map[string]any, key string) string {
	value, _ := payload[key].(string)
	return value
}

func incrementIP(ip net.IP) {
	for index := len(ip) - 1; index >= 0; index-- {
		ip[index]++
		if ip[index] != 0 {
			break
		}
	}
}

func randomID() string {
	return fmt.Sprintf("%d-%d", time.Now().UnixNano(), os.Getpid())
}

func hostname() string {
	value, err := os.Hostname()
	if err != nil {
		return "wg-node"
	}
	return value
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func first(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}
