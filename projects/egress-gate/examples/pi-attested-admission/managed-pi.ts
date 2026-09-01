/** Standard Pi CLI with mandatory OpenShell context admission. */
import { closeSync, readFileSync } from "node:fs";
import { main } from "@earendil-works/pi-coding-agent";
import { createOpenShellContextAdmission } from "./managed-pi-admission.ts";

async function run(): Promise<void> {
	const bridgeUrl = process.env.OPENSHELL_AGENT_CONVERSATION_URL;
	const provider = process.env.PI_MANAGED_PROVIDER;
	if (!bridgeUrl || !provider) {
		throw new Error("OPENSHELL_AGENT_CONVERSATION_URL and PI_MANAGED_PROVIDER are required");
	}

	const modelApiKey = readFileSync(3, "utf8").replace(/\n$/u, "");
	closeSync(3);

	await main(process.argv.slice(2), {
		configureModelRuntime: async (modelRuntime) => {
			await modelRuntime.setRuntimeApiKey(provider, modelApiKey);
		},
		createContextAdmission: (sessionManager) =>
			createOpenShellContextAdmission(bridgeUrl, () => sessionManager.getSessionId()),
	});
}

run().catch((error: unknown) => {
	console.error(error);
	process.exitCode = 1;
});
