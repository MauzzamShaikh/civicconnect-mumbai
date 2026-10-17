import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { createSubmissionController } from "../../frontend/post-submission.mjs";

const appPath = resolve(dirname(fileURLToPath(import.meta.url)), "../../frontend/app.js");
const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "../../frontend");

test("post submission reports confirmed success and always restores the button state", async () => {
  const states = [];
  const controller = createSubmissionController(value => states.push(value));
  const result = await controller.run(async () => ({ id: 42, text: "saved", image_data: "data:image/png;base64,AA==" }));
  assert.equal(result.ok, true);
  assert.equal(result.value.id, 42);
  assert.deepEqual(states, [true, false]);
  assert.equal(controller.busy, false);
});

test("network and unexpected response failures retain the draft and restore the button", async () => {
  const states = [];
  const controller = createSubmissionController(value => states.push(value));
  const networkFailure = await controller.run(async () => { throw new TypeError("Failed to fetch"); });
  assert.equal(networkFailure.ok, false);
  assert.match(networkFailure.error.message, /connection/);
  let cleared = false;
  const malformed = await controller.run(async () => ({}));
  if (malformed.ok) cleared = true;
  assert.equal(malformed.ok, false);
  assert.equal(cleared, false);
  assert.match(malformed.error.message, /unexpected post response/i);
  assert.deepEqual(states, [true, false, true, false]);
});

test("a second submission while one request is pending is ignored", async () => {
  const controller = createSubmissionController();
  let resolveRequest;
  let calls = 0;
  const first = controller.run(() => {
    calls++;
    return new Promise(resolve => { resolveRequest = resolve; });
  });
  const duplicate = await controller.run(async () => { calls++; return { id: 2 }; });
  assert.equal(duplicate.duplicate, true);
  assert.equal(calls, 1);
  resolveRequest({ id: 1 });
  assert.equal((await first).ok, true);
});

test("a later post can be submitted after the previous post succeeds", async () => {
  const states = [];
  const controller = createSubmissionController(value => states.push(value));
  const first = await controller.run(async () => ({ id: 101 }));
  const second = await controller.run(async () => ({ id: 102 }));
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.deepEqual([first.value.id, second.value.id], [101, 102]);
  assert.deepEqual(states, [true, false, true, false]);
  assert.equal(controller.busy, false);
});

test("API errors, network errors, aborts, timeouts, and malformed bodies restore the submit state", async () => {
  const states = [];
  const controller = createSubmissionController(value => states.push(value));
  const cases = [
    [new Error("BMC validation failed"), /BMC validation failed/],
    [new TypeError("Failed to fetch"), /connection/i],
    [Object.assign(new Error("aborted"), { name: "AbortError" }), /timed out/i],
    [Object.assign(new Error("timeout"), { name: "TimeoutError" }), /timed out/i],
  ];
  for (const [failure, expected] of cases) {
    let draft = "My complaint draft";
    const result = await controller.run(async () => { throw failure; });
    assert.equal(result.ok, false);
    assert.match(result.error.message, expected);
    assert.equal(draft, "My complaint draft", "failed submission keeps the draft untouched");
    assert.equal(controller.busy, false);
  }
  const malformed = await controller.run(async () => ({ complaint_reference: "CMP-123" }));
  assert.equal(malformed.ok, false);
  assert.match(malformed.error.message, /unexpected post response/i);
  assert.deepEqual(states, Array(cases.length + 1).fill([true, false]).flat());

  for (const invalid of [{ id: "" }, { id: 0 }, { id: 1.5 }]) {
    const invalidResult = await controller.run(async () => invalid);
    assert.equal(invalidResult.ok, false);
    assert.match(invalidResult.error.message, /unexpected post response/i);
    assert.equal(controller.busy, false);
  }
});

test("a throwing UI state callback cannot leave a successful submission busy", async () => {
  const controller = createSubmissionController(() => { throw new Error("unmounted UI"); });
  const result = await controller.run(async () => ({ id: 91 }));
  assert.equal(result.ok, true);
  assert.equal(controller.busy, false);
});

test("the composer closes only after a confirmed response and includes attached media", async () => {
  const app = await readFile(appPath, "utf8");
  const submit = app.match(/async function submitPost\(\)[\s\S]*?\n\}/)?.[0] || "";
  assert.ok(submit);
  assert.ok(submit.indexOf("if (!submission.ok)") < submit.indexOf("closeCompose();"));
  assert.match(submit, /if \(composeImageBase64\) body\.image_base64/);
  assert.match(submit, /if \(composeVideoBase64\) body\.video_base64/);
  assert.match(submit, /postSubmissionLoading = true/);
});

test("the submission module is served with a JavaScript MIME type", async () => {
  const nginx = await readFile(resolve(frontendDir, "nginx.conf"), "utf8");
  const dockerfile = await readFile(resolve(frontendDir, "Dockerfile"), "utf8");
  const app = await readFile(appPath, "utf8");
  const moduleSource = await readFile(resolve(frontendDir, "post-submission.mjs"), "utf8");
  assert.match(nginx, /location\s*=\s*\/post-submission\.mjs\s*\{[\s\S]*?default_type\s+application\/javascript\s*;/);
  assert.match(dockerfile, /COPY\s+\.\s+\/usr\/share\/nginx\/html\//);
  assert.match(dockerfile, /COPY\s+nginx\.conf\s+\/etc\/nginx\/conf\.d\/default\.conf/);
  assert.match(app, /import\('\.\/post-submission\.mjs'\)/);
  assert.match(moduleSource, /export function createSubmissionController/);
});

test("the service worker clones network responses before asynchronous cache writes", async () => {
  const serviceWorker = await readFile(resolve(frontendDir, "service-worker.js"), "utf8");
  const fetchHandler = serviceWorker.split("self.addEventListener('fetch'")[1] || "";
  const cloneIndex = fetchHandler.indexOf("const cacheCopy = response.clone();");
  const cacheOpenIndex = fetchHandler.indexOf("await caches.open(CACHE)");
  assert.ok(cloneIndex >= 0);
  assert.ok(cacheOpenIndex > cloneIndex);
  assert.match(serviceWorker, /civicsocial-shell-v4/);
});
