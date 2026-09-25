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

const version = "0.1.0"

type Config struct {
	PanelURL             string
	NodeID               string
	NodeToken            string
	BootstrapToken       string
	NodeName             string
	Region               string
	PublicEndpoint       string
	AddressPool          string
	DefaultInterface     string
	Capacity             int
	PollInterval         time.Duration
	ReportInterval       time.Duration
	StatePath            string
	CredentialsPath      string
	CACertificatePath    string
	ClientCertificatePath string
	ClientKeyPath          string
	InsecureTLS            bool
	EnablePreshared        bool
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

type PersistentState struct {
	SessionID string                `json:"session_id"`
	Sequence  int64                 `json:"sequence"`
	Peers     map[string]*PeerState `json:"peers"`
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
	capacity, _ := strconv.Atoi(env("WG_NODE_CAPACITY", "0"))
	pollSeconds, _ := strconv.Atoi(env("WG_NODE_POLL_SECONDS", "5"))
	reportSeconds, _ := strconv.Atoi(env("WG_NODE_REPORT_SECONDS", "15"))
	config := Config{
		PanelURL:             strings.TrimRight(os.Getenv("WG_PANEL_URL"), "/"),
		NodeID:               os.Getenv("WG_NODE_ID"),
		NodeToken:            os.Getenv("WG_NODE_TOKEN"),
		BootstrapToken:       os.Getenv("WG_NODE_BOOTSTRAP_TOKEN"),
		NodeName:             env("WG_NODE_NAME", hostname()),
		Region:               os.Getenv("WG_NODE_REGION"),
		PublicEndpoint:       os.Getenv("WG_NODE_PUBLIC_ENDPOINT"),
		AddressPool:          env("WG_NODE_ADDRESS_POOL", "10.88.0.0/24"),
		DefaultInterface:     env("WG_NODE_INTERFACE", "wg0"),
		Capacity:             capacity,
		PollInterval:         time.Duration(max(1, pollSeconds)) * time.Second,
		ReportInterval:       time.Duration(max(5, reportSeconds)) * time.Second,
		StatePath:            env("WG_NODE_STATE", "/var/lib/wg-node/state.json"),
		CredentialsPath:      env("WG_NODE_CREDENTIALS", "/var/lib/wg-node/credentials.json"),
		CACertificatePath:    os.Getenv("WG_NODE_CA_CERT"),
		ClientCertificatePath: os.Getenv("WG_NODE_CLIENT_CERT"),
		ClientKeyPath:          os.Getenv("WG_NODE_CLIENT_KEY"),
		InsecureTLS:            strings.EqualFold(os.Getenv("WG_NODE_INSECURE_TLS"), "true"),
		EnablePreshared:        !strings.EqualFold(os.Getenv("WG_NODE_PRESHARED_KEY"), "false"),
	}
	if config.PanelURL == "" {
		return Config{}, errors.New("WG_PANEL_URL is required")
	}
	if config.PublicEndpoint == "" {
		return Config{}, errors.New("WG_NODE_PUBLIC_ENDPOINT is required, for example vpn.example.com:51820")
	}
	if !interfacePattern.MatchString(config.DefaultInterface) {
		return Config{}, errors.New("WG_NODE_INTERFACE is invalid")
	}
	if _, _, err := net.ParseCIDR(config.AddressPool); err != nil {
		return Config{}, fmt.Errorf("invalid WG_NODE_ADDRESS_POOL: %w", err)
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
			TLSClientConfig:     tlsConfig,
			MaxIdleConns:        10,
			IdleConnTimeout:     30 * time.Second,
			DisableCompression:  false,
		},
	}, nil
}

func (a *Agent) loadState() error {
	a.state = PersistentState{SessionID: randomID(), Peers: map[string]*PeerState{}}
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

func (a *Agent) heartbeat(ctx context.Context) error {
	payload := map[string]any{
		"agent_version": version, "public_endpoint": a.config.PublicEndpoint,
		"capacity": a.config.Capacity,
	}
	var response APIResponse[map[string]any]
	return a.request(ctx, http.MethodPost, "/api/node/v1/heartbeat", a.config.NodeToken, payload, &response)
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
	a.mu.Lock()
	defer a.mu.Unlock()
	if existing := a.state.Peers[peerID]; existing != nil {
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
	address, err := a.allocateAddress(ctx, interfaceName)
	if err != nil {
		return nil, err
	}
	presharedKey := ""
	if a.config.EnablePreshared {
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

func (a *Agent) peerResult(peer *PeerState, serverKey string) map[string]any {
	return map[string]any{
		"address": peer.Address, "server_public_key": serverKey,
		"preshared_key": peer.PresharedKey, "endpoint": a.config.PublicEndpoint,
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
	_, network, err := net.ParseCIDR(a.config.AddressPool)
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
			"session_id": a.state.SessionID, "sequence": a.state.Sequence,
			"rx_bytes": transfer[0], "tx_bytes": transfer[1],
		})
	}
	if err := a.saveStateLocked(); err != nil {
		return err
	}
	if len(samples) == 0 {
		return nil
	}
	var response APIResponse[map[string]any]
	return a.request(ctx, http.MethodPost, "/api/node/v1/traffic", a.config.NodeToken, map[string]any{"samples": samples}, &response)
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
