# OpenShell exporter module

Keep this Go module self-contained. Use Go commands directly; do not add wrapper
scripts. Run `go mod verify`, `go vet ./...`, `go test -race ./...`, and
`go build ./cmd/...` after substantive changes. Test documented configurations.
Preserve stable telemetry scope names and calculate identity before redaction.
Keep separate persistent checkpoints, queues and recovery with one writer.
Receiver/TLS/durability behavior changes require relevant live qualification.
Do not publish tags or claim remote installation works until the module is public.
