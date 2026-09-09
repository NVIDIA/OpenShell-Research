// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  formatSkillInvocation,
  type AgentTool,
  type StreamFn,
} from "@earendil-works/pi-agent-core";
import {
  isContextOverflow,
  validateToolArguments,
  type AssistantMessage,
  type Context,
  type Message,
  type Model,
  type Usage,
} from "@earendil-works/pi-ai";
import { streamSimple } from "@earendil-works/pi-ai/compat";
import {
  SessionManager,
  DefaultResourceLoader,
  SettingsManager,
  convertToLlm,
  createReadTool,
  createBashTool,
  createEditTool,
  createWriteTool,
  createGrepTool,
  createFindTool,
  createLsTool,
  createLocalBashOperations,
  formatSkillsForPrompt,
  estimateTokens,
  shouldCompact,
  generateSummaryWithUsage,
  sessionEntryToContextMessages,
  type Skill,
} from "@earendil-works/pi-coding-agent";
import { Admission, AdmissionError, RECEIPT_HEADER } from "./admission.js";

export interface SessionOptions {
  cwd: string;
  sessionDir: string;
  agentDir: string;
  model: Model<"openai-completions">;
  apiKey: string;
  admission: Admission;
  /** Public Pi stream/tool seams also permit deterministic boundary tests. */
  stream?: StreamFn;
  tools?: AgentTool[];
  compactAtTokens?: number;
}

/** The only owner of writable Pi history. Candidates stay local until approved. */
export class AdmissionSession {
  private readonly store: SessionManager;
  private readonly tools: AgentTool[];
  private readonly stream: StreamFn;
  private systemPrompt = "";
  private skills: Skill[] = [];
  private busy = false;
  private stopped = false;
  private readonly reserveTokens: number;

  private constructor(private readonly options: SessionOptions) {
    this.store = SessionManager.create(
      resolve(options.cwd),
      resolve(options.sessionDir),
    );
    this.tools = options.tools ?? projectTools(resolve(options.cwd));
    this.reserveTokens = Math.min(4096, options.model.maxTokens);
    this.stream = async (model, context, streamOptions) => {
      const receipt = await options.admission.receipt(
        context,
        streamOptions?.signal,
      );
      return (options.stream ?? streamSimple)(model, context, {
        ...streamOptions,
        apiKey: options.apiKey,
        maxRetries: 0,
        headers: { ...streamOptions?.headers, [RECEIPT_HEADER]: receipt },
      });
    };
  }

  static async create(options: SessionOptions): Promise<AdmissionSession> {
    if (
      options.model.api !== "openai-completions" ||
      options.model.reasoning ||
      options.model.input.some((type) => type !== "text") ||
      new URL(options.model.baseUrl).protocol !== "https:"
    ) {
      throw new AdmissionError("unsupported");
    }
    const session = new AdmissionSession(options);
    const resources = new DefaultResourceLoader({
      cwd: resolve(options.cwd),
      agentDir: resolve(options.agentDir),
      settingsManager: SettingsManager.inMemory({ packages: [] }),
      noExtensions: true,
      noPromptTemplates: true,
      noThemes: true,
    });
    await resources.reload();
    const skills = resources.getSkills().skills;
    const candidate = [
      "You are a coding assistant. Use the available tools to work in the project directory.",
      resources.getSystemPrompt() ?? "",
      ...resources.getAppendSystemPrompt(),
      ...resources
        .getAgentsFiles()
        .agentsFiles.map((file) => `${file.path}\n${file.content}`),
      formatSkillsForPrompt(skills),
    ].join("\n\n");
    session.systemPrompt = await options.admission.text("system", candidate);
    session.skills = skills;
    return session;
  }

  get history(): Message[] {
    return structuredClone(
      convertToLlm(this.store.buildSessionContext().messages),
    );
  }
  get entries() {
    return structuredClone(this.store.getEntries());
  }
  get sessionFile(): string {
    return this.store.getSessionFile()!;
  }
  get isStopped(): boolean {
    return this.stopped;
  }

  async prompt(input: string, signal?: AbortSignal): Promise<void> {
    this.begin();
    try {
      let text = input;
      const invocation = /^\/skill:([^\s]+)(?:\s+([\s\S]*))?$/.exec(input);
      if (invocation) {
        const skill = this.skills.find((skill) => skill.name === invocation[1]);
        if (!skill) throw new Error("Unknown project skill.");
        text = formatSkillInvocation(
          { ...skill, content: await readFile(skill.filePath, "utf8") },
          invocation[2],
        );
      }
      await this.admitAndAppend(
        { role: "user", content: text, timestamp: Date.now() },
        signal,
      );
      let retriedOverflow = false;
      for (;;) {
        const response = await (
          await this.stream(this.options.model, this.context(), {
            signal,
            maxTokens: this.reserveTokens,
          })
        ).result();
        if (isContextOverflow(response, this.options.model.contextWindow)) {
          if (retriedOverflow || !(await this.compactSession(signal)))
            throw new Error("Context is too large; start a new session.");
          retriedOverflow = true;
          continue;
        }
        if (
          response.stopReason === "error" ||
          response.stopReason === "aborted"
        )
          throw new Error(
            "Model request failed or was cancelled; no response was saved.",
          );
        const candidate: AssistantMessage = {
          role: "assistant",
          content: response.content,
          api: this.options.model.api,
          model: this.options.model.id,
          provider: this.options.model.provider,
          usage: retainedUsage(response.usage),
          stopReason: response.stopReason,
          timestamp: Date.now(),
        };
        const assistant = (await this.admitAndAppend(
          candidate,
          signal,
        )) as AssistantMessage;
        const calls = assistant.content.filter(
          (block) => block.type === "toolCall",
        );
        if (!calls.length) break;
        for (let index = 0; index < calls.length; index++) {
          const call = calls[index];
          try {
            if (assistant.stopReason === "length")
              throw new Error("Incomplete tool call.");
            const tool = this.tools.find((tool) => tool.name === call.name);
            let content:
              | { type: "text"; text: string }[]
              | Awaited<ReturnType<AgentTool["execute"]>>["content"];
            let isError = false;
            try {
              if (!tool) throw new Error("Requested tool is not available.");
              const args = validateToolArguments(tool, call);
              content = (await tool.execute(call.id, args, signal)).content;
            } catch (error) {
              isError = true;
              content = [
                {
                  type: "text",
                  text:
                    error instanceof Error
                      ? error.message
                      : "Tool execution failed.",
                },
              ];
            }
            await this.admitAndAppend(
              {
                role: "toolResult",
                toolCallId: call.id,
                toolName: call.name,
                content,
                isError,
                timestamp: Date.now(),
              },
              signal,
            );
          } catch (error) {
            // No more model calls after a rejected result. Close outstanding
            // pairs only with separately admitted, content-free failures.
            try {
              for (const pending of calls.slice(index))
                await this.admitAndAppend(
                  {
                    role: "toolResult",
                    toolCallId: pending.id,
                    toolName: pending.name,
                    content: [
                      {
                        type: "text",
                        text: "Tool result unavailable; this turn was stopped.",
                      },
                    ],
                    isError: true,
                    timestamp: Date.now(),
                  },
                  signal,
                );
            } catch {
              this.stopped = true;
            }
            throw error;
          }
        }
      }
      const tokens = this.contextTokens();
      if (
        tokens >= (this.options.compactAtTokens ?? Infinity) ||
        shouldCompact(tokens, this.options.model.contextWindow, {
          enabled: true,
          reserveTokens: this.reserveTokens,
          keepRecentTokens: 0,
        })
      )
        await this.compactSession(signal);
    } finally {
      this.busy = false;
    }
  }

  async compact(signal?: AbortSignal): Promise<boolean> {
    this.begin();
    try {
      return await this.compactSession(signal);
    } finally {
      this.busy = false;
    }
  }

  private begin(): void {
    if (this.busy || this.stopped)
      throw new Error(
        "Session is busy or stopped; start a new session if stopped.",
      );
    this.busy = true;
  }

  private context(): Context {
    return {
      systemPrompt: this.systemPrompt,
      messages: this.history,
      tools: this.tools,
    };
  }
  private contextTokens(): number {
    return this.history.reduce(
      (sum, message) => sum + estimateTokens(message),
      Math.ceil(this.systemPrompt.length / 4),
    );
  }

  private async admitAndAppend(
    candidate: Message,
    signal?: AbortSignal,
  ): Promise<Message> {
    const admitted = await this.options.admission.message(candidate, signal);
    this.store.appendMessage(admitted);
    return structuredClone(admitted);
  }

  private async compactSession(signal?: AbortSignal): Promise<boolean> {
    const entries = this.store.buildContextEntries();
    const keepIndex = entries.findLastIndex(
      (entry) => entry.type === "message" && entry.message.role === "user",
    );
    if (keepIndex <= 0) return false;
    const previous = entries.slice(0, keepIndex);
    const messages = previous.flatMap((entry) =>
      entry.type === "compaction" ? [] : sessionEntryToContextMessages(entry),
    );
    if (!messages.length) return false;
    const priorSummary = previous.find((entry) => entry.type === "compaction");
    const tokensBefore = this.contextTokens();
    const summary = await generateSummaryWithUsage(
      messages,
      this.options.model,
      this.reserveTokens,
      this.options.apiKey,
      undefined,
      signal,
      undefined,
      priorSummary?.summary,
      "off",
      this.stream,
      undefined,
      { enabled: false, maxRetries: 0, baseDelayMs: 0 },
    );
    const approved = await this.options.admission.text(
      "compaction_summary",
      summary.text,
      signal,
    );
    this.store.appendCompaction(
      approved,
      entries[keepIndex].id,
      tokensBefore,
      undefined,
      undefined,
      retainedUsage(summary.usage),
    );
    return true;
  }
}

/** Keep bash output below Pi's automatic spill-to-file threshold. */
export function projectTools(cwd: string): AgentTool[] {
  const local = createLocalBashOperations();
  const bash = createBashTool(cwd, {
    exposeSessionEnvironment: false,
    operations: {
      async exec(command, directory, options) {
        const limit = new AbortController();
        let bytes = 0;
        let lines = 0;
        const result = await local.exec(command, directory, {
          ...options,
          signal: AbortSignal.any([
            limit.signal,
            ...(options.signal ? [options.signal] : []),
          ]),
          onData(data) {
            bytes += data.length;
            lines += data.toString("utf8").split("\n").length - 1;
            if (bytes > 16_000 || lines > 1000) limit.abort();
            else if (!limit.signal.aborted) options.onData(data);
          },
        });
        if (limit.signal.aborted)
          throw new Error(
            "Bash output exceeded the example's in-memory limit.",
          );
        return result;
      },
    },
  });
  return [
    createReadTool(cwd),
    bash,
    createEditTool(cwd),
    createWriteTool(cwd),
    createGrepTool(cwd),
    createFindTool(cwd),
    createLsTool(cwd),
  ];
}

function retainedUsage(usage: Usage): Usage {
  return {
    input: usage.input,
    output: usage.output,
    cacheRead: usage.cacheRead,
    cacheWrite: usage.cacheWrite,
    totalTokens: usage.totalTokens,
    cost: {
      input: usage.cost.input,
      output: usage.cost.output,
      cacheRead: usage.cost.cacheRead,
      cacheWrite: usage.cost.cacheWrite,
      total: usage.cost.total,
    },
  };
}
