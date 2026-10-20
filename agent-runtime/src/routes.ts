// The sidecar's route table. server/assistant/adapters/durable.py carries the
// same tuple, and scripts/check_protocol.py compares the two so a rename on
// either side fails the gate instead of silently 404ing.
export const SIDECAR_ROUTES = [
	"/healthz",
	"/v1/stats",
	"/v1/runs",
	"/v1/runs/{id}",
	"/v1/runs/{id}/cancel",
	"/v1/events",
	"/v1/approvals",
	"/v1/lookup",
	"/v1/shutdown",
] as const;
