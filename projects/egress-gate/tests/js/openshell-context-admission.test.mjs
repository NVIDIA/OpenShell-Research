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

function assistant(text) {
	return {
		role: "assistant",
		content: [
			{ type: "thinking", thinking: "keep reasoning" },
			{ type: "text", text },
			{ type: "toolCall", id: "call-1", name: "read", arguments: { path: "safe" } },
		],
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
				const requestBody = new TextDecoder().decode(new Uint8Array(request.request_body));
				const envelope = JSON.parse(requestBody);
				assert.equal(request.hook, "user_message");
				assert.equal(envelope.schema_version, "openshell.pi-message.v1");
				assert.equal(envelope.origin, "user");
				assert.equal(
					requestBody,
					`{"origin":"user","schema_version":"openshell.pi-message.v1","text":${JSON.stringify(envelope.text)}}`,
				);
				return new Response(JSON.stringify({ decision: "allow", handle: `handle:${envelope.text}` }));
			},
		);
		const current = user("current", 1);
		const queued = { role: "user", content: "queued", timestamp: 2 };

		assert.equal((await admission.admitMessage(current, { origin: "user", source: "interactive" })).action, "allow");
		assert.equal((await admission.admitMessage(queued, { origin: "user", source: "interactive" })).action, "allow");

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
		const admitted = await admission.admitMessage(user("secret", 1), { origin: "user", source: "interactive" });

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

		await admission.admitMessage(prompt, { origin: "user", source: "interactive" });
		await admission.admitMessage(failed, { origin: "tool_result" });
		const headers = await admission.transformProviderHeaders(
			{},
			await admittedContext(admission, { messages: [prompt, failed], tools: [] }),
		);

		assert.equal(headers[HANDLE_HEADER], "handle:tool_result");
		assert.deepEqual(hooks, ["user_message", "tool_result", "tool_result"]);
	});

	it("maps every Pi message origin to its exact hook and envelope", async () => {
		const cases = [
			{
				origin: "user",
				message: user("user text", 1),
				hook: "user_message",
				schema: "openshell.pi-message.v1",
				expected: { origin: "user", text: "user text" },
			},
			{
				origin: "tool_result",
				message: toolResult("tool text"),
				hook: "tool_result",
				schema: "openshell.pi-tool-result.v1",
				expected: { tool_call_id: "call-1", tool_name: "bash", is_error: false },
			},
			{
				origin: "assistant",
				message: assistant("assistant text"),
				hook: "assistant_message",
				schema: "openshell.pi-assistant-message.v1",
				expected: { text: "assistant text", tool_calls: [{ id: "call-1", name: "read", arguments: { path: "safe" } }] },
			},
			{
				origin: "compaction_summary",
				message: { role: "compactionSummary", summary: "compact text" },
				hook: "compaction_summary",
				schema: "openshell.pi-message.v1",
				expected: { origin: "compaction_summary", text: "compact text" },
			},
			{
				origin: "branch_summary",
				message: { role: "branchSummary", summary: "branch text" },
				hook: "branch_summary",
				schema: "openshell.pi-message.v1",
				expected: { origin: "branch_summary", text: "branch text" },
			},
			{
				origin: "extension_message",
				message: { role: "custom", content: "extension text" },
				hook: "extension_message",
				schema: "openshell.pi-message.v1",
				expected: { origin: "extension_message", text: "extension text" },
			},
			{
				origin: "bash_execution",
				message: { role: "bashExecution", command: "printf safe", output: "bash text", exitCode: 0 },
				hook: "bash_execution",
				schema: "openshell.pi-bash-execution.v1",
				expected: { command: "printf safe", output: "bash text", exit_code: 0 },
			},
		];
		const observed = [];
		const admission = createOpenShellContextAdmission(
			"http://bridge.test/admit",
			() => "session-123",
			async (_url, init) => {
				const request = JSON.parse(String(init?.body));
				const envelope = JSON.parse(new TextDecoder().decode(new Uint8Array(request.request_body)));
				observed.push({ request, envelope });
				return new Response(JSON.stringify({ decision: "allow", handle: `handle:${request.hook}` }));
			},
		);

		for (const item of cases) {
			assert.equal((await admission.admitMessage(item.message, { origin: item.origin })).action, "allow");
		}
		assert.equal(observed.length, cases.length);
		for (const [index, item] of cases.entries()) {
			assert.equal(observed[index].request.hook, item.hook);
			assert.equal(observed[index].request.schema_version, item.schema);
			assert.equal(observed[index].envelope.schema_version, item.schema);
			for (const [key, value] of Object.entries(item.expected)) {
				assert.deepEqual(observed[index].envelope[key], value);
			}
		}
		assert.deepEqual(Object.keys(observed[2].envelope), ["schema_version", "text", "tool_calls"]);
		assert.deepEqual(Object.keys(observed[2].envelope.tool_calls[0]), ["arguments", "id", "name"]);
		assert.deepEqual(Object.keys(observed[6].envelope), ["command", "exit_code", "output", "schema_version"]);
	});

	it("applies replacements only to the origin's replaceable text", async () => {
		const admission = createOpenShellContextAdmission(
			"http://bridge.test/admit",
			() => "session-123",
			async (_url, init) => {
				const request = JSON.parse(String(init?.body));
				const envelope = JSON.parse(new TextDecoder().decode(new Uint8Array(request.request_body)));
				if ("text" in envelope) envelope.text = "[REDACTED]";
				if ("output" in envelope) envelope.output = "[REDACTED]";
				return new Response(
					JSON.stringify({
						decision: "allow",
						handle: `handle:${request.hook}`,
						replacement_body: [...new TextEncoder().encode(JSON.stringify(envelope))],
					}),
				);
			},
		);

		const summary = await admission.admitMessage(
			{ role: "compactionSummary", summary: "secret" },
			{ origin: "compaction_summary" },
		);
		const bash = await admission.admitMessage(
			{ role: "bashExecution", command: "printf safe", output: "secret", exitCode: 7 },
			{ origin: "bash_execution" },
		);
		const reply = await admission.admitMessage(assistant("secret"), { origin: "assistant" });

		assert.equal(summary.message.summary, "[REDACTED]");
		assert.deepEqual(
			{ command: bash.message.command, output: bash.message.output, exitCode: bash.message.exitCode },
			{ command: "printf safe", output: "[REDACTED]", exitCode: 7 },
		);
		assert.deepEqual(reply.message.content, [
			{ type: "thinking", thinking: "keep reasoning" },
			{ type: "text", text: "[REDACTED]" },
			{ type: "toolCall", id: "call-1", name: "read", arguments: { path: "safe" } },
		]);
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
