/** Normal interactive Pi with mandatory OpenShell context admission. */
import {
	type CreateAgentSessionRuntimeFactory,
	InteractiveMode,
	ModelRuntime,
	SessionManager,
	createAgentSessionFromServices,
	createAgentSessionRuntime,
	createAgentSessionServices,
} from "@earendil-works/pi-coding-agent";
import { createOpenShellContextAdmission } from "./managed-pi-admission.ts";

async function main(): Promise<void> {
	const bridgeUrl = process.env.OPENSHELL_AGENT_CONVERSATION_URL;
	const agentDir = process.env.PI_CODING_AGENT_DIR;
	const provider = process.env.PI_MANAGED_PROVIDER;
	const modelId = process.env.PI_MANAGED_MODEL;
	if (!bridgeUrl || !agentDir || !provider || !modelId) {
		throw new Error(
			"OPENSHELL_AGENT_CONVERSATION_URL, PI_CODING_AGENT_DIR, PI_MANAGED_PROVIDER, and PI_MANAGED_MODEL are required",
		);
	}

	const sessionManager = SessionManager.inMemory(process.cwd());
	const contextAdmission = createOpenShellContextAdmission(bridgeUrl, () => sessionManager.getSessionId());
	const modelRuntime = await ModelRuntime.create({
		authPath: `${agentDir}/auth.json`,
		modelsPath: `${agentDir}/models.json`,
		refreshOnCreate: false,
	});
	const model = modelRuntime.getModel(provider, modelId);
	if (!model) throw new Error(`Model ${provider}/${modelId} was not found`);

	const createRuntime: CreateAgentSessionRuntimeFactory = async ({ cwd, sessionManager, sessionStartEvent }) => {
		const services = await createAgentSessionServices({
			cwd,
			agentDir,
			modelRuntime,
			resourceLoaderOptions: { noExtensions: true },
		});
		return {
			...(await createAgentSessionFromServices({
				services,
				sessionManager,
				sessionStartEvent,
				model,
				thinkingLevel: "off",
				contextAdmission,
			})),
			services,
			diagnostics: services.diagnostics,
		};
	};
	const runtime = await createAgentSessionRuntime(createRuntime, {
		cwd: process.cwd(),
		agentDir,
		sessionManager,
	});
	await new InteractiveMode(runtime, { startupDiagnostics: [...runtime.diagnostics] }).run();
}

main().catch((error: unknown) => {
	console.error(error);
	process.exitCode = 1;
});
