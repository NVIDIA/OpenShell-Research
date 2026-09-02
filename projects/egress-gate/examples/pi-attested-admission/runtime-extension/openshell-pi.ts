import { closeSync, readFileSync } from "node:fs";
import { runCli, type RuntimeExtension } from "@earendil-works/pi-coding-agent";

import { createOpenShellContextAdmission } from "./openshell-context-admission.js";

const bridgeUrl = process.env.OPENSHELL_AGENT_CONVERSATION_URL;
if (!bridgeUrl) {
	throw new Error("OPENSHELL_AGENT_CONVERSATION_URL is required");
}

const runtimeExtension = createRuntimeExtension(bridgeUrl, readAdmissionToken());

await runCli(process.argv.slice(2), { runtimeExtension });

function createRuntimeExtension(bridgeUrl: string, admissionToken: string): RuntimeExtension {
	return {
		createContextAdmission: (sessionManager) =>
			createOpenShellContextAdmission(bridgeUrl, () => sessionManager.getSessionId(), admissionToken),
	};
}

function readAdmissionToken(): string {
	const tokenFdValue = process.env.OPENSHELL_AGENT_ADMISSION_TOKEN_FD;
	delete process.env.OPENSHELL_AGENT_ADMISSION_TOKEN_FD;
	if (!tokenFdValue || !/^\d+$/.test(tokenFdValue)) {
		throw new Error("OPENSHELL_AGENT_ADMISSION_TOKEN_FD must name a readable file descriptor");
	}

	const tokenFd = Number(tokenFdValue);
	let admissionToken: string;
	try {
		admissionToken = readFileSync(tokenFd, "utf8");
	} catch (cause) {
		try {
			closeSync(tokenFd);
		} catch {}
		throw new Error("Could not read the OpenShell agent admission token", { cause });
	}
	try {
		closeSync(tokenFd);
	} catch (cause) {
		throw new Error("Could not close the OpenShell agent admission token descriptor", { cause });
	}
	if (!/^[A-Za-z0-9_-]{43}$/.test(admissionToken)) {
		throw new Error("OpenShell supplied an invalid agent admission token");
	}
	return admissionToken;
}
