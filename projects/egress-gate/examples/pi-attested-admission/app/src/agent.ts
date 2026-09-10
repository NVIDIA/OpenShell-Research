// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  Agent,
  type AgentEvent,
  type AgentMessage,
  type AgentContext,
  type StreamFn,
} from "@earendil-works/pi-agent-core";
import {
  isContextOverflow,
  validateToolArguments,
  type AssistantMessage,
  type ImageContent,
  type Message,
  type Model,
  type ToolResultMessage,
  type Usage,
} from "@earendil-works/pi-ai";
import { convertToLlm } from "@earendil-works/pi-coding-agent";
import { Admission, AdmissionError } from "./admission.js";

export class ContextOverflowError extends Error {}

/** Own the execution loop so even pending content never enters Pi's reducer.
 * AgentSession alone persists the approved message_end events.
 */
export class AdmissionAgent extends Agent {
  private readonly live;
  private systemPromptCandidate = "";
  private approvedSystemPrompt = "";
  private readonly subscribers = new Set<
    (event: AgentEvent, signal: AbortSignal) => Promise<void> | void
  >();
  private readonly steering: AgentMessage[] = [];
  private readonly followUps: AgentMessage[] = [];
  private controller?: AbortController;
  private settled: Promise<void> = Promise.resolve();
  stopped = false;

  constructor(
    model: Model<"openai-completions">,
    streamFn: StreamFn,
    private readonly admission: Admission,
  ) {
    super({ initialState: { model, thinkingLevel: "off" }, streamFn });
    // Pi's base lifecycle fields are readonly. This engine owns its own public
    // state and lifecycle; it never invokes the base execution/state reducer.
    const owner = this;
    this.live = {
      ...super.state,
      // Pi rebuilds this field synchronously. Stage those writes as candidates;
      // public state continues to expose only the last approved system prompt.
      get systemPrompt(): string {
        return owner.approvedSystemPrompt;
      },
      set systemPrompt(value: string) {
        owner.systemPromptCandidate = value;
      },
      pendingToolCalls: new Set<string>(),
    };
  }

  async approveSystemPrompt(signal?: AbortSignal): Promise<string> {
    const approved = await this.admission.text(
      "system",
      this.systemPromptCandidate,
      signal,
    );
    signal?.throwIfAborted();
    this.approvedSystemPrompt = approved;
    return approved;
  }

  override get state() {
    return this.live;
  }
  override get signal() {
    return this.controller?.signal;
  }
  override subscribe(
    listener: (event: AgentEvent, signal: AbortSignal) => Promise<void> | void,
  ) {
    this.subscribers.add(listener);
    return () => {
      this.subscribers.delete(listener);
    };
  }
  override abort() {
    this.controller?.abort();
  }
  override waitForIdle() {
    return this.settled;
  }
  override steer(message: AgentMessage) {
    this.steering.push(message);
  }
  override followUp(message: AgentMessage) {
    this.followUps.push(message);
  }
  override clearSteeringQueue() {
    this.steering.length = 0;
  }
  override clearFollowUpQueue() {
    this.followUps.length = 0;
  }
  override clearAllQueues() {
    this.clearSteeringQueue();
    this.clearFollowUpQueue();
  }
  override hasQueuedMessages() {
    return this.steering.length + this.followUps.length > 0;
  }
  override reset() {
    if (this.live.isStreaming)
      throw new Error("Cancel the current operation first.");
    this.live.messages = [];
    this.live.errorMessage = undefined;
    this.stopped = false;
    this.clearAllQueues();
  }
  override prompt(
    input: string | AgentMessage | AgentMessage[],
    images?: ImageContent[],
  ): Promise<void> {
    if (images?.length)
      return Promise.reject(new AdmissionError("unsupported"));
    const messages: AgentMessage[] =
      typeof input === "string"
        ? [{ role: "user", content: input, timestamp: Date.now() }]
        : Array.isArray(input)
          ? input
          : [input];
    return this.run(messages);
  }
  override continue(): Promise<void> {
    const last = this.live.messages.at(-1);
    if (
      !this.hasQueuedMessages() &&
      last?.role !== "user" &&
      last?.role !== "toolResult"
    )
      return Promise.reject(
        new Error("There is no unfinished turn to continue."),
      );
    return this.run([]);
  }

  private async run(candidates: AgentMessage[]): Promise<void> {
    if (this.live.isStreaming || this.stopped)
      throw new Error("Session is busy or stopped; use /new if stopped.");
    this.controller = new AbortController();
    this.live.isStreaming = true;
    this.live.errorMessage = undefined;
    let settle!: () => void;
    this.settled = new Promise<void>((resolve) => {
      settle = resolve;
    });
    const published: AgentMessage[] = [];
    try {
      await this.emit({ type: "agent_start" });
      await this.admitBatch(candidates, published);
      const steered = await this.drain(
        this.steering,
        this.steeringMode,
        published,
      );
      if (!candidates.length && !steered)
        await this.drain(this.followUps, this.followUpMode, published);
      for (;;) {
        this.signal!.throwIfAborted();
        await this.emit({ type: "turn_start" });
        // AgentSession rebuilds system context when tools/settings change.
        // Approve that snapshot before every provider call.
        const systemPrompt = await this.approveSystemPrompt(this.signal);
        const response = await (
          await this.streamFunction(
            this.live.model,
            {
              systemPrompt,
              messages: convertToLlm(this.live.messages),
              tools: this.live.tools,
            },
            { signal: this.signal, sessionId: this.sessionId },
          )
        ).result();
        this.signal!.throwIfAborted();
        if (isContextOverflow(response, this.live.model.contextWindow))
          throw new ContextOverflowError("Context is too large.");
        if (
          response.stopReason === "error" ||
          response.stopReason === "aborted"
        )
          throw new Error(
            "Model request failed or was cancelled; no response was saved.",
          );
        const assistant = (await this.admit({
          role: "assistant",
          content: response.content,
          api: this.live.model.api,
          provider: this.live.model.provider,
          model: this.live.model.id,
          usage: retainedUsage(response.usage),
          stopReason: response.stopReason,
          timestamp: Date.now(),
        })) as AssistantMessage;
        await this.publish(assistant, published);
        const calls = assistant.content.filter(
          (block) => block.type === "toolCall",
        );
        const toolResults: ToolResultMessage[] = [];
        for (let index = 0; index < calls.length; index++) {
          const call = calls[index];
          try {
            if (assistant.stopReason === "length")
              throw new Error("Incomplete tool call.");
            this.signal!.throwIfAborted();
            const tool = this.live.tools.find(
              (tool) => tool.name === call.name,
            );
            let args: unknown = call.arguments;
            let result;
            let isError = false;
            this.live.pendingToolCalls.add(call.id);
            await this.emit({
              type: "tool_execution_start",
              toolCallId: call.id,
              toolName: call.name,
              args,
            });
            try {
              if (!tool) throw new Error("Requested tool is not available.");
              args = validateToolArguments(tool, call);
              const before = await this.beforeToolCall?.(
                {
                  assistantMessage: assistant,
                  toolCall: call,
                  args,
                  context: this.context(),
                },
                this.signal,
              );
              if (before?.block)
                throw new Error(before.reason ?? "Tool execution blocked.");
              // No onUpdate callback: partial tool output is not approved yet.
              result = await tool.execute(call.id, args, this.signal);
            } catch (error) {
              isError = true;
              result = {
                content: [
                  {
                    type: "text" as const,
                    text:
                      error instanceof Error
                        ? error.message
                        : "Tool execution failed.",
                  },
                ],
                details: undefined,
              };
            }
            const after = await this.afterToolCall?.(
              {
                assistantMessage: assistant,
                toolCall: call,
                args,
                result,
                isError,
                context: this.context(),
              },
              this.signal,
            );
            const approved = (await this.admit({
              role: "toolResult",
              toolCallId: call.id,
              toolName: call.name,
              content: after?.content ?? result.content,
              isError: after?.isError ?? isError,
              timestamp: Date.now(),
            })) as ToolResultMessage;
            await this.publishTool(approved, published);
            toolResults.push(approved);
          } catch (error) {
            // Close outstanding pairs with separately admitted, content-free
            // failures. If admission is unavailable, require a new session.
            try {
              for (const pending of calls.slice(index)) {
                const approved = (await this.admit({
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
                })) as ToolResultMessage;
                await this.publishTool(approved, published);
              }
            } catch {
              this.stopped = true;
            }
            throw error;
          }
        }
        await this.emit({ type: "turn_end", message: assistant, toolResults });
        const steered = await this.drain(
          this.steering,
          this.steeringMode,
          published,
        );
        const followedUp =
          !calls.length &&
          !steered &&
          (await this.drain(this.followUps, this.followUpMode, published));
        if (!calls.length && !steered && !followedUp) break;
        // Native automatic compaction between tool turns uses the same
        // session_before_compact admission hook as manual compaction.
        await this.prepareNextTurnWithContext?.(
          {
            message: assistant,
            toolResults,
            context: this.context(),
            newMessages: published,
          },
          this.signal,
        );
      }
    } catch (error) {
      this.clearAllQueues();
      // Never turn an unchecked exception or partial provider response into a
      // persisted assistant error message.
      this.live.errorMessage =
        error instanceof AdmissionError
          ? error.message
          : "Operation stopped; no unchecked content was saved.";
      if (
        error instanceof AdmissionError ||
        error instanceof ContextOverflowError
      )
        throw error;
      throw new Error(this.live.errorMessage);
    } finally {
      try {
        await this.emit({ type: "agent_end", messages: published });
      } finally {
        this.live.pendingToolCalls.clear();
        this.live.isStreaming = false;
        this.controller = undefined;
        settle();
      }
    }
  }

  private context(): AgentContext {
    return {
      systemPrompt: this.live.systemPrompt,
      messages: this.live.messages.slice(),
      tools: this.live.tools,
    };
  }
  private async admit(candidate: AgentMessage): Promise<Message> {
    if (
      candidate.role !== "user" &&
      candidate.role !== "assistant" &&
      candidate.role !== "toolResult"
    )
      throw new AdmissionError("unsupported");
    return this.admission.message(candidate, this.signal);
  }
  private async admitBatch(
    candidates: AgentMessage[],
    published: AgentMessage[],
  ) {
    const approved = [];
    for (const candidate of candidates)
      approved.push(await this.admit(candidate));
    this.signal!.throwIfAborted();
    for (const message of approved) await this.publish(message, published);
  }
  private async drain(
    queue: AgentMessage[],
    mode: string,
    published: AgentMessage[],
  ): Promise<boolean> {
    if (!queue.length) return false;
    const candidates = queue.splice(0, mode === "all" ? queue.length : 1);
    await this.admitBatch(candidates, published);
    return true;
  }
  private async publish(message: Message, published: AgentMessage[]) {
    this.live.messages = [...this.live.messages, message];
    published.push(message);
    await this.emit({ type: "message_start", message });
    await this.emit({ type: "message_end", message });
  }
  private async publishTool(
    message: ToolResultMessage,
    published: AgentMessage[],
  ) {
    this.live.pendingToolCalls.delete(message.toolCallId);
    // Drop unchecked details (including edit diffs) and usage metadata.
    await this.emit({
      type: "tool_execution_end",
      toolCallId: message.toolCallId,
      toolName: message.toolName,
      result: { content: message.content },
      isError: message.isError,
    });
    await this.publish(message, published);
  }
  private async emit(event: AgentEvent) {
    for (const subscriber of this.subscribers)
      await subscriber(event, this.signal!);
  }
}

export function retainedUsage(usage: Usage): Usage {
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
