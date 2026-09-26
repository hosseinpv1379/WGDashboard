package main

import (
	"strings"
	"testing"
)

const validOutboundConfiguration = `[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 172.16.0.2/32
DNS = 1.1.1.1
Table = auto
PostUp = touch /tmp/must-not-run

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
Endpoint = upstream.example.com:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
`

func TestSanitizeOutboundConfigRemovesOperatorCommands(t *testing.T) {
	configuration, err := sanitizeOutboundConfig(validOutboundConfiguration)
	if err != nil {
		t.Fatalf("sanitize outbound: %v", err)
	}
	for _, blocked := range []string{"PostUp", "touch /tmp", "DNS =", "Table = auto"} {
		if strings.Contains(configuration, blocked) {
			t.Fatalf("blocked directive %q remains in configuration", blocked)
		}
	}
	if !strings.Contains(configuration, "Table = off") {
		t.Fatal("agent-managed Table = off was not inserted")
	}
	if !strings.Contains(configuration, "PersistentKeepalive = 25") {
		t.Fatal("safe peer option was unexpectedly removed")
	}
}

func TestSanitizeOutboundConfigRequiresIPv4DefaultRoute(t *testing.T) {
	configuration := strings.Replace(validOutboundConfiguration, "0.0.0.0/0", "10.0.0.0/8", 1)
	if _, err := sanitizeOutboundConfig(configuration); err == nil {
		t.Fatal("configuration without the IPv4 default route was accepted")
	}
}

func TestRoutingTableIsStableAndInReservedRange(t *testing.T) {
	firstTable := routingTable("wgo0")
	if firstTable != routingTable("wgo0") {
		t.Fatal("routing table is not stable")
	}
	if firstTable < 20000 || firstTable > 29999 {
		t.Fatalf("routing table %d is outside the agent range", firstTable)
	}
}

func TestHostRoute(t *testing.T) {
	route, err := hostRoute("10.88.0.2/24")
	if err != nil || route != "10.88.0.2/32" {
		t.Fatalf("unexpected IPv4 host route %q: %v", route, err)
	}
}

func TestValidEndpoint(t *testing.T) {
	for _, endpoint := range []string{"vpn.example.com:51820", "91.107.252.38:51820", "[2001:db8::1]:51820"} {
		if !validEndpoint(endpoint) {
			t.Fatalf("valid endpoint %q was rejected", endpoint)
		}
	}
	for _, endpoint := range []string{"vpn.example.com", "vpn.example.com:0", "vpn.example.com:70000", "vpn.example.com:51820\nPostUp=x"} {
		if validEndpoint(endpoint) {
			t.Fatalf("invalid endpoint %q was accepted", endpoint)
		}
	}
}
