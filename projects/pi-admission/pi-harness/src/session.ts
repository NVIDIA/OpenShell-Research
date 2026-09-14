// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { resolve } from "node:path";
import type { StreamFn } from "@earendil-works/pi-agent-core";
import { InMemoryCredentialStore, type Model } from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import {
  AgentSession,
  AgentSessionRuntime,
  SessionManager,
  SettingsManager,
  ModelRuntime,
  createAgentSessionServices,
  convertToLlm,
  compact,
  type CreateAgentSessionRuntimeFactory,
  type PromptOptions,
} from "@earendil-works/pi-coding-agent";
import { Admission, AdmissionError, RECEIPT_HEADER } from "./admission.js";
import {
  AdmissionAgent,
  ContextOverflowError,
  retainedUsage,
} from "./agent.js";
import { projectTools } from "./tools.js";

export { projectTools } from "./tools.js";

export interface SessionOptions {
  cwd: string;
  sessionDir: string;
  agentDir: string;
  model: Model<"openai-completions">;
  apiKey: string;
  admission: Admission;
  /** Deterministic integration tests use Pi's public stream/tool seams. */
  stream?: StreamFn;
  compactAtTokens?: number;
}

/** Native Pi session/persistence, with explicit guards for unsupported writes. */
export class AdmissionSession extends AgentSession {
  static async create(options: SessionOptions): Promise<AdmissionSession> {
    const result = await sessionFactory(options)({
      cwd: resolve(options.cwd),
      agentDir: resolve(options.agentDir),
      sessionManager: SessionManager.create(
        resolve(options.cwd),
        resolve(options.sessionDir),
      ),
    });
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
    if (this.isStopped)
      throw new Error("An unfinished tool batch requires /new.");
    try {
      await super.prompt(text, options);
    } catch (error) {
      if (!(error instanceof ContextOverflowError) || !this.autoCompactionEnabled)
        throw error;
      // The failed provider response was never published. Compact only approved
      // history, then retry that unfinished turn once.
      await this.compact();
      await this.agent.continue();
    } finally {
      if (!this.isStreaming) this.clearQueue();
    }
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
    return unsupported("Resource reload; use /new");
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

/** Use Pi's real TUI runtime; /new is safe, importing unchecked history is not. */
export async function createAdmissionRuntime(
  options: SessionOptions,
): Promise<AgentSessionRuntime> {
  const factory = sessionFactory(options);
  const result = await factory({
    cwd: resolve(options.cwd),
    agentDir: resolve(options.agentDir),
    sessionManager: SessionManager.create(
      resolve(options.cwd),
      resolve(options.sessionDir),
    ),
  });
  return new AdmissionRuntime(
    result.session,
    result.services,
    factory,
    result.diagnostics,
  );
}

function sessionFactory(options: SessionOptions) {
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
      enabled: true,
      reserveTokens:
        options.compactAtTokens === undefined
          ? undefined
          : options.model.contextWindow - options.compactAtTokens,
    },
    retry: { enabled: false },
  });
  const stream: StreamFn = async (model, context, streamOptions) => {
    const receipt = await options.admission.receipt(
      context,
      streamOptions?.signal,
    );
    return (options.stream ?? streamSimple)(model, context, {
      ...streamOptions,
      apiKey: options.apiKey,
      headers: { ...streamOptions?.headers, [RECEIPT_HEADER]: receipt },
    });
  };
  return async ({
    cwd,
    agentDir,
    sessionManager,
    sessionStartEvent,
  }: Parameters<CreateAgentSessionRuntimeFactory>[0]) => {
    if (sessionManager.getEntries().length)
      return unsupported("Restoring existing history");
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
    const tools = projectTools(cwd);
    const agent = new AdmissionAgent(options.model, stream, options.admission);
    agent.sessionId = sessionManager.getSessionId();
    agent.steeringMode = settingsManager.getSteeringMode();
    agent.followUpMode = settingsManager.getFollowUpMode();
    const session = new AdmissionSession({
      agent,
      cwd,
      sessionManager,
      sessionStartEvent,
      settingsManager: services.settingsManager,
      resourceLoader: services.resourceLoader,
      modelRuntime: services.modelRuntime,
      baseToolsOverride: Object.fromEntries(
        tools.map((tool) => [tool.name, tool]),
      ),
      initialActiveToolNames: tools.map((tool) => tool.name),
      allowedToolNames: tools.map((tool) => tool.name),
    });
    // Check project instructions and skill metadata before exposing the session.
    await agent.approveSystemPrompt();
    return {
      session,
      services,
      diagnostics: services.diagnostics,
      extensionsResult: services.resourceLoader.getExtensions(),
    };
  };
}

class AdmissionRuntime extends AgentSessionRuntime {
  override async switchSession(): Promise<never> {
    return unsupported("Resume");
  }
  override async importFromJsonl(): Promise<never> {
    return unsupported("Import");
  }
  override async fork(): Promise<never> {
    return unsupported("Fork");
  }
}

function unsupported(feature: string): never {
  throw new Error(`${feature} is not supported by this admission example.`);
}
