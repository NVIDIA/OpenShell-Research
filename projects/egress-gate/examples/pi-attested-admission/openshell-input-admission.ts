/**
 * OpenShell direct-input admission for Pi.
 *
 * Load this extension explicitly with Pi's standard --extension option. It
 * admits one idle, text-only user submission after rendering and before Pi
 * persists it, then attaches the returned receipt to the first provider
 * request. Steering, follow-ups, images, compaction, and post-tool
 * continuations are unsupported and fail closed.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const BRIDGE_URL_ENV = "OPENSHELL_PI_CONVERSATION_URL";
const RECEIPT_HEADER = "x-openshell-middleware-egress-receipt";
const SCHEMA_VERSION = "openshell.pi-input.v1";
const MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_RECEIPT_BYTES = 8 * 1024;

interface BridgeResponse {
	decision: "allow" | "deny";
	replacement_body?: number[];
	receipt?: number[];
	reason_code?: string;
}

interface CandidateEnvelope {
	schema_version: typeof SCHEMA_VERSION;
	text: string;
}

export default function (pi: ExtensionAPI) {
	let pendingReceipt: string | undefined;

	pi.on("before_user_message_append", async (event, ctx) => {
		try {
			pendingReceipt = undefined;
			if (!ctx.isIdle() || event.images?.length) {
				notifySafely(ctx, "OpenShell admission currently supports only idle, text-only prompts");
				return { action: "cancel" };
			}
			const bridgeUrl = process.env[BRIDGE_URL_ENV];
			if (!bridgeUrl) throw new Error(`${BRIDGE_URL_ENV} is required for OpenShell admission`);
			const envelope: CandidateEnvelope = { schema_version: SCHEMA_VERSION, text: event.text };
			const requestBody = new TextEncoder().encode(JSON.stringify(envelope));
			const response = await fetch(bridgeUrl, {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify({
					harness_version: "extension-v1",
					session_id: ctx.sessionManager.getSessionId(),
					submission_id: crypto.randomUUID(),
					request_body: Array.from(requestBody),
				}),
				signal: ctx.signal,
			});
			if (!response.ok) throw new Error("OpenShell admission is unavailable");
			const encoded = new Uint8Array(await response.arrayBuffer());
			if (encoded.byteLength > MAX_RESPONSE_BYTES) throw new Error("OpenShell admission response is too large");
			const result = parseBridgeResponse(JSON.parse(new TextDecoder().decode(encoded)));
			if (result.decision === "deny") {
				notifySafely(ctx, `OpenShell denied the prompt (${result.reason_code ?? "policy_denied"})`);
				return { action: "cancel" };
			}

			pendingReceipt = decodeReceipt(result.receipt);
			if (!result.replacement_body) return;
			const replacement = parseEnvelope(new Uint8Array(result.replacement_body));
			return { action: "transform", text: replacement.text };
		} catch {
			pendingReceipt = undefined;
			notifySafely(ctx, "OpenShell admission is unavailable");
			return { action: "cancel" };
		}
	});

	pi.on("before_provider_headers", (event) => {
		if (!pendingReceipt) throw new Error("OpenShell candidate admission receipt is missing");
		if (Object.keys(event.headers).some((name) => name.toLowerCase() === RECEIPT_HEADER)) {
			throw new Error("OpenShell receipt header is reserved");
		}
		event.headers[RECEIPT_HEADER] = pendingReceipt;
		pendingReceipt = undefined;
	});
}

function notifySafely(ctx: ExtensionContext, message: string): void {
	try {
		ctx.ui.notify(message, "warning");
	} catch {
		// Admission remains fail closed when a UI implementation cannot notify.
	}
}

function parseBridgeResponse(value: unknown): BridgeResponse {
	if (!isRecord(value) || (value.decision !== "allow" && value.decision !== "deny")) {
		throw new Error("OpenShell admission returned an invalid response");
	}
	if (value.decision === "deny") {
		if (value.receipt !== undefined || value.replacement_body !== undefined) {
			throw new Error("OpenShell admission returned an invalid denial");
		}
		return {
			decision: "deny",
			reason_code: typeof value.reason_code === "string" ? value.reason_code : undefined,
		};
	}
	if (!isByteArray(value.receipt) || (value.replacement_body !== undefined && !isByteArray(value.replacement_body))) {
		throw new Error("OpenShell admission returned an invalid allow response");
	}
	return { decision: "allow", receipt: value.receipt, replacement_body: value.replacement_body };
}

function parseEnvelope(body: Uint8Array): CandidateEnvelope {
	const value: unknown = JSON.parse(new TextDecoder().decode(body));
	if (!isRecord(value) || value.schema_version !== SCHEMA_VERSION || typeof value.text !== "string") {
		throw new Error("OpenShell admission returned an invalid replacement");
	}
	return { schema_version: SCHEMA_VERSION, text: value.text };
}

function decodeReceipt(value: number[] | undefined): string {
	if (!value || value.length === 0 || value.length > MAX_RECEIPT_BYTES) {
		throw new Error("OpenShell admission receipt is invalid");
	}
	const receipt = new TextDecoder("ascii", { fatal: true }).decode(new Uint8Array(value));
	if (!/^[\x21-\x7e]+$/.test(receipt)) throw new Error("OpenShell admission receipt is invalid");
	return receipt;
}

function isByteArray(value: unknown): value is number[] {
	return Array.isArray(value) && value.every((byte) => Number.isInteger(byte) && byte >= 0 && byte <= 255);
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object";
}
