import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import type { AgentMessage } from "@earendil-works/pi-agent-core";
import type {
	AssistantMessage,
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
	MessageOrigin,
} from "@earendil-works/pi-coding-agent";

const HANDLE_HEADER = "x-openshell-agent-admission-handle";
const MAX_ADMISSION_BYTES = 4 * 1024 * 1024;
const MAX_BRIDGE_RESPONSE_BYTES = MAX_ADMISSION_BYTES * 4 + 64 * 1024;
const MAX_HANDLE_ENTRIES = 1024;

type ContentBlock = TextContent | ImageContent;
type MessageEnvelope = {
	schema_version: "openshell.pi-message.v1";
	origin: "user" | "compaction_summary" | "branch_summary" | "extension_message";
	text: string;
};
type ToolResultEnvelope = {
	schema_version: "openshell.pi-tool-result.v1";
	tool_call_id: string;
	tool_name: string;
	content: ContentBlock[];
	is_error: boolean;
};
type AssistantEnvelope = {
	schema_version: "openshell.pi-assistant-message.v1";
	text: string;
	tool_calls: { id: string; name: string; arguments: Record<string, unknown> }[];
};
type BashEnvelope = {
	schema_version: "openshell.pi-bash-execution.v1";
	command: string;
	output: string;
	exit_code: number | null;
};
type ContextEntry =
	| { role: "user"; text: string }
	| { role: "tool"; tool_call_id: string; text: string };
type ProviderContextEnvelope = {
	schema_version: "openshell.pi-provider-context.v1";
	entries: ContextEntry[];
};
type AdmissionEnvelope = MessageEnvelope | ToolResultEnvelope | AssistantEnvelope | BashEnvelope;
type BridgeEnvelope = AdmissionEnvelope | ProviderContextEnvelope;
type AppendAdmissionHook =
	| "user_message"
	| "tool_result"
	| "assistant_message"
	| "compaction_summary"
	| "branch_summary"
	| "extension_message"
	| "bash_execution";
type AdmissionHook = AppendAdmissionHook | "provider_context";
type BridgeResult =
	| { decision: "deny"; reason_code?: string }
	| { decision: "allow"; handle?: string; replacement_body?: Uint8Array };

type SummaryMessage = AgentMessage & { summary: string };
type CustomMessage = AgentMessage & { content: string | ContentBlock[] };
type BashMessage = AgentMessage & { command: string; output: string; exitCode: number | undefined };

export function createOpenShellContextAdmission(
	bridgeUrl: string,
	getSessionId: () => string,
	admissionToken: string,
	fetchRequest: typeof fetch = fetch,
): ContextAdmission {
	const handles = new Map<string, string>();

	async function requestAdmission(hook: AdmissionHook, envelope: BridgeEnvelope): Promise<BridgeResult> {
		const requestBody = new TextEncoder().encode(canonicalJson(envelope));
		if (requestBody.byteLength > MAX_ADMISSION_BYTES) {
			throw new Error("OpenShell admission request is too large");
		}
		const response = await fetchRequest(bridgeUrl, {
			method: "POST",
			headers: {
				authorization: `Bearer ${admissionToken}`,
				"content-type": "application/json",
			},
			body: JSON.stringify({
				harness_version: "sdk-v1",
				hook,
				schema_version: envelope.schema_version,
				session_id: getSessionId(),
				submission_id: crypto.randomUUID(),
				request_body_b64: Buffer.from(requestBody).toString("base64"),
			}),
		});
		if (!response.ok) throw new Error("OpenShell admission is unavailable");
		const encoded = new Uint8Array(await response.arrayBuffer());
		if (encoded.byteLength > MAX_BRIDGE_RESPONSE_BYTES) {
			throw new Error("OpenShell admission response is too large");
		}
		return parseBridgeResult(JSON.parse(new TextDecoder().decode(encoded)));
	}

	async function admitMessage<T extends AgentMessage>(
		message: T,
		meta: { origin: MessageOrigin },
	): Promise<ContextAdmissionResult<T>> {
		const prepared = envelopeForMessage(message, meta.origin);
		if (!prepared) {
			return { action: "deny", reason: "Image inputs are not supported by OpenShell admission" };
		}
		const result = await requestAdmission(prepared.hook, prepared.envelope);
		if (result.decision === "deny") return denied(result.reason_code);
		const admittedEnvelope = result.replacement_body
			? parseReplacement(prepared.hook, result.replacement_body)
			: prepared.envelope;
		const admittedMessage = applyReplacement(message, meta.origin, admittedEnvelope);
		return result.replacement_body ? { action: "allow", message: admittedMessage } : { action: "allow" };
	}

	return {
		admitMessage,
		async admitProviderContext(context) {
			const envelope = providerContextEnvelope(context);
			if (!envelope) {
				return { action: "deny", reason: "Image inputs are not supported by OpenShell admission" };
			}
			const result = await requestAdmission("provider_context", envelope);
			if (result.decision === "deny") return denied(result.reason_code);
			if (!result.handle) throw new Error("OpenShell admission returned no provider-context handle");
			const admittedEnvelope = result.replacement_body
				? parseProviderContextReplacement(result.replacement_body, envelope)
				: envelope;
			const admittedContext = applyProviderContextReplacement(context, admittedEnvelope.entries);
			rememberHandle(handles, contextKey(admittedEnvelope), result.handle);
			return result.replacement_body ? { action: "allow", context: admittedContext } : { action: "allow" };
		},
		async transformProviderHeaders(headers: ProviderHeaders, context: Context) {
			if (Object.keys(headers).some((name) => name.toLowerCase() === HANDLE_HEADER)) {
				throw new Error("OpenShell admission handle header is reserved");
			}
			const envelope = providerContextEnvelope(context);
			if (!envelope) throw new Error("Image inputs are not supported by OpenShell admission");
			const handle = handles.get(contextKey(envelope));
			if (handle) return { ...headers, [HANDLE_HEADER]: handle };
			throw new Error("OpenShell admission handle is missing for the outbound context");
		},
	};
}

function envelopeForMessage(
	message: AgentMessage,
	origin: MessageOrigin,
): { hook: AppendAdmissionHook; envelope: AdmissionEnvelope } | undefined {
	switch (origin) {
		case "user": {
			if (message.role !== "user") throw new Error("Pi admission origin does not match the message");
			const envelope = textEnvelope("user", message.content);
			return envelope && { hook: "user_message", envelope };
		}
		case "tool_result":
			if (message.role !== "toolResult") throw new Error("Pi admission origin does not match the message");
			return { hook: "tool_result", envelope: toolResultEnvelope(message) };
		case "assistant":
			if (message.role !== "assistant") throw new Error("Pi admission origin does not match the message");
			return { hook: "assistant_message", envelope: assistantEnvelope(message) };
		case "compaction_summary":
			if (message.role !== "compactionSummary") throw new Error("Pi admission origin does not match the message");
			return {
				hook: "compaction_summary",
				envelope: messageEnvelope("compaction_summary", (message as SummaryMessage).summary),
			};
		case "branch_summary":
			if (message.role !== "branchSummary") throw new Error("Pi admission origin does not match the message");
			return {
				hook: "branch_summary",
				envelope: messageEnvelope("branch_summary", (message as SummaryMessage).summary),
			};
		case "extension_message": {
			if (message.role !== "custom") throw new Error("Pi admission origin does not match the message");
			const envelope = textEnvelope("extension_message", (message as CustomMessage).content);
			return envelope && { hook: "extension_message", envelope };
		}
		case "bash_execution": {
			if (message.role !== "bashExecution") throw new Error("Pi admission origin does not match the message");
			const bash = message as BashMessage;
			return {
				hook: "bash_execution",
				envelope: {
					command: bash.command,
					exit_code: bash.exitCode ?? null,
					output: bash.output,
					schema_version: "openshell.pi-bash-execution.v1",
				},
			};
		}
	}
}

function messageEnvelope(origin: MessageEnvelope["origin"], text: string): MessageEnvelope {
	return { origin, schema_version: "openshell.pi-message.v1", text };
}

function textEnvelope(origin: MessageEnvelope["origin"], content: string | ContentBlock[]): MessageEnvelope | undefined {
	if (typeof content === "string") return messageEnvelope(origin, content);
	if (content.some((block) => block.type === "image")) return undefined;
	return {
		origin,
		schema_version: "openshell.pi-message.v1",
		text: content.map((block) => (block as TextContent).text).join("\n"),
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

function assistantEnvelope(message: AssistantMessage): AssistantEnvelope {
	return {
		schema_version: "openshell.pi-assistant-message.v1",
		text: message.content
			.filter((block): block is TextContent => block.type === "text")
			.map((block) => block.text)
			.join("\n"),
		tool_calls: message.content
			.filter((block) => block.type === "toolCall")
			.map(({ id, name, arguments: args }) => ({ arguments: args, id, name })),
	};
}

function applyReplacement<T extends AgentMessage>(message: T, origin: MessageOrigin, envelope: AdmissionEnvelope): T {
	switch (origin) {
		case "user":
			return { ...message, content: replaceTextContent((message as UserMessage).content, (envelope as MessageEnvelope).text) };
		case "tool_result":
			return { ...message, content: (envelope as ToolResultEnvelope).content };
		case "assistant":
			return replaceAssistantText(message as AssistantMessage, (envelope as AssistantEnvelope).text) as T;
		case "compaction_summary":
		case "branch_summary":
			return { ...message, summary: (envelope as MessageEnvelope).text };
		case "extension_message":
			return { ...message, content: replaceTextContent((message as CustomMessage).content, (envelope as MessageEnvelope).text) };
		case "bash_execution":
			return { ...message, output: (envelope as BashEnvelope).output };
	}
	throw new Error("Pi admission origin is unsupported");
}

function replaceAssistantText(message: AssistantMessage, text: string): AssistantMessage {
	const content: AssistantMessage["content"] = [];
	let replaced = false;
	for (const block of message.content) {
		if (block.type !== "text") {
			content.push(block);
		} else if (!replaced) {
			if (text) content.push({ type: "text", text });
			replaced = true;
		}
	}
	if (!replaced && text) content.push({ type: "text", text });
	return { ...message, content };
}

function replaceTextContent(content: string | ContentBlock[], text: string): string | TextContent[] {
	return typeof content === "string" ? text : [{ type: "text", text }];
}

function providerContextEnvelope(context: Context): ProviderContextEnvelope | undefined {
	const entries: ContextEntry[] = [];
	for (const message of context.messages) {
		if (message.role === "user") {
			const text = textContent(message.content);
			if (text === undefined) return undefined;
			entries.push({ role: "user", text });
		} else if (message.role === "toolResult") {
			const text = textBlocks(message.content);
			if (text === undefined) return undefined;
			entries.push({
				role: "tool",
				tool_call_id: message.toolCallId.split("|", 1)[0],
				text: text || "(no tool output)",
			});
		}
	}
	if (entries.length === 0) throw new Error("Provider context has no user message or tool result to admit");
	return { schema_version: "openshell.pi-provider-context.v1", entries };
}

function textContent(content: string | ContentBlock[]): string | undefined {
	if (typeof content === "string") return content;
	return textBlocks(content);
}

function textBlocks(content: ContentBlock[]): string | undefined {
	if (content.some((block) => block.type === "image")) return undefined;
	return content.map((block) => (block as TextContent).text).join("\n");
}

function applyProviderContextReplacement(context: Context, entries: ContextEntry[]): Context {
	let entryIndex = 0;
	const messages = context.messages.map((message) => {
		if (message.role !== "user" && message.role !== "toolResult") return message;
		const entry = entries[entryIndex++];
		if (message.role === "user") {
			if (entry.role !== "user") throw new Error("OpenShell admission changed provider-context structure");
			return { ...message, content: replaceTextContent(message.content, entry.text) };
		}
		if (entry.role !== "tool" || entry.tool_call_id !== message.toolCallId.split("|", 1)[0]) {
			throw new Error("OpenShell admission changed provider-context structure");
		}
		return { ...message, content: [{ type: "text" as const, text: entry.text }] };
	});
	if (entryIndex !== entries.length) throw new Error("OpenShell admission changed provider-context structure");
	return { ...context, messages };
}

function contextKey(envelope: ProviderContextEnvelope): string {
	return createHash("sha256").update(canonicalJson(envelope.entries)).digest("hex");
}

function canonicalJson(value: unknown): string {
	return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(sortJson);
	if (!isRecord(value)) return value;
	return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortJson(value[key])]));
}

function rememberHandle(handles: Map<string, string>, key: string, handle: string): void {
	handles.delete(key);
	handles.set(key, handle);
	if (handles.size > MAX_HANDLE_ENTRIES) {
		const oldest = handles.keys().next().value;
		if (oldest !== undefined) handles.delete(oldest);
	}
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
	if (
		value.handle !== undefined &&
		(typeof value.handle !== "string" || !value.handle || value.handle.length > 1024)
	) {
		throw new Error("OpenShell admission returned an invalid handle");
	}
	let replacementBody: Uint8Array | undefined;
	if (value.replacement_body_b64 !== undefined) {
		if (typeof value.replacement_body_b64 !== "string") {
			throw new Error("OpenShell admission returned an invalid replacement");
		}
		const decoded = Buffer.from(value.replacement_body_b64, "base64");
		if (decoded.toString("base64") !== value.replacement_body_b64 || decoded.byteLength > MAX_ADMISSION_BYTES) {
			throw new Error("OpenShell admission returned an invalid replacement");
		}
		replacementBody = decoded;
	}
	return { decision: "allow", handle: value.handle, replacement_body: replacementBody };
}

function parseProviderContextReplacement(
	body: Uint8Array,
	original: ProviderContextEnvelope,
): ProviderContextEnvelope {
	const value: unknown = JSON.parse(new TextDecoder().decode(body));
	if (
		!isRecord(value) ||
		value.schema_version !== "openshell.pi-provider-context.v1" ||
		!Array.isArray(value.entries) ||
		value.entries.length !== original.entries.length
	) {
		throw new Error("OpenShell admission returned an invalid provider-context replacement");
	}
	const entries = value.entries.map((entry, index): ContextEntry => {
		const expected = original.entries[index];
		if (!isRecord(entry) || entry.role !== expected.role || typeof entry.text !== "string") {
			throw new Error("OpenShell admission changed provider-context structure");
		}
		if (entry.role === "user" && entry.tool_call_id === undefined) return { role: "user", text: entry.text };
		if (
			entry.role === "tool" &&
			typeof entry.tool_call_id === "string" &&
			expected.role === "tool" &&
			entry.tool_call_id === expected.tool_call_id
		) {
			return { role: "tool", tool_call_id: entry.tool_call_id, text: entry.text };
		}
		throw new Error("OpenShell admission changed provider-context structure");
	});
	return { schema_version: "openshell.pi-provider-context.v1", entries };
}

function parseReplacement(hook: AppendAdmissionHook, body: Uint8Array): AdmissionEnvelope {
	const value: unknown = JSON.parse(new TextDecoder().decode(body));
	if (!isRecord(value)) throw new Error("OpenShell admission returned an invalid replacement");
	switch (hook) {
		case "user_message":
		case "compaction_summary":
		case "branch_summary":
		case "extension_message":
			if (
				value.schema_version !== "openshell.pi-message.v1" ||
				typeof value.origin !== "string" ||
				typeof value.text !== "string"
			) throw new Error("OpenShell admission returned an invalid message replacement");
			return value as MessageEnvelope;
		case "tool_result":
			if (
				value.schema_version !== "openshell.pi-tool-result.v1" ||
				typeof value.tool_call_id !== "string" ||
				typeof value.tool_name !== "string" ||
				!Array.isArray(value.content) ||
				typeof value.is_error !== "boolean"
			) throw new Error("OpenShell admission returned an invalid tool-result replacement");
			return value as ToolResultEnvelope;
		case "assistant_message":
			if (
				value.schema_version !== "openshell.pi-assistant-message.v1" ||
				typeof value.text !== "string" ||
				!Array.isArray(value.tool_calls)
			) throw new Error("OpenShell admission returned an invalid assistant replacement");
			return value as AssistantEnvelope;
		case "bash_execution":
			if (
				value.schema_version !== "openshell.pi-bash-execution.v1" ||
				typeof value.command !== "string" ||
				typeof value.output !== "string" ||
				(value.exit_code !== null && typeof value.exit_code !== "number")
			) throw new Error("OpenShell admission returned an invalid bash replacement");
			return value as BashEnvelope;
	}
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object";
}
