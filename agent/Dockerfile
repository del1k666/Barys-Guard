# Образ агента для стенда. Версия Go совпадает с agent/go.mod.
FROM golang:1.23-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .

ARG AGENT_VERSION=0.1.0-stand
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath \
      -ldflags "-s -w -X main.agentVersion=${AGENT_VERSION}" \
      -o /out/barysguard-agent ./cmd/barysguard-agent

FROM alpine:3.20

# /etc/machine-id заполняется входом контейнера под обычным пользователем,
# поэтому файл создаётся заранее и принадлежит ему.
RUN adduser -D -u 10001 agent \
    && mkdir -p /var/lib/agent \
    && chown agent:agent /var/lib/agent \
    && touch /etc/machine-id \
    && chown agent:agent /etc/machine-id

COPY --from=build /out/barysguard-agent /usr/local/bin/barysguard-agent
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER agent
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
