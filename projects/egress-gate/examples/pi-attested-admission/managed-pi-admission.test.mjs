import assert from "node:assert/strict";
import test from "node:test";

import { createOpenShellContextAdmission } from "./managed-pi-admission.ts";

const HANDLE_HEADER = "x-openshell-agent-admission-handle";

function user(text, timestamp) {
	return { role: "user", content: [{ type: "text", text }], timestamp };
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

	const currentHeaders = await admission.transformProviderHeaders({}, { messages: [current], tools: [] });
	const retryHeaders = await admission.transformProviderHeaders({}, { messages: [current], tools: [] });
	const queuedHeaders = await admission.transformProviderHeaders({}, { messages: [current, queued], tools: [] });

	assert.equal(currentHeaders[HANDLE_HEADER], "handle:current turn");
	assert.equal(retryHeaders[HANDLE_HEADER], "handle:current turn");
	assert.equal(queuedHeaders[HANDLE_HEADER], "handle:queued turn");
	assert.deepEqual(
		bridgeRequests.map((request) => [request.hook, request.schema_version]),
		[
			["rendered_prompt_admission", "openshell.pi-input.v1"],
			["rendered_prompt_admission", "openshell.pi-input.v1"],
		],
	);
	assert.deepEqual(bridgeRequests.map((request) => request.session_id), ["session-123", "session-123"]);
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
	const headers = await admission.transformProviderHeaders(
		{},
		{ messages: [admitted.message], tools: [] },
	);
	assert.equal(headers[HANDLE_HEADER], "replacement-handle");
});

test("bounds handles retained for a long-lived in-memory session", async () => {
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

test("uses the current session ID after a new in-memory session starts", async () => {
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
