// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFileSync, writeFileSync } from "node:fs";

const options = parseOptions(process.argv.slice(2));
const baseUrl = parseBaseUrl(options.get("base-url"));
const modelId = requireOption(options, "model-id");
const modelsOutput = requireOption(options, "models-output");
const policyOutput = requireOption(options, "policy-output");
const providerProfileOutput = requireOption(options, "provider-profile-output");
const gatewayOutput = requireOption(options, "gateway-output");
const middlewareEndpoint = parseMiddlewareEndpoint(
  options.get("middleware-endpoint"),
);
const endpointPort = baseUrl.port || (baseUrl.protocol === "https:" ? "443" : "80");

const models = {
  providers: {
    "attested-provider": {
      baseUrl: baseUrl.toString().replace(/\/$/, ""),
      api: "openai-completions",
      apiKey: "$PI_MODEL_API_KEY",
      models: [
        {
          id: modelId,
          name: modelId,
          reasoning: false,
          input: ["text"],
          contextWindow: 128000,
          maxTokens: 16384,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        },
      ],
    },
  },
};
writeFileSync(modelsOutput, `${JSON.stringify(models, null, 2)}\n`);

const policyTemplate = readFileSync(new URL("policy.yaml", import.meta.url), "utf8");
const policy = replaceExpected(
  replaceExpected(policyTemplate, "provider.example.com", baseUrl.hostname, 2),
  "        port: 443",
  `        port: ${endpointPort}`,
  1,
);
writeFileSync(policyOutput, policy);

writeFileSync(
  providerProfileOutput,
  `id: pi-attested-model
display_name: Pi attested-admission model
description: Endpoint-scoped model credential for the Pi attested-admission example
category: inference
inference_capable: true
credentials:
  - name: api_key
    description: Model provider API key
    env_vars: [PI_MODEL_API_KEY]
    required: true
    auth_style: bearer
    header_name: authorization
discovery:
  credentials: [api_key]
endpoints:
  - host: ${JSON.stringify(baseUrl.hostname)}
    port: ${endpointPort}
    protocol: rest
    access: read-write
    enforcement: enforce
binaries: [/usr/bin/node, /usr/local/bin/node]
`,
);

writeFileSync(
  gatewayOutput,
  `[[openshell.supervisor.middleware]]
name = "pi-egress"
grpc_endpoint = "${middlewareEndpoint}"
allow_insecure_transport = true
max_payload_bytes = 4194304
timeout = "30s"
`,
);

function parseOptions(argumentsList) {
  if (argumentsList.length % 2 !== 0) {
    fail("Options must be passed as --name value pairs.");
  }
  const parsed = new Map();
  for (let index = 0; index < argumentsList.length; index += 2) {
    const name = argumentsList[index];
    if (!name.startsWith("--")) {
      fail(`Expected an option name, received: ${name}`);
    }
    parsed.set(name.slice(2), argumentsList[index + 1]);
  }
  return parsed;
}

function requireOption(parsed, name) {
  const value = parsed.get(name);
  if (!value) {
    fail(`Missing --${name}.`);
  }
  return value;
}

function parseBaseUrl(value) {
  const raw = value || fail("Missing --base-url.");
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    fail("--base-url must be an absolute HTTP or HTTPS URL.");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    fail("--base-url must use HTTP or HTTPS.");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    fail("--base-url must not contain credentials, a query, or a fragment.");
  }
  return parsed;
}

function parseMiddlewareEndpoint(value) {
  const raw = value || fail("Missing --middleware-endpoint.");
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    fail("--middleware-endpoint must be an absolute HTTP or HTTPS URL.");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    fail("--middleware-endpoint must use HTTP or HTTPS.");
  }
  if (
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash
  ) {
    fail("--middleware-endpoint must contain only a scheme, host, and port.");
  }
  return parsed.toString().replace(/\/$/, "");
}

function replaceExpected(value, marker, replacement, expectedOccurrences) {
  const occurrences = value.split(marker).length - 1;
  if (occurrences !== expectedOccurrences) {
    fail(
      `Expected policy marker ${marker} ${expectedOccurrences} times; found ${occurrences}.`,
    );
  }
  return value.replaceAll(marker, replacement);
}

function fail(message) {
  console.error(message);
  process.exit(1);
}
