import assert from "node:assert/strict";
import test from "node:test";

import { createOpenShellContextAdmission } from "./managed-pi-admission.ts";

const HANDLE_HEADER = "x-openshell-agent-admission-handle";

function user(text, timestamp) {
	return { role: "user", content: [{ type: "text", text }], timestamp };
}

function toolResult(text, isError = false) {
	return {
		role: "toolResult",
		toolCallId: "call-1",
		toolName: "bash",
		content: [{ type: "text", text }],
		isError,
		timestamp: 2,
	};
}

async function admittedProviderContext(admission, context) {
	const result = await admission.admitProviderContext(context);
	assert.equal(result.action, "allow");
	return result.context ?? context;
}

test("selects the handle for the exact queued or retried provider context", async () => {
	const bridgeRequests = [];
	const admission = createOpenShellContextAdmission("http://bridge.test/admit", () => "session-123", async (_url, init) => {
		const request = JSON.parse(init.body);
		bridgeRequests.push(request);
		const envelope = JSON.parse(new TextDecoder().decode(new Uint8Array(request.request_body)));
		return new Response(JSON.stringify({ decision: "allow", handle: `handle:${envelope.text}` }));
	});
	const current = user("current turn", 1);
	const queued = user("queued turn", 2);

	assert.deepEqual(await admission.admitUserMessage(current, { source: "interactive" }), { action: "allow" });
	assert.deepEqual(await admission.admitUserMessage(queued, { source: "interactive" }), { action: "allow" });

	const currentContext = await admittedProviderContext(admission, { messages: [current], tools: [] });
	const currentHeaders = await admission.transformProviderHeaders({}, currentContext);
	const retryContext = await admittedProviderContext(admission, { messages: [current], tools: [] });
	const retryHeaders = await admission.transformProviderHeaders({}, retryContext);
	const queuedContext = await admittedProviderContext(admission, { messages: [current, queued], tools: [] });
	const queuedHeaders = await admission.transformProviderHeaders({}, queuedContext);

	assert.equal(currentHeaders[HANDLE_HEADER], "handle:current turn");
	assert.equal(retryHeaders[HANDLE_HEADER], "handle:current turn");
	assert.equal(queuedHeaders[HANDLE_HEADER], "handle:queued turn");
	assert.deepEqual(
		bridgeRequests.map((request) => [request.hook, request.schema_version]),
		[
			["rendered_prompt_admission", "openshell.pi-input.v1"],
			["rendered_prompt_admission", "openshell.pi-input.v1"],
			["rendered_prompt_admission", "openshell.pi-input.v1"],
			["rendered_prompt_admission", "openshell.pi-input.v1"],
			["rendered_prompt_admission", "openshell.pi-input.v1"],
		],
	);
	assert.deepEqual(
		bridgeRequests.map((request) => request.session_id),
		["session-123", "session-123", "session-123", "session-123", "session-123"],
	);
});

test("uses an admitted replacement as the handle lookup key", async () => {
	const replacement = new TextEncoder().encode(
		JSON.stringify({ schema_version: "openshell.pi-input.v1", text: "[REDACTED]" }),
	);
	const admission = createOpenShellContextAdmission("http://bridge.test/admit", () => "session-123", async () =>
		new Response(
			JSON.stringify({
				decision: "allow",
				handle: "replacement-handle",
				replacement_body: Array.from(replacement),
			}),
		),
	);
	const original = user("secret", 1);
	const admitted = await admission.admitUserMessage(original, { source: "interactive" });

	assert.equal(admitted.action, "allow");
	assert.equal(admitted.message.content[0].text, "[REDACTED]");
	const context = await admittedProviderContext(admission, { messages: [admitted.message], tools: [] });
	const headers = await admission.transformProviderHeaders({}, context);
	assert.equal(headers[HANDLE_HEADER], "replacement-handle");
});

test("selects the handle for an admitted failed tool result", async () => {
	const observedHooks = [];
	const admission = createOpenShellContextAdmission("http://bridge.test/admit", () => "session-123", async (_url, init) => {
		const request = JSON.parse(init.body);
		observedHooks.push(request.hook);
		return new Response(JSON.stringify({ decision: "allow", handle: `handle:${request.hook}` }));
	});
	const prompt = user("run a command", 1);
	const failed = toolResult("(no output)\n\nCommand exited with code 2", true);

	assert.equal((await admission.admitUserMessage(prompt, { source: "interactive" })).action, "allow");
	assert.equal((await admission.admitToolResult(failed)).action, "allow");
	const context = await admittedProviderContext(admission, { messages: [prompt, failed], tools: [] });
	const headers = await admission.transformProviderHeaders({}, context);

	assert.equal(headers[HANDLE_HEADER], "handle:tool_result_admission");
	assert.deepEqual(observedHooks, ["rendered_prompt_admission", "tool_result_admission", "tool_result_admission"]);
});

test("admits and replaces a generated provider-only context", async () => {
	const observedTexts = [];
	const admission = createOpenShellContextAdmission("http://bridge.test/admit", () => "session-123", async (_url, init) => {
		const request = JSON.parse(init.body);
		const envelope = JSON.parse(new TextDecoder().decode(new Uint8Array(request.request_body)));
		observedTexts.push(envelope.text);
		const replacement = new TextEncoder().encode(
			JSON.stringify({ schema_version: "openshell.pi-input.v1", text: "admitted summary context" }),
		);
		return new Response(
			JSON.stringify({
				decision: "allow",
				handle: "summary-handle",
				replacement_body: Array.from(replacement),
			}),
		);
	});
	const generated = { messages: [user("generated summary context", 1)], tools: [] };

	const context = await admittedProviderContext(admission, generated);
	const headers = await admission.transformProviderHeaders({}, context);

	assert.deepEqual(observedTexts, ["generated summary context"]);
	assert.equal(context.messages[0].content[0].text, "admitted summary context");
	assert.equal(headers[HANDLE_HEADER], "summary-handle");
});

test("fails closed when a generated provider-only context is denied", async () => {
	const admission = createOpenShellContextAdmission("http://bridge.test/admit", () => "session-123", async () =>
		new Response(JSON.stringify({ decision: "deny", reason_code: "policy_denied" })),
	);

	const result = await admission.admitProviderContext({
		messages: [user("generated summary context", 1)],
		tools: [],
	});

	assert.deepEqual(result, {
		action: "deny",
		reason: "OpenShell denied this context addition (policy_denied)",
	});
});

test("re-admits provider context restored without an in-memory handle", async () => {
	let requests = 0;
	const admission = createOpenShellContextAdmission("http://bridge.test/admit", () => "session-123", async () => {
		requests++;
		return new Response(JSON.stringify({ decision: "allow", handle: "restored-handle" }));
	});
	const restored = { messages: [user("restored session message", 1)], tools: [] };

	await assert.rejects(admission.transformProviderHeaders({}, restored), /admission handle is missing/);
	const context = await admittedProviderContext(admission, restored);
	const headers = await admission.transformProviderHeaders({}, context);

	assert.equal(requests, 1);
	assert.equal(headers[HANDLE_HEADER], "restored-handle");
});

test("bounds handles retained for a long-lived session", async () => {
	const admission = createOpenShellContextAdmission("http://bridge.test/admit", () => "session-123", async (_url, init) => {
		const request = JSON.parse(init.body);
		const envelope = JSON.parse(new TextDecoder().decode(new Uint8Array(request.request_body)));
		return new Response(JSON.stringify({ decision: "allow", handle: `handle:${envelope.text}` }));
	});
	const messages = Array.from({ length: 1025 }, (_, index) => user(`turn ${index}`, index));
	for (const message of messages) {
		assert.equal((await admission.admitUserMessage(message, { source: "interactive" })).action, "allow");
	}

	await assert.rejects(
		admission.transformProviderHeaders({}, { messages: [messages[0]], tools: [] }),
		/OpenShell admission handle is missing/,
	);
	const headers = await admission.transformProviderHeaders({}, { messages: [messages.at(-1)], tools: [] });
	assert.equal(headers[HANDLE_HEADER], "handle:turn 1024");
});

test("uses the current session ID after a new session starts", async () => {
	let sessionId = "session-1";
	const observedSessionIds = [];
	const admission = createOpenShellContextAdmission("http://bridge.test/admit", () => sessionId, async (_url, init) => {
		const request = JSON.parse(init.body);
		observedSessionIds.push(request.session_id);
		return new Response(JSON.stringify({ decision: "allow", handle: `handle:${request.session_id}` }));
	});

	await admission.admitUserMessage(user("first", 1), { source: "interactive" });
	sessionId = "session-2";
	await admission.admitUserMessage(user("after new", 2), { source: "interactive" });

	assert.deepEqual(observedSessionIds, ["session-1", "session-2"]);
});
