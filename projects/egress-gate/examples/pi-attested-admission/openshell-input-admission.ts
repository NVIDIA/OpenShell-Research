/**
 * OpenShell direct-input admission for Pi.
 *
 * Load this extension explicitly with Pi's standard --extension option. It
 * admits each text-only user submission after rendering and before Pi
 * persists it. Every provider request in the admitted turn receives a fresh
 * receipt, including automatic continuations after tool calls.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const BRIDGE_URL_ENV = "OPENSHELL_AGENT_CONVERSATION_URL";
const LEGACY_BRIDGE_URL_ENV = "OPENSHELL_PI_CONVERSATION_URL";
const RECEIPT_HEADER = "x-openshell-middleware-egress-receipt";
const SCHEMA_VERSION = "openshell.pi-input.v1";
const MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_RECEIPT_BYTES = 8 * 1024;
const REDACTED_CREDENTIAL = "[REDACTED_MODEL_CREDENTIAL]";
const CREDENTIAL_ENV_NAMES = ["MODEL_PROVIDER_API_KEY", "PI_MODEL_API_KEY"];

type BridgeResponse =
	| { decision: "allow"; replacement_body?: number[]; receipt: number[] }
	| { decision: "deny"; reason_code?: string };

interface CandidateEnvelope {
	schema_version: typeof SCHEMA_VERSION;
	text: string;
}

interface ActiveAdmission {
	bridgeUrl: string;
	sessionId: string;
	envelope: CandidateEnvelope;
}

interface PendingAdmission extends ActiveAdmission {
	receipt: string;
}

interface AdmissionResult {
	envelope: CandidateEnvelope;
	receipt: string;
}

export default function (pi: ExtensionAPI) {
	let activeAdmission: ActiveAdmission | undefined;
	let pendingReceipt: string | undefined;
	const queuedAdmissions: PendingAdmission[] = [];
	const credentialValues = CREDENTIAL_ENV_NAMES.map((name) => process.env[name]).filter(
		(value): value is string => typeof value === "string" && value.length >= 12,
	);

	pi.on("tool_result", (event) => {
		let changed = false;
		const content = event.content.map((part) => {
			if (part.type !== "text") return part;
			let text = part.text;
			for (const credential of credentialValues) {
				const redacted = text.replaceAll(credential, REDACTED_CREDENTIAL);
				changed ||= redacted !== text;
				text = redacted;
			}
			return text === part.text ? part : { ...part, text };
		});
		return changed ? { content } : undefined;
	});

	pi.on("before_user_message_append", async (event, ctx) => {
		const isIdle = ctx.isIdle();
		try {
			if (isIdle) {
				activeAdmission = undefined;
				pendingReceipt = undefined;
			}
			if (event.images?.length) {
				notifySafely(ctx, "OpenShell admission currently supports only text prompts");
				return { action: "cancel" };
			}
			const bridgeUrl = process.env[BRIDGE_URL_ENV] ?? process.env[LEGACY_BRIDGE_URL_ENV];
			if (!bridgeUrl) throw new Error(`${BRIDGE_URL_ENV} is required for OpenShell admission`);
			const envelope: CandidateEnvelope = { schema_version: SCHEMA_VERSION, text: event.text };
			const sessionId = ctx.sessionManager.getSessionId();
			const result = await requestAdmission(bridgeUrl, sessionId, envelope, ctx.signal);
			if (result.response.decision === "deny") {
				notifySafely(ctx, `OpenShell denied the prompt (${result.response.reason_code ?? "policy_denied"})`);
				return { action: "cancel" };
			}

			const admission = { bridgeUrl, sessionId, ...result.admission };
			if (isIdle) {
				activeAdmission = admission;
				pendingReceipt = admission.receipt;
			} else {
				queuedAdmissions.push(admission);
			}
			if (result.admission.envelope.text === event.text) return;
			return { action: "transform", text: result.admission.envelope.text };
		} catch {
			if (isIdle) {
				activeAdmission = undefined;
				pendingReceipt = undefined;
			}
			notifySafely(ctx, "OpenShell admission is unavailable");
			return { action: "cancel" };
		}
	});

	pi.on("message_start", (event) => {
		const text = userMessageText(event.message);
		if (text === undefined) return;
		const index = queuedAdmissions.findIndex((admission) => admission.envelope.text === text);
		if (index === -1) return;
		const [admission] = queuedAdmissions.splice(index, 1);
		activeAdmission = admission;
		pendingReceipt = admission.receipt;
	});

	pi.on("before_provider_headers", async (event, ctx) => {
		if (Object.keys(event.headers).some((name) => name.toLowerCase() === RECEIPT_HEADER)) {
			throw new Error("OpenShell receipt header is reserved");
		}
		if (!activeAdmission) throw new Error("OpenShell candidate admission context is missing");

		let receipt = pendingReceipt;
		if (!receipt) {
			const result = await requestAdmission(
				activeAdmission.bridgeUrl,
				activeAdmission.sessionId,
				activeAdmission.envelope,
				ctx.signal,
			);
			if (result.response.decision === "deny") {
				throw new Error(`OpenShell denied the active prompt (${result.response.reason_code ?? "policy_denied"})`);
			}
			if (result.admission.envelope.text !== activeAdmission.envelope.text) {
				throw new Error("OpenShell changed a prompt after Pi persisted it");
			}
			receipt = result.admission.receipt;
		}

		event.headers[RECEIPT_HEADER] = receipt;
		pendingReceipt = undefined;
	});
}

async function requestAdmission(
	bridgeUrl: string,
	sessionId: string,
	envelope: CandidateEnvelope,
	signal: AbortSignal,
): Promise<
	| { response: Extract<BridgeResponse, { decision: "deny" }>; admission?: never }
	| { response: Extract<BridgeResponse, { decision: "allow" }>; admission: AdmissionResult }
> {
	const requestBody = new TextEncoder().encode(JSON.stringify(envelope));
	const response = await fetch(bridgeUrl, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify({
			harness_version: "extension-v1",
			session_id: sessionId,
			submission_id: crypto.randomUUID(),
			request_body: Array.from(requestBody),
		}),
		signal,
	});
	if (!response.ok) throw new Error("OpenShell admission is unavailable");
	const encoded = new Uint8Array(await response.arrayBuffer());
	if (encoded.byteLength > MAX_RESPONSE_BYTES) throw new Error("OpenShell admission response is too large");
	const result = parseBridgeResponse(JSON.parse(new TextDecoder().decode(encoded)));
	if (result.decision === "deny") return { response: result };

	return {
		response: result,
		admission: {
			receipt: decodeReceipt(result.receipt),
			envelope: result.replacement_body ? parseEnvelope(new Uint8Array(result.replacement_body)) : envelope,
		},
	};
}

function userMessageText(message: unknown): string | undefined {
	if (!isRecord(message) || message.role !== "user" || !Array.isArray(message.content)) return undefined;
	const text = message.content
		.filter(
			(part): part is { type: "text"; text: string } =>
				isRecord(part) && part.type === "text" && typeof part.text === "string",
		)
		.map((part) => part.text)
		.join("\n");
	return text || undefined;
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
	const receipt = value.receipt;
	const replacementBody = value.replacement_body;
	if (!isByteArray(receipt)) {
		throw new Error("OpenShell admission returned an invalid allow response");
	}
	let replacement: number[] | undefined;
	if (replacementBody !== undefined) {
		if (!isByteArray(replacementBody)) {
			throw new Error("OpenShell admission returned an invalid allow response");
		}
		replacement = replacementBody;
	}
	return { decision: "allow", receipt, replacement_body: replacement };
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
