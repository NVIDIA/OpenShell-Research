// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import type {
  Context,
  Message,
  TextContent,
  ToolResultMessage,
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
          "This example supports text content without reasoning payloads only.",
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
      return {
        role: "user",
        content: await this.text("user", textOnly(message.content), signal),
        timestamp: message.timestamp,
      };
    }
    if (message.role === "assistant") {
      if (
        message.content.some(
          (block) => block.type !== "text" && block.type !== "toolCall",
        )
      )
        throw new AdmissionError("unsupported");
      const calls = message.content
        .filter((block) => block.type === "toolCall")
        .map(({ id, name, arguments: args }) => ({
          id,
          name,
          arguments: args,
        }));
      const envelope = {
        schema_version: "openshell.pi-assistant-message.v1",
        text: message.content
          .filter((block) => block.type === "text")
          .map((block) => block.text)
          .join("\n"),
        tool_calls: calls,
      };
      const admitted = await this.apply("assistant_message", envelope, signal);
      if (
        typeof admitted.text !== "string" ||
        !isDeepStrictEqual(admitted.tool_calls, calls)
      )
        throw new AdmissionError("invalid");
      return {
        role: "assistant",
        api: message.api,
        provider: message.provider,
        model: message.model,
        usage: message.usage,
        stopReason: message.stopReason,
        timestamp: message.timestamp,
        content: [
          ...(admitted.text
            ? [{ type: "text" as const, text: admitted.text }]
            : []),
          ...calls.map((call) => ({ type: "toolCall" as const, ...call })),
        ],
      };
    }
    const envelope = {
      schema_version: "openshell.pi-tool-result.v1",
      tool_call_id: message.toolCallId,
      tool_name: message.toolName,
      content: [{ type: "text", text: textOnly(message.content) }],
      is_error: message.isError,
    };
    const admitted = await this.apply("tool_result", envelope, signal);
    if (
      admitted.tool_call_id !== message.toolCallId ||
      admitted.tool_name !== message.toolName ||
      admitted.is_error !== message.isError ||
      !Array.isArray(admitted.content)
    )
      throw new AdmissionError("invalid");
    const content = admitted.content.map((block: unknown): TextContent => {
      if (
        !isRecord(block) ||
        block.type !== "text" ||
        typeof block.text !== "string"
      )
        throw new AdmissionError("invalid");
      return { type: "text", text: block.text };
    });
    return {
      role: "toolResult",
      toolCallId: message.toolCallId,
      toolName: message.toolName,
      content,
      isError: message.isError,
      timestamp: message.timestamp,
    } satisfies ToolResultMessage;
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
