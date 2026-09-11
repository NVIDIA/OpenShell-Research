// Pi Subagents rejects unknown handle fields. Keep operator-only diagnostics
// available through the service API without passing them into that protocol.
export function toPiJobResponse(response: Record<string, unknown>): Record<string, unknown> {
  const result = { ...response };
  delete result.cleanupError;
  delete result.sandboxName;
  return result;
}
