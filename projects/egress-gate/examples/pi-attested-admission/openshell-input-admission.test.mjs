import assert from "node:assert/strict";
import test from "node:test";

import registerAdmission from "./openshell-input-admission.ts";

const BRIDGE_URL_ENV = "OPENSHELL_AGENT_CONVERSATION_URL";
const RECEIPT_HEADER = "x-openshell-middleware-egress-receipt";

function createHarness() {
	const handlers = new Map();
	registerAdmission({
		on(event, handler) {
			handlers.set(event, handler);
		},
	});
	return handlers;
}

function createContext(isIdle = true) {
	return {
		isIdle: () => isIdle,
		sessionManager: { getSessionId: () => "session-1" },
		signal: new AbortController().signal,
		ui: { notify: () => {} },
	};
}

function allowResponse(receipt) {
	return new Response(
		JSON.stringify({
			decision: "allow",
			receipt: Array.from(new TextEncoder().encode(receipt)),
		}),
		{ status: 200 },
	);
}

test("uses a fresh receipt for every provider request in one admitted turn", async () => {
	const originalBridgeUrl = process.env[BRIDGE_URL_ENV];
	const originalFetch = globalThis.fetch;
	const bridgeRequests = [];
	process.env[BRIDGE_URL_ENV] = "http://bridge.test/admit";
	globalThis.fetch = async (_url, init) => {
		bridgeRequests.push(JSON.parse(init.body));
		return allowResponse(`receipt-${bridgeRequests.length}`);
	};

	try {
		const handlers = createHarness();
		const ctx = createContext();
		const append = await handlers.get("before_user_message_append")(
			{ text: "inspect the repository" },
			ctx,
		);
		assert.equal(append, undefined);

		const firstHeaders = {};
		await handlers.get("before_provider_headers")({ headers: firstHeaders }, ctx);
		assert.equal(firstHeaders[RECEIPT_HEADER], "receipt-1");

		const continuationHeaders = {};
		await handlers.get("before_provider_headers")({ headers: continuationHeaders }, ctx);
		assert.equal(continuationHeaders[RECEIPT_HEADER], "receipt-2");

		assert.equal(bridgeRequests.length, 2);
		assert.notEqual(bridgeRequests[0].submission_id, bridgeRequests[1].submission_id);
		assert.deepEqual(bridgeRequests[0].request_body, bridgeRequests[1].request_body);
	} finally {
		globalThis.fetch = originalFetch;
		if (originalBridgeUrl === undefined) delete process.env[BRIDGE_URL_ENV];
		else process.env[BRIDGE_URL_ENV] = originalBridgeUrl;
	}
});

test("activates queued prompts only when Pi delivers them", async () => {
	const originalBridgeUrl = process.env[BRIDGE_URL_ENV];
	const originalFetch = globalThis.fetch;
	let receiptNumber = 0;
	process.env[BRIDGE_URL_ENV] = "http://bridge.test/admit";
	globalThis.fetch = async () => allowResponse(`receipt-${++receiptNumber}`);

	try {
		const handlers = createHarness();
		const idleContext = createContext();
		await handlers.get("before_user_message_append")({ text: "current turn" }, idleContext);

		const initialHeaders = {};
		await handlers.get("before_provider_headers")({ headers: initialHeaders }, idleContext);
		assert.equal(initialHeaders[RECEIPT_HEADER], "receipt-1");

		const streamingContext = createContext(false);
		await handlers.get("before_user_message_append")({ text: "queued turn" }, streamingContext);

		const currentContinuationHeaders = {};
		await handlers.get("before_provider_headers")({ headers: currentContinuationHeaders }, idleContext);
		assert.equal(currentContinuationHeaders[RECEIPT_HEADER], "receipt-3");

		await handlers.get("message_start")({
			message: { role: "user", content: [{ type: "text", text: "queued turn" }] },
		});
		const queuedHeaders = {};
		await handlers.get("before_provider_headers")({ headers: queuedHeaders }, idleContext);
		assert.equal(queuedHeaders[RECEIPT_HEADER], "receipt-2");
	} finally {
		globalThis.fetch = originalFetch;
		if (originalBridgeUrl === undefined) delete process.env[BRIDGE_URL_ENV];
		else process.env[BRIDGE_URL_ENV] = originalBridgeUrl;
	}
});
