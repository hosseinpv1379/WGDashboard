FROM golang:1.26-alpine3.23 AS builder
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN go test ./... \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/wg-node .

FROM alpine:3.23
ARG VCS_URL="local"
LABEL org.opencontainers.image.title="WGDashboard Node"
LABEL org.opencontainers.image.description="WireGuard data-plane agent for WGDashboard"
LABEL org.opencontainers.image.source="${VCS_URL}"
RUN apk add --no-cache ca-certificates iproute2 iptables wireguard-tools
COPY --from=builder /out/wg-node /usr/local/bin/wg-node
RUN mkdir -p /etc/wg-node/outbounds /var/lib/wg-node \
    && chmod 700 /etc/wg-node /etc/wg-node/outbounds /var/lib/wg-node
ENTRYPOINT ["/usr/local/bin/wg-node"]
