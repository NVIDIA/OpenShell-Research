// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Message, TextContent } from "@earendil-works/pi-ai";

export type AdmissionMode = "off" | "on";
export type TextOrigin = "user" | "compaction_summary";

const SYNTHETIC_KEY = /sk-[A-Za-z0-9_-]{16,}/g;
const REPLACEMENT = "[REDACTED]";
const EDITABLE_THINKING_SIGNATURES = new Set([
  "reasoning",
  "reasoning_content",
  "reasoning_text",
]);

export class AdmissionError extends Error {
  constructor(readonly kind: "unsupported" | "invalid") {
    super(
      kind === "unsupported"
        ? "This content is outside the example’s supported Chat Completions format."
        : "Local admission cannot safely transform signed content or tool-call semantics.",
    );
  }
}

/** Apply the example's one local policy directly to native Pi messages. */
export class Admission {
  constructor(readonly mode: AdmissionMode) {}

  async text(
    _origin: TextOrigin,
    text: string,
    signal?: AbortSignal,
  ): Promise<string> {
    signal?.throwIfAborted();
    return this.redact(text);
  }

  async message(message: Message, signal?: AbortSignal): Promise<Message> {
    signal?.throwIfAborted();
    if (message.role === "user") {
      if (typeof message.content === "string") {
        const content = this.redact(message.content);
        return content === message.content ? message : { ...message, content };
      }
      return {
        ...message,
        content: message.content.map((block) => {
          if (block.type !== "text") throw new AdmissionError("unsupported");
          const text = this.redact(block.text);
          if (text !== block.text && block.textSignature)
            throw new AdmissionError("invalid");
          return text === block.text ? block : { ...block, text };
        }),
      };
    }
    if (message.role === "assistant") {
      return {
        ...message,
        content: message.content.map((block) => {
          if (block.type === "toolCall") {
            if (
              this.mode === "on" &&
              JSON.stringify(block).match(SYNTHETIC_KEY)
            )
              throw new AdmissionError("invalid");
            return block;
          }
          if (block.type === "text") {
            const text = this.redact(block.text);
            if (text !== block.text && block.textSignature)
              throw new AdmissionError("invalid");
            return text === block.text ? block : { ...block, text };
          }
          if (block.type === "thinking") {
            // Chat Completions can retain plaintext reasoning in replay metadata.
            if (
              this.mode === "on" &&
              block.thinkingSignature?.match(SYNTHETIC_KEY)
            )
              throw new AdmissionError("invalid");
            const thinking = this.redact(block.thinking);
            if (
              thinking !== block.thinking &&
              block.thinkingSignature &&
              !EDITABLE_THINKING_SIGNATURES.has(block.thinkingSignature)
            )
              throw new AdmissionError("invalid");
            return thinking === block.thinking
              ? block
              : { ...block, thinking };
          }
          throw new AdmissionError("unsupported");
        }),
      };
    }
    return {
      ...message,
      content: message.content.map((block): TextContent => {
        if (block.type !== "text") throw new AdmissionError("unsupported");
        const text = this.redact(block.text);
        if (text !== block.text && block.textSignature)
          throw new AdmissionError("invalid");
        return text === block.text ? block : { ...block, text };
      }),
    };
  }

  private redact(text: string): string {
    return this.mode === "on" ? text.replace(SYNTHETIC_KEY, REPLACEMENT) : text;
  }
}
