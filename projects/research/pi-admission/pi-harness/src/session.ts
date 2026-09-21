// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { resolve } from "node:path";
import type { StreamFn } from "@earendil-works/pi-agent-core";
import { InMemoryCredentialStore, type Model } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import { Text } from "@earendil-works/pi-tui";
import {
  AgentSession,
  AgentSessionRuntime,
  SessionManager,
  SettingsManager,
  ModelRuntime,
  createAgentSessionServices,
  convertToLlm,
  compact,
  type PromptOptions,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { Admission, AdmissionError } from "./admission.js";
import { AdmissionAgent, retainedUsage } from "./agent.js";

export interface SessionOptions {
  cwd: string;
  sessionDir: string;
  agentDir: string;
  model: Model<"openai-completions">;
  apiKey: string;
  admission: Admission;
  /** Deterministic integration tests use Pi's public stream/tool seams. */
  stream?: StreamFn;
}

/** Native Pi session/persistence, with explicit guards for unsupported writes. */
export class AdmissionSession extends AgentSession {
  static async create(options: SessionOptions): Promise<AdmissionSession> {
    const result = await createSession(options);
    await result.session.bindExtensions({});
    return result.session;
  }

  get history() {
    return structuredClone(convertToLlm(this.messages));
  }
  get entries() {
    return structuredClone(this.sessionManager.getEntries());
  }
  override get sessionFile(): string {
    return this.sessionManager.getSessionFile()!;
  }
  get isStopped() {
    return (this.agent as AdmissionAgent).stopped;
  }

  override async prompt(text: string, options?: PromptOptions): Promise<void> {
    if (this.isStreaming) {
      if (this.extensionRunner.hasUI()) {
        this.extensionRunner.getUIContext().notify(
          "Agent is busy; wait or cancel the active turn.", "warning",
        );
        return;
      }
      throw new Error("Agent is busy; wait or cancel the active turn.");
    }
    if (this.isStopped)
      throw new Error("The session stopped after an incomplete tool batch; restart Pi.");
    await super.prompt(text, { ...options, streamingBehavior: undefined });
  }

  override getToolDefinition(name: string): ToolDefinition | undefined {
    const definition = super.getToolDefinition(name);
    if (name !== "edit" || !definition) return definition;
    // Pi's edit preview rereads the file, outside admission and after execution.
    // Render only the admitted arguments/result; keep native tool execution.
    return {
      ...definition,
      renderShell: "default",
      renderCall: (args) =>
        new Text(`edit ${(args as { path?: string }).path ?? ""}`, 0, 0),
      renderResult: (result) =>
        new Text(result.content.filter((part) => part.type === "text")
          .map((part) => part.text).join("\n"), 0, 0),
    };
  }

  // These native entry points write outside the agent's message event path.
  // Keep them unavailable until each has its own pre-write admission boundary.
  override async executeBash(): Promise<never> {
    return unsupported("Direct ! commands; ask the model to use the bash tool");
  }
  override recordBashResult(): never {
    return unsupported("Direct shell results");
  }
  override async sendCustomMessage(): Promise<never> {
    return unsupported("Custom extension messages");
  }
  override async navigateTree(): Promise<never> {
    return unsupported("Session branching");
  }
  override async reload(): Promise<never> {
    return unsupported("Resource reload; restart the launcher");
  }
  override async setModel(): Promise<never> {
    return unsupported("Model switching");
  }
  override async cycleModel(): Promise<never> {
    return unsupported("Model switching");
  }
  override setSessionName(): never {
    return unsupported("Session renaming");
  }
}

/** Use Pi's real TUI runtime without importing unchecked history. */
export async function createAdmissionRuntime(
  options: SessionOptions,
): Promise<AgentSessionRuntime> {
  const result = await createSession(options);
  return new AdmissionRuntime(
    result.session,
    result.services,
    async () => unsupported("Session replacement"),
    result.diagnostics,
  );
}

async function createSession(options: SessionOptions) {
  if (
    options.model.api !== "openai-completions" ||
    options.model.input.some((type) => type !== "text") ||
    new URL(options.model.baseUrl).protocol !== "https:"
  )
    throw new AdmissionError("unsupported");
  // Preferences belong to the runtime, not to an individual conversation.
  const settingsManager = SettingsManager.inMemory({
    packages: [],
    enableInstallTelemetry: false,
    compaction: {
      enabled: false,
    },
    retry: { enabled: false },
  });
  const stream: StreamFn = (model, context, streamOptions) =>
    (options.stream ?? streamSimple)(model, context, {
      ...streamOptions,
      apiKey: options.apiKey,
    });
  const cwd = resolve(options.cwd);
  const agentDir = resolve(options.agentDir);
  const sessionManager = SessionManager.create(cwd, resolve(options.sessionDir));
  const services = await createAgentSessionServices({
    cwd,
    agentDir,
    // OpenShell supplies runtime credentials; /app remains read-only.
    modelRuntime: await ModelRuntime.create({
      credentials: new InMemoryCredentialStore(),
      modelsPath: null,
    }),
    settingsManager,
    resourceLoaderOptions: {
      noExtensions: true,
      noSkills: true,
      noContextFiles: true,
      noPromptTemplates: true,
      noThemes: true,
      extensionFactories: [
        {
          name: "admission",
          factory: (pi) => {
            pi.on("session_before_compact", async (event) => {
              // Supplying a summary or explicitly cancelling is mandatory:
              // throwing from an extension handler could fall back to Pi's
              // unchecked default summarizer.
              try {
                const summary = await compact(
                  event.preparation,
                  options.model,
                  options.apiKey,
                  undefined,
                  event.customInstructions,
                  event.signal,
                  session.thinkingLevel,
                  stream,
                  undefined,
                  { enabled: false, maxRetries: 0, baseDelayMs: 0 },
                  undefined,
                  sessionManager.getSessionId(),
                );
                const approved = await options.admission.text(
                  "compaction_summary",
                  summary.summary,
                  event.signal,
                );
                return {
                  compaction: {
                    summary: approved,
                    firstKeptEntryId: summary.firstKeptEntryId,
                    tokensBefore: event.preparation.tokensBefore,
                    ...(summary.usage
                      ? { usage: retainedUsage(summary.usage) }
                      : {}),
                  },
                };
              } catch {
                return { cancel: true };
              }
            });
          },
        },
      ],
    },
  });
  services.modelRuntime.registerProvider(options.model.provider, {
    api: options.model.api,
    baseUrl: options.model.baseUrl,
    models: [options.model],
  });
  await services.modelRuntime.setRuntimeApiKey(
    options.model.provider,
    options.apiKey,
  );
  const agent = new AdmissionAgent(options.model, stream, options.admission);
  agent.sessionId = sessionManager.getSessionId();
  const session = new AdmissionSession({
    agent,
    cwd,
    sessionManager,
    settingsManager: services.settingsManager,
    resourceLoader: services.resourceLoader,
    modelRuntime: services.modelRuntime,
  });
  return {
    session,
    services,
    diagnostics: services.diagnostics,
  };
}

class AdmissionRuntime extends AgentSessionRuntime {
  override async newSession() {
    return this.cancelChange("New sessions; restart the launcher");
  }
  override async switchSession() {
    return this.cancelChange("Resume");
  }
  override async importFromJsonl() {
    return this.cancelChange("Import");
  }
  override async fork() {
    return this.cancelChange("Fork");
  }
  private cancelChange(feature: string) {
    this.session.extensionRunner.getUIContext().notify(
      `${feature} is not supported by this admission example.`, "warning",
    );
    return { cancelled: true };
  }
}

function unsupported(feature: string): never {
  throw new Error(`${feature} is not supported by this admission example.`);
}
