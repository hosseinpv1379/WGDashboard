FROM golang:1.26-alpine3.23 AS builder
WORKDIR /src
COPY go.mod ./
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/wg-node ./main.go

FROM alpine:3.23
RUN apk add --no-cache ca-certificates iproute2 wireguard-tools
COPY --from=builder /out/wg-node /usr/local/bin/wg-node
RUN mkdir -p /var/lib/wg-node && chmod 700 /var/lib/wg-node
ENTRYPOINT ["/usr/local/bin/wg-node"]
