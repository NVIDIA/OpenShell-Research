import { runCli, type RuntimeExtension } from "@earendil-works/pi-coding-agent";

import { createOpenShellContextAdmission } from "./openshell-context-admission.js";

const bridgeUrl = process.env.OPENSHELL_AGENT_CONVERSATION_URL;
if (!bridgeUrl) {
	throw new Error("OPENSHELL_AGENT_CONVERSATION_URL is required");
}

const runtimeExtension: RuntimeExtension = {
	createContextAdmission: (sessionManager) =>
		createOpenShellContextAdmission(bridgeUrl, () => sessionManager.getSessionId()),
};

await runCli(process.argv.slice(2), { runtimeExtension });
