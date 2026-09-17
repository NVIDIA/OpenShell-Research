// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { isDeepStrictEqual } from "node:util";
import {
  Agent,
  type AgentEvent,
  type AgentMessage,
  type StreamFn,
} from "@earendil-works/pi-agent-core";
import {
  clampThinkingLevel,
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
  private readonly subscribers = new Set<
    (event: AgentEvent, signal: AbortSignal) => Promise<void> | void
  >();
  private controller?: AbortController;
  private settled: Promise<void> = Promise.resolve();
  stopped = false;

  constructor(
    model: Model<"openai-completions">,
    streamFn: StreamFn,
    private readonly admission: Admission,
  ) {
    // Match Pi's default thinking level; the session's native controls can change it.
    super({
      initialState: { model, thinkingLevel: clampThinkingLevel(model, "medium") },
      streamFn,
    });
    // Pi's session persists only the approved events emitted by this loop.
    this.live = {
      ...super.state,
      pendingToolCalls: new Set<string>(),
    };
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
    void message;
    throw new Error("Agent is busy; wait or cancel the active turn.");
  }
  override followUp(message: AgentMessage) {
    void message;
    throw new Error("Agent is busy; wait or cancel the active turn.");
  }
  override clearSteeringQueue() {}
  override clearFollowUpQueue() {}
  override clearAllQueues() {}
  override hasQueuedMessages() {
    return false;
  }
  override reset() {
    if (this.live.isStreaming)
      throw new Error("Cancel the current operation first.");
    this.live.messages = [];
    this.live.errorMessage = undefined;
    this.stopped = false;
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
    return Promise.reject(new Error("Automatic continuation is disabled."));
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
      for (;;) {
        this.signal!.throwIfAborted();
        await this.emit({ type: "turn_start" });
        const response = await (
          await this.streamFunction(
            this.live.model,
            {
              systemPrompt: this.live.systemPrompt,
              messages: convertToLlm(this.live.messages),
              tools: this.live.tools,
            },
            {
              signal: this.signal,
              sessionId: this.sessionId,
              reasoning: this.live.thinkingLevel === "off" ? undefined : this.live.thinkingLevel,
            },
          )
        ).result();
        this.signal!.throwIfAborted();
        if (isContextOverflow(response, this.live.model.contextWindow))
          throw new ContextOverflowError(
            "Context is too large; run /compact and retry.",
          );
        if (
          response.stopReason === "error" ||
          response.stopReason === "aborted"
        )
          throw new Error(
            "Model request failed or was cancelled; no response was saved.",
          );
        const assistant = (await this.admit(response)) as AssistantMessage;
        const calls = assistant.content.filter(
          (block) => block.type === "toolCall",
        );
        const toolResults: ToolResultMessage[] = [];
        for (const call of calls) {
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
              // Pi's edit tool normalizes common model argument shapes in place.
              // Keep that preparation separate from the already-approved message.
              const prepared = structuredClone(call);
              prepared.arguments = (tool.prepareArguments
                ? tool.prepareArguments(prepared.arguments)
                : prepared.arguments) as typeof prepared.arguments;
              args = validateToolArguments(tool, prepared);
              if (!isDeepStrictEqual(args, call.arguments)) {
                const checked = (await this.admit({
                  ...assistant,
                  content: assistant.content.map((block) =>
                    block.type === "toolCall" && block.id === call.id
                      ? { ...block, arguments: args as typeof block.arguments }
                      : block,
                  ),
                })) as AssistantMessage;
                const checkedCall = checked.content.find(
                  (block) => block.type === "toolCall" && block.id === call.id,
                );
                if (
                  checkedCall?.type !== "toolCall" ||
                  !isDeepStrictEqual(checkedCall.arguments, args)
                )
                  throw new AdmissionError("invalid");
              }
              // No onUpdate callback: partial tool output is not approved yet.
              result = await tool.execute(call.id, args, this.signal);
            } catch (error) {
              this.signal!.throwIfAborted();
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
            const approved = (await this.admit({
              role: "toolResult",
              toolCallId: call.id,
              toolName: call.name,
              content: result.content,
              isError: isError,
              timestamp: Date.now(),
            })) as ToolResultMessage;
            toolResults.push(approved);
          } catch (error) {
            // Tool effects cannot be rolled back. Keep the incomplete batch out
            // of history and stop instead of attempting replay.
            if (!this.signal!.aborted) this.stopped = true;
            throw error;
          }
        }
        this.signal!.throwIfAborted();
        await this.publish(assistant, published);
        for (const result of toolResults)
          await this.publishTool(result, published);
        await this.emit({ type: "turn_end", message: assistant, toolResults });
        if (!calls.length) break;
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
