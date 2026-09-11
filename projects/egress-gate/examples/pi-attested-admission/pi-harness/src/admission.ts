// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import type {
  Context,
  Message,
  TextContent,
} from "@earendil-works/pi-ai";

export const RECEIPT_HEADER = "x-egress-admission";
export const MAX_ADMISSION_BYTES = 4 * 1024 * 1024;

export type TextOrigin = "user" | "system" | "compaction_summary";
export type AdmissionKind =
  | "user_message"
  | "system_context"
  | "compaction_summary"
  | "assistant_message"
  | "tool_result"
  | "provider_context";
export type AdmissionResponse = {
  decision: "allow" | "replace" | "deny";
  replacement: Record<string, unknown> | null;
  receipt: string | null;
};
export type Evaluate = (
  kind: AdmissionKind,
  body: Record<string, unknown>,
  signal?: AbortSignal,
) => Promise<AdmissionResponse>;

export class AdmissionError extends Error {
  constructor(
    readonly kind: "denied" | "unavailable" | "unsupported" | "invalid",
  ) {
    super(
      {
        denied:
          "Admission denied this content; the candidate was not added to history.",
        unavailable:
          "Admission is unavailable; no unchecked content will be added.",
        unsupported:
          "This content is outside the example’s supported Chat Completions format.",
        invalid:
          "Admission returned an inconsistent result; the operation was stopped.",
      }[kind],
    );
  }
}

export function createHttpEvaluator(
  url: string,
  credential: string,
  sessionId: string,
): Evaluate {
  if (new URL(url).protocol !== "https:")
    throw new Error("Admission requires HTTPS.");
  return async (kind, body, signal) => {
    const encoded = JSON.stringify({
      kind,
      body,
      session_id: sessionId,
      submission_id: randomUUID(),
    });
    if (Buffer.byteLength(encoded) > MAX_ADMISSION_BYTES)
      throw new AdmissionError("unsupported");
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          authorization: `Bearer ${credential}`,
          "content-type": "application/json",
        },
        body: encoded,
        signal: AbortSignal.any([
          AbortSignal.timeout(30_000),
          ...(signal ? [signal] : []),
        ]),
      });
      if (!response.ok) throw new AdmissionError("unavailable");
      const encodedResult = await response.text();
      if (Buffer.byteLength(encodedResult) > MAX_ADMISSION_BYTES + 16_384)
        throw new AdmissionError("invalid");
      const result: unknown = JSON.parse(encodedResult);
      if (
        !isRecord(result) ||
        !["allow", "replace", "deny"].includes(String(result.decision))
      )
        throw new AdmissionError("invalid");
      if (result.decision === "deny")
        return { decision: "deny", replacement: null, receipt: null };
      if (result.decision === "allow" && result.replacement !== null)
        throw new AdmissionError("invalid");
      if (result.decision === "replace" && !isRecord(result.replacement))
        throw new AdmissionError("invalid");
      if (kind === "provider_context") {
        if (
          typeof result.receipt !== "string" ||
          !/^[A-Za-z0-9_-]+={0,2}$/.test(result.receipt) ||
          result.receipt.length > 11_000
        )
          throw new AdmissionError("invalid");
      } else if (result.receipt !== null) throw new AdmissionError("invalid");
      return {
        decision: result.decision as "allow" | "replace",
        replacement: result.replacement as Record<string, unknown> | null,
        receipt: result.receipt as string | null,
      };
    } catch (error) {
      if (error instanceof AdmissionError) throw error;
      throw new AdmissionError("unavailable");
    }
  };
}

export class Admission {
  constructor(private readonly evaluate: Evaluate) {}

  async text(
    origin: TextOrigin,
    text: string,
    signal?: AbortSignal,
  ): Promise<string> {
    const kind = {
      user: "user_message",
      system: "system_context",
      compaction_summary: "compaction_summary",
    } as const;
    const envelope = {
      schema_version: "openshell.pi-message.v1",
      origin,
      text,
    };
    const admitted = await this.apply(kind[origin], envelope, signal);
    if (
      admitted.origin !== origin ||
      admitted.schema_version !== envelope.schema_version ||
      typeof admitted.text !== "string"
    )
      throw new AdmissionError("invalid");
    return admitted.text;
  }

  async message(message: Message, signal?: AbortSignal): Promise<Message> {
    if (message.role === "user") {
      const original = textOnly(message.content);
      const approved = await this.text("user", original, signal);
      if (approved === original) return message;
      if (typeof message.content === "string")
        return { ...message, content: approved };
      if (message.content.length !== 1 || message.content[0].type !== "text" ||
          message.content[0].textSignature)
        throw new AdmissionError("invalid");
      return { ...message, content: [{ ...message.content[0], text: approved }] };
    }
    if (message.role === "assistant") {
      if (message.content.some((block) =>
        block.type !== "text" && block.type !== "toolCall" && block.type !== "thinking"))
        throw new AdmissionError("unsupported");
      const texts = message.content.filter((block) => block.type === "text");
      const thinking = message.content.filter((block) => block.type === "thinking");
      const calls = message.content.filter((block) => block.type === "toolCall").map((call) => ({
        id: call.id, name: call.name, arguments: call.arguments,
        thought_signature: call.thoughtSignature ?? null,
      }));
      const envelope = {
        schema_version: "openshell.pi-assistant-message.v1",
        text: texts.map((block) => block.text).join("\n"),
        tool_calls: calls,
        thinking: thinking.map((block) => ({
          text: block.thinking, signature: block.thinkingSignature ?? null,
        })),
      };
      const admitted = await this.apply("assistant_message", envelope, signal);
      // Preserve the complete native message on allow, including block order,
      // signatures, usage and provider metadata.
      if (admitted === envelope) return message;
      if (typeof admitted.text !== "string" ||
          !isDeepStrictEqual(admitted.tool_calls, calls) ||
          !Array.isArray(admitted.thinking) || admitted.thinking.length !== thinking.length)
        throw new AdmissionError("invalid");
      const changedText = admitted.text !== envelope.text;
      // A joined text projection cannot safely identify edits across multiple blocks.
      if (changedText && (texts.length !== 1 || texts[0].textSignature))
        throw new AdmissionError("invalid");
      const replacements = admitted.thinking.map((value: unknown, index: number) => {
        const original = envelope.thinking[index];
        if (!isRecord(value) || typeof value.text !== "string" ||
            value.signature !== original.signature ||
            (original.signature !== null &&
              !["reasoning", "reasoning_content", "reasoning_text"].includes(original.signature) &&
              value.text !== original.text))
          throw new AdmissionError("invalid");
        return value.text;
      });
      let index = 0;
      return {
        ...message,
        content: message.content.map((block) => {
          if (block.type === "text" && changedText)
            return { ...block, text: admitted.text as string };
          if (block.type === "thinking")
            return { ...block, thinking: replacements[index++] };
          return block;
        }),
      };
    }
    // Keep text block boundaries and metadata; images remain outside this POC.
    textOnly(message.content);
    const envelope = {
      schema_version: "openshell.pi-tool-result.v1",
      tool_call_id: message.toolCallId,
      tool_name: message.toolName,
      content: message.content.map((block) => ({ type: "text", text: (block as TextContent).text })),
      is_error: message.isError,
    };
    const admitted = await this.apply("tool_result", envelope, signal);
    if (admitted === envelope) return message;
    if (admitted.tool_call_id !== message.toolCallId ||
        admitted.tool_name !== message.toolName || admitted.is_error !== message.isError ||
        !Array.isArray(admitted.content) || admitted.content.length !== message.content.length)
      throw new AdmissionError("invalid");
    const content = admitted.content.map((value: unknown, index: number): TextContent => {
      const original = message.content[index] as TextContent;
      if (!isRecord(value) || value.type !== "text" || typeof value.text !== "string" ||
          (original.textSignature && value.text !== original.text))
        throw new AdmissionError("invalid");
      return { ...original, text: value.text };
    });
    return { ...message, content };
  }

  async receipt(context: Context, signal?: AbortSignal): Promise<string> {
    const entries = context.messages.flatMap((message) => {
      if (message.role === "user")
        return [{ role: "user", text: textOnly(message.content) }];
      if (message.role === "toolResult")
        return [
          {
            role: "tool",
            tool_call_id: message.toolCallId.split("|", 1)[0],
            text: textOnly(message.content) || "(no tool output)",
          },
        ];
      return [];
    });
    const result = await this.evaluate(
      "provider_context",
      { schema_version: "openshell.pi-provider-context.v1", entries },
      signal,
    );
    if (result.decision === "deny") throw new AdmissionError("denied");
    // A send-only replacement would leave saved history inconsistent. Fix the
    // earlier admission boundary instead of silently diverging at egress.
    if (result.decision !== "allow" || !result.receipt)
      throw new AdmissionError("invalid");
    return result.receipt;
  }

  private async apply(
    kind: AdmissionKind,
    body: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    const result = await this.evaluate(kind, body, signal);
    if (signal?.aborted) throw new AdmissionError("unavailable");
    if (result.decision === "deny") throw new AdmissionError("denied");
    if (result.decision === "replace") {
      if (
        !result.replacement ||
        result.replacement.schema_version !== body.schema_version
      )
        throw new AdmissionError("invalid");
      return result.replacement;
    }
    return body;
  }
}

export function textOnly(
  content: string | readonly { type: string; text?: string }[],
): string {
  if (typeof content === "string") return content;
  if (
    content.some(
      (block) => block.type !== "text" || typeof block.text !== "string",
    )
  )
    throw new AdmissionError("unsupported");
  return content.map((block) => block.text).join("\n");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
