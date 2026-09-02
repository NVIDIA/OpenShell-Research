import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createOpenShellContextAdmission } from "../../examples/pi-attested-admission/runtime-extension/openshell-context-admission.ts";

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

async function admittedContext(admission, context) {
	const result = await admission.admitProviderContext(context);
	assert.equal(result.action, "allow");
	return result.context ?? context;
}

describe("OpenShell context admission adapter", () => {
	it("selects the handle for the exact provider context", async () => {
		const admission = createOpenShellContextAdmission(
			"http://bridge.test/admit",
			() => "session-123",
			async (_url, init) => {
				const request = JSON.parse(String(init?.body));
				const envelope = JSON.parse(new TextDecoder().decode(new Uint8Array(request.request_body)));
				assert.equal(request.hook, "user_message");
				assert.equal(envelope.schema_version, "openshell.pi-message.v1");
				assert.equal(envelope.origin, "user");
				return new Response(JSON.stringify({ decision: "allow", handle: `handle:${envelope.text}` }));
			},
		);
		const current = user("current", 1);
		const queued = user("queued", 2);

		assert.equal((await admission.admitUserMessage(current, { source: "interactive" })).action, "allow");
		assert.equal((await admission.admitUserMessage(queued, { source: "interactive" })).action, "allow");

		const currentHeaders = await admission.transformProviderHeaders(
			{},
			await admittedContext(admission, { messages: [current], tools: [] }),
		);
		const queuedHeaders = await admission.transformProviderHeaders(
			{},
			await admittedContext(admission, { messages: [current, queued], tools: [] }),
		);

		assert.equal(currentHeaders[HANDLE_HEADER], "handle:current");
		assert.equal(queuedHeaders[HANDLE_HEADER], "handle:queued");
	});

	it("uses an admitted replacement for the outbound handle", async () => {
		const replacement = new TextEncoder().encode(
			JSON.stringify({ schema_version: "openshell.pi-message.v1", origin: "user", text: "[REDACTED]" }),
		);
		const admission = createOpenShellContextAdmission(
			"http://bridge.test/admit",
			() => "session-123",
			async () =>
				new Response(
					JSON.stringify({ decision: "allow", handle: "replacement-handle", replacement_body: [...replacement] }),
				),
		);
		const admitted = await admission.admitUserMessage(user("secret", 1), { source: "interactive" });

		assert.equal(admitted.action, "allow");
		assert.ok(admitted.message);
		const context = await admittedContext(admission, { messages: [admitted.message], tools: [] });
		const headers = await admission.transformProviderHeaders({}, context);

		assert.deepEqual(admitted.message.content, [{ type: "text", text: "[REDACTED]" }]);
		assert.equal(headers[HANDLE_HEADER], "replacement-handle");
	});

	it("attests failed tool results", async () => {
		const hooks = [];
		const admission = createOpenShellContextAdmission(
			"http://bridge.test/admit",
			() => "session-123",
			async (_url, init) => {
				const request = JSON.parse(String(init?.body));
				hooks.push(request.hook);
				return new Response(JSON.stringify({ decision: "allow", handle: `handle:${request.hook}` }));
			},
		);
		const prompt = user("run command", 1);
		const failed = toolResult("Command exited with code 2", true);

		await admission.admitUserMessage(prompt, { source: "interactive" });
		await admission.admitToolResult(failed);
		const headers = await admission.transformProviderHeaders(
			{},
			await admittedContext(admission, { messages: [prompt, failed], tools: [] }),
		);

		assert.equal(headers[HANDLE_HEADER], "handle:tool_result");
		assert.deepEqual(hooks, ["user_message", "tool_result", "tool_result"]);
	});

	it("fails closed when provider-only context is denied", async () => {
		const admission = createOpenShellContextAdmission(
			"http://bridge.test/admit",
			() => "session-123",
			async () => new Response(JSON.stringify({ decision: "deny", reason_code: "policy_denied" })),
		);

		assert.deepEqual(await admission.admitProviderContext({ messages: [user("summary", 1)], tools: [] }), {
			action: "deny",
			reason: "OpenShell denied this context addition (policy_denied)",
		});
	});
});
