import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { BMC_CATEGORIES, createGroqCivicClassifier } from "../src/groq-civic-classifier.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const key = "unit-test-secret-that-must-not-escape";

function mockResponse(result) {
  return { ok: true, status: 200, async json() {
    return { choices: [{ message: { content: JSON.stringify(result) } }] };
  } };
}

test("Groq request uses the configured model, strict result schema, and validates BMC categories", async () => {
  let request;
  const classify = createGroqCivicClassifier({
    apiKey: key,
    model: "openai/gpt-oss-20b",
    fetchImpl: async (url, options) => { request = { url, options }; return mockResponse({
      classification: "civic_complaint", category: "Transport and mobility", reason: "A traffic signal is not working",
    }); },
  });

  const result = await classify("@mybmc Traffic light is not working at JJ signal");
  const body = JSON.parse(request.options.body);
  assert.equal(request.url, "https://api.groq.com/openai/v1/chat/completions");
  assert.equal(request.options.headers.authorization, `Bearer ${key}`);
  assert.equal(body.model, "openai/gpt-oss-20b");
  assert.equal(body.response_format.json_schema.strict, true);
  assert.deepEqual(result, {
    classification: "civic_complaint", category: "Transport and mobility", reason: "A traffic signal is not working",
  });
  assert.ok(BMC_CATEGORIES.includes(result.category));
  assert.doesNotMatch(JSON.stringify(result), new RegExp(key));
});

test("malformed JSON and categories outside the BMC taxonomy are rejected", async () => {
  const malformed = createGroqCivicClassifier({ apiKey: key, fetchImpl: async () => ({
    ok: true, status: 200, async json() { return { choices: [{ message: { content: "not json" } }] }; },
  }) });
  await assert.rejects(malformed("@mybmc something"), /malformed civic classification JSON/);

  const unsupported = createGroqCivicClassifier({ apiKey: key, fetchImpl: async () => mockResponse({
    classification: "civic_complaint", category: "Traffic Signals", reason: "A signal is broken",
  }) });
  await assert.rejects(unsupported("@mybmc signal broken"), /unsupported civic classification/);
});

test("missing configuration, rate limits, provider errors, timeouts, and oversize text fail safely", async () => {
  let called = false;
  const missingKey = createGroqCivicClassifier({ fetchImpl: async () => { called = true; } });
  await assert.rejects(missingKey("@mybmc pothole"), /not configured/);
  assert.equal(called, false);

  for (const [status, message] of [[429, /rate limited/], [401, /rejected the configured credentials/], [503, /temporarily unavailable/]]) {
    const classify = createGroqCivicClassifier({ apiKey: key, fetchImpl: async () => ({ ok: false, status }) });
    await assert.rejects(classify("@mybmc pothole"), message);
  }

  const timeout = createGroqCivicClassifier({
    apiKey: key,
    timeoutMs: 5,
    fetchImpl: async (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
    }),
  });
  await assert.rejects(timeout("@mybmc pothole"), /timed out/);

  const oversize = createGroqCivicClassifier({ apiKey: key, fetchImpl: async () => { called = true; } });
  await assert.rejects(oversize("x".repeat(4_001)), /too long/);
  assert.equal(called, false);
});

test("transport error messages cannot expose an API key", async () => {
  const classify = createGroqCivicClassifier({
    apiKey: key,
    fetchImpl: async () => { throw new Error(`request failed with ${key}`); },
  });
  await assert.rejects(classify("@mybmc pothole"), error => {
    assert.doesNotMatch(error.message, new RegExp(key));
    return /temporarily unavailable/.test(error.message);
  });
});

test("Groq configuration is backend-only and no frontend asset references its key", async () => {
  const frontendSource = await readFile(resolve(root, "frontend/app.js"), "utf8");
  const html = await readFile(resolve(root, "frontend/index.html"), "utf8");
  const server = await readFile(resolve(root, "backend/src/server.js"), "utf8");
  const compose = await readFile(resolve(root, "docker-compose.yml"), "utf8");
  assert.doesNotMatch(frontendSource, /GROQ_API_KEY|groqApiKey/i);
  assert.doesNotMatch(html, /GROQ_API_KEY|groqApiKey/i);
  assert.match(server, /classification_status:\s*integration\.classificationStatus/);
  assert.match(server, /category:\s*integration\.category/);
  assert.match(frontendSource, /state === 'not_civic'/);
  assert.match(frontendSource, /state === 'uncertain'/);
  assert.match(frontendSource, /state === 'classification_retry'/);
  assert.match(compose, /api:[\s\S]*?env_file:\s*\.env/);
});
