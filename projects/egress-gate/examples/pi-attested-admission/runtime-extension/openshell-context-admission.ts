import { createHash } from "node:crypto";
import type {
	Context,
	ImageContent,
	ProviderHeaders,
	TextContent,
	ToolResultMessage,
	UserMessage,
} from "@earendil-works/pi-ai";
import type {
	ContextAdmission,
	ContextAdmissionResult,
	InputSource,
} from "@earendil-works/pi-coding-agent";

const HANDLE_HEADER = "x-openshell-agent-admission-handle";
const MAX_ADMISSION_BYTES = 4 * 1024 * 1024;
const MAX_BRIDGE_RESPONSE_BYTES = MAX_ADMISSION_BYTES * 4 + 64 * 1024;
const MAX_HANDLE_ENTRIES = 1024;

type ContentBlock = TextContent | ImageContent;
type UserEnvelope = { schema_version: "openshell.pi-input.v1"; text: string };
type ToolResultEnvelope = {
	schema_version: "openshell.pi-tool-result.v1";
	tool_call_id: string;
	tool_name: string;
	content: ContentBlock[];
	is_error: boolean;
};
type AdmissionEnvelope = UserEnvelope | ToolResultEnvelope;
type BridgeResult =
	| { decision: "deny"; reason_code?: string }
	| { decision: "allow"; handle: string; replacement_body?: number[] };

export function createOpenShellContextAdmission(
	bridgeUrl: string,
	getSessionId: () => string,
	fetchRequest: typeof fetch = fetch,
): ContextAdmission {
	const handles = new Map<string, string>();

	async function requestAdmission(
		hook: "rendered_prompt_admission" | "tool_result_admission",
		envelope: AdmissionEnvelope,
	): Promise<BridgeResult> {
		const requestBody = new TextEncoder().encode(JSON.stringify(envelope));
		if (requestBody.byteLength > MAX_ADMISSION_BYTES) {
			throw new Error("OpenShell admission request is too large");
		}
		const response = await fetchRequest(bridgeUrl, {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify({
				harness_version: "sdk-v1",
				hook,
				schema_version: envelope.schema_version,
				session_id: getSessionId(),
				submission_id: crypto.randomUUID(),
				request_body: Array.from(requestBody),
			}),
		});
		if (!response.ok) throw new Error("OpenShell admission is unavailable");
		const encoded = new Uint8Array(await response.arrayBuffer());
		if (encoded.byteLength > MAX_BRIDGE_RESPONSE_BYTES) {
			throw new Error("OpenShell admission response is too large");
		}
		return parseBridgeResult(JSON.parse(new TextDecoder().decode(encoded)));
	}

	async function admitUserMessage(
		message: UserMessage,
		_context?: { source: InputSource },
	): Promise<ContextAdmissionResult<UserMessage>> {
		const envelope = userEnvelope(message);
		if (!envelope) {
			return { action: "deny", reason: "Image inputs are not supported by OpenShell admission" };
		}
		const result = await requestAdmission("rendered_prompt_admission", envelope);
		if (result.decision === "deny") return denied(result.reason_code);
		const admittedEnvelope = result.replacement_body
			? parseUserEnvelope(new Uint8Array(result.replacement_body))
			: envelope;
		const admittedMessage: UserMessage = {
			...message,
			content: replaceUserText(message.content, admittedEnvelope.text),
		};
		rememberHandle(handles, messageKey(admittedMessage), result.handle);
		return admittedEnvelope.text === envelope.text
			? { action: "allow" }
			: { action: "allow", message: admittedMessage };
	}

	async function admitToolResult(
		message: ToolResultMessage,
	): Promise<ContextAdmissionResult<ToolResultMessage>> {
		const envelope = toolResultEnvelope(message);
		const result = await requestAdmission("tool_result_admission", envelope);
		if (result.decision === "deny") return denied(result.reason_code);
		const admittedEnvelope = result.replacement_body
			? parseToolResultEnvelope(new Uint8Array(result.replacement_body))
			: envelope;
		const admittedMessage: ToolResultMessage = { ...message, content: admittedEnvelope.content };
		rememberHandle(handles, messageKey(admittedMessage), result.handle);
		return result.replacement_body ? { action: "allow", message: admittedMessage } : { action: "allow" };
	}

	return {
		admitUserMessage,
		admitToolResult,
		async admitProviderContext(context) {
			for (let index = context.messages.length - 1; index >= 0; index -= 1) {
				const message = context.messages[index];
				if (message.role !== "user" && message.role !== "toolResult") continue;
				const result = message.role === "user" ? await admitUserMessage(message) : await admitToolResult(message);
				if (result.action === "deny") return result;
				if (!result.message) return { action: "allow" };
				const messages = [...context.messages];
				messages[index] = result.message;
				return { action: "allow", context: { ...context, messages } };
			}
			return { action: "deny", reason: "Provider context has no user message or tool result to admit" };
		},
		async transformProviderHeaders(headers: ProviderHeaders, context: Context) {
			if (Object.keys(headers).some((name) => name.toLowerCase() === HANDLE_HEADER)) {
				throw new Error("OpenShell admission handle header is reserved");
			}
			for (let index = context.messages.length - 1; index >= 0; index -= 1) {
				const message = context.messages[index];
				if (message.role !== "user" && message.role !== "toolResult") continue;
				const handle = handles.get(messageKey(message));
				if (handle) return { ...headers, [HANDLE_HEADER]: handle };
			}
			throw new Error("OpenShell admission handle is missing for the outbound context");
		},
	};
}

function userEnvelope(message: UserMessage): UserEnvelope | undefined {
	if (typeof message.content === "string") {
		return { schema_version: "openshell.pi-input.v1", text: message.content };
	}
	if (message.content.some((block) => block.type === "image")) return undefined;
	return {
		schema_version: "openshell.pi-input.v1",
		text: message.content.map((block) => (block as TextContent).text).join("\n"),
	};
}

function toolResultEnvelope(message: ToolResultMessage): ToolResultEnvelope {
	return {
		schema_version: "openshell.pi-tool-result.v1",
		tool_call_id: message.toolCallId,
		tool_name: message.toolName,
		content: message.content,
		is_error: message.isError,
	};
}

function messageKey(message: UserMessage | ToolResultMessage): string {
	return createHash("sha256")
		.update(JSON.stringify(message.role === "user" ? userEnvelope(message) : toolResultEnvelope(message)))
		.digest("hex");
}

function rememberHandle(handles: Map<string, string>, key: string, handle: string): void {
	handles.delete(key);
	handles.set(key, handle);
	if (handles.size > MAX_HANDLE_ENTRIES) {
		const oldest = handles.keys().next().value;
		if (oldest !== undefined) handles.delete(oldest);
	}
}

function replaceUserText(content: UserMessage["content"], text: string): UserMessage["content"] {
	return typeof content === "string" ? text : [{ type: "text", text }];
}

function denied(reasonCode?: string): { action: "deny"; reason: string } {
	return {
		action: "deny",
		reason: reasonCode
			? `OpenShell denied this context addition (${reasonCode})`
			: "OpenShell denied this context addition",
	};
}

function parseBridgeResult(value: unknown): BridgeResult {
	if (!isRecord(value) || (value.decision !== "allow" && value.decision !== "deny")) {
		throw new Error("OpenShell admission returned an invalid response");
	}
	if (value.decision === "deny") {
		return { decision: "deny", reason_code: typeof value.reason_code === "string" ? value.reason_code : undefined };
	}
	if (typeof value.handle !== "string" || !value.handle || value.handle.length > 1024) {
		throw new Error("OpenShell admission returned an invalid handle");
	}
	if (
		value.replacement_body !== undefined &&
		(!isByteArray(value.replacement_body) || value.replacement_body.length > MAX_ADMISSION_BYTES)
	) {
		throw new Error("OpenShell admission returned an invalid replacement");
	}
	return { decision: "allow", handle: value.handle, replacement_body: value.replacement_body };
}

function parseUserEnvelope(body: Uint8Array): UserEnvelope {
	const value: unknown = JSON.parse(new TextDecoder().decode(body));
	if (!isRecord(value) || value.schema_version !== "openshell.pi-input.v1" || typeof value.text !== "string") {
		throw new Error("OpenShell admission returned an invalid user replacement");
	}
	return { schema_version: "openshell.pi-input.v1", text: value.text };
}

function parseToolResultEnvelope(body: Uint8Array): ToolResultEnvelope {
	const value: unknown = JSON.parse(new TextDecoder().decode(body));
	if (
		!isRecord(value) ||
		value.schema_version !== "openshell.pi-tool-result.v1" ||
		typeof value.tool_call_id !== "string" ||
		typeof value.tool_name !== "string" ||
		!Array.isArray(value.content) ||
		typeof value.is_error !== "boolean"
	) {
		throw new Error("OpenShell admission returned an invalid tool-result replacement");
	}
	return value as ToolResultEnvelope;
}

function isByteArray(value: unknown): value is number[] {
	return Array.isArray(value) && value.every((byte) => Number.isInteger(byte) && byte >= 0 && byte <= 255);
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object";
}
