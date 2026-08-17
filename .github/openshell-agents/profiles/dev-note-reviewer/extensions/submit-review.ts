// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { type Static, type TSchema } from "typebox";
import { Value } from "typebox/value";

const payloadRoot = process.env.OAR_RUNTIME_ROOT || "/sandbox/oar-runtime";
const responseSchema = JSON.parse(
  readFileSync(`${payloadRoot}/schemas/output.schema.json`, "utf8"),
) as TSchema;
const repositoryRoot = realpathSync(process.env.REPOSITORY_ROOT || "/workspace/source");
const requestedPath = process.env.REVIEW_TARGET_PATH || "";
if (!requestedPath || isAbsolute(requestedPath) || requestedPath.split("/").includes("..")) {
  throw new Error("REVIEW_TARGET_PATH must be a repository-relative path without '..'");
}
const candidatePath = realpathSync(resolve(repositoryRoot, requestedPath));
const relativeCandidate = relative(repositoryRoot, candidatePath);
if (relativeCandidate.startsWith("..") || isAbsolute(relativeCandidate)) {
  throw new Error("REVIEW_TARGET_PATH escapes REPOSITORY_ROOT");
}
const markdown = readFileSync(candidatePath, "utf8");
const taskInput = {
  markdown,
  model_id: process.env.OAR_MODEL_ID || "",
  source_path: requestedPath,
  source_revision: execFileSync("git", ["-C", repositoryRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
  source_content_digest: createHash("sha256").update(markdown).digest("hex"),
} as Record<string, unknown>;
const outputDirectory = "/sandbox/artifacts";
const outputPath = `${outputDirectory}/review.json`;

type DocumentFinding = {
  quote: string;
  source_path: string;
  line: number;
  column: number;
};

type DocumentReview = {
  model_id: string;
  source_revision: string;
  source_content_digest: string;
  findings: DocumentFinding[];
};

const review = responseSchema;

function sourcePosition(markdown: string, quote: string) {
  const first = markdown.indexOf(quote);
  if (first < 0 || markdown.indexOf(quote, first + 1) >= 0) return undefined;
  const lineStart = markdown.lastIndexOf("\n", first - 1) + 1;
  return {
    line: markdown.slice(0, first).split("\n").length,
    column: Array.from(markdown.slice(lineStart, first)).length + 1,
  };
}

function evidenceErrors(params: DocumentReview): string[] {
  const markdown = taskInput.markdown;
  const expectedPath = taskInput.source_path;
  const expectedRevision = taskInput.source_revision;
  const expectedDigest = taskInput.source_content_digest;
  const errors: string[] = [];

  if (
    typeof expectedRevision === "string" &&
    params.source_revision !== expectedRevision
  ) {
    errors.push("/source_revision: must match the inspected source");
  }
  if (
    typeof expectedDigest === "string" &&
    params.source_content_digest !== expectedDigest
  ) {
    errors.push("/source_content_digest: must match the task bundle");
  }
  if (typeof markdown !== "string" || typeof expectedPath !== "string") {
    return errors;
  }

  params.findings.forEach((item, index) => {
    const path = `/findings/${index}`;
    if (item.source_path !== expectedPath) {
      errors.push(`${path}/source_path: must match the task source_path`);
    }
    const first = markdown.indexOf(item.quote);
    if (first < 0) {
      errors.push(`${path}/quote: exact text was not found in the candidate`);
      return;
    }
    if (markdown.indexOf(item.quote, first + 1) >= 0) {
      errors.push(`${path}/quote: text is not unique in the candidate`);
      return;
    }
    const position = sourcePosition(markdown, item.quote);
    if (!position) return;
    if (item.line !== position.line || item.column !== position.column) {
      errors.push(
        `${path}: quote begins at line ${position.line}, column ${position.column}, not line ${item.line}, column ${item.column}`,
      );
    }
  });
  return errors;
}

const submitReview = defineTool({
  name: "submit_review",
  label: "Submit Review",
  description: "Validate and save the final Dev Note review.",
  promptSnippet: "Submit the final schema-valid Dev Note review",
  promptGuidelines: [
    "Call submit_review only after inspecting the repository and completing the review.",
    "If submit_review returns validation errors, correct every error and call it again.",
    "Do not emit the report as assistant text.",
  ],
  parameters: review,
  prepareArguments(raw) {
    const params = { ...(raw as Record<string, unknown>) };
    if (typeof taskInput.model_id === "string" && taskInput.model_id) {
      params.model_id = taskInput.model_id;
    }
    if (typeof taskInput.source_revision === "string") {
      params.source_revision = taskInput.source_revision;
    }
    if (typeof taskInput.source_content_digest === "string") {
      params.source_content_digest = taskInput.source_content_digest;
    }
    if (
      Array.isArray(params.findings) &&
      typeof taskInput.markdown === "string" &&
      typeof taskInput.source_path === "string"
    ) {
      params.findings = params.findings.map((rawFinding) => {
        const item = { ...(rawFinding as Record<string, unknown>) };
        item.source_path = taskInput.source_path;
        if (typeof item.quote === "string") {
          const position = sourcePosition(taskInput.markdown as string, item.quote);
          if (position) Object.assign(item, position);
        }
        return item;
      });
    }
    return params as Static<typeof review>;
  },
  async execute(_toolCallId, rawParams) {
    const params = rawParams as DocumentReview;
    const schemaDiagnostics = Value.Check(responseSchema, params)
      ? []
      : Value.Errors(responseSchema, params)
        .slice(0, 12)
        .map((error) => `${error.instancePath || "/"}: ${error.message}`);
    const evidenceDiagnostics =
      schemaDiagnostics.length === 0 ? evidenceErrors(params) : [];
    const diagnostics = [...schemaDiagnostics, ...evidenceDiagnostics]
      .slice(0, 12)
      .join("\n");
    if (diagnostics) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Review rejected by the configured response schema:\n${diagnostics}`,
          },
        ],
        details: { accepted: false, diagnostics },
        isError: true,
      };
    }

    mkdirSync(outputDirectory, { recursive: true, mode: 0o700 });
    const temporaryPath = `${outputPath}.tmp`;
    writeFileSync(temporaryPath, `${JSON.stringify(params, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    renameSync(temporaryPath, outputPath);
    return {
      content: [{ type: "text" as const, text: "Structured review accepted." }],
      details: { accepted: true, outputPath },
      terminate: true,
    };
  },
});

export default function (pi: ExtensionAPI) {
  pi.registerTool(submitReview);
}
