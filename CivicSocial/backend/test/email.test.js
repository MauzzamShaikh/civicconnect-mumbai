import assert from "node:assert/strict";
import test from "node:test";
import { config } from "../src/config.js";
import { sendActionEmail } from "../src/email.js";

const originalBrevo = { ...config.brevo };
const originalFrontendUrl = config.frontendUrl;

function configureBrevo({ apiKey = "test-api-key", senderEmail = "no-reply@example.com" } = {}) {
  config.brevo.apiKey = apiKey;
  config.brevo.senderEmail = senderEmail;
  config.brevo.senderName = "Test Sender";
  config.frontendUrl = "https://social.example.test/";
}

test.afterEach(() => {
  Object.assign(config.brevo, originalBrevo);
  config.frontendUrl = originalFrontendUrl;
  delete globalThis.fetch;
});

test("Brevo sends verification and reset links to the configured frontend", async () => {
  configureBrevo();
  const requests = [];
  globalThis.fetch = async (...args) => {
    requests.push(args);
    return { ok: true, status: 201 };
  };

  await sendActionEmail("person@example.com", "verify", "token +/=");
  await sendActionEmail("person@example.com", "reset", "reset-token");

  assert.equal(requests.length, 2);
  for (const [url, options] of requests) {
    assert.equal(url, "https://api.brevo.com/v3/smtp/email");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["api-key"], "test-api-key");
    const payload = JSON.parse(options.body);
    assert.deepEqual(payload.sender, { name: "Test Sender", email: "no-reply@example.com" });
    assert.deepEqual(payload.to, [{ email: "person@example.com" }]);
  }
  assert.match(JSON.parse(requests[0][1].body).htmlContent, /https:\/\/social\.example\.test\/verify-email\?token=token%20%2B%2F%3D/);
  assert.match(JSON.parse(requests[1][1].body).htmlContent, /https:\/\/social\.example\.test\/reset-password\?token=reset-token/);
});

test("partial Brevo configuration fails clearly without making an HTTP request", async () => {
  configureBrevo({ senderEmail: "" });
  globalThis.fetch = async () => { throw new Error("must not call fetch"); };
  await assert.rejects(sendActionEmail("person@example.com", "verify", "token"), /Brevo email configuration is incomplete/);
});

test("Brevo API rejection is surfaced", async () => {
  configureBrevo();
  globalThis.fetch = async () => ({ ok: false, status: 401 });
  await assert.rejects(sendActionEmail("person@example.com", "verify", "token"), /HTTP 401/);
});

test("Brevo network errors are surfaced without leaking transport details", async () => {
  configureBrevo();
  globalThis.fetch = async () => { throw new Error("private network detail"); };
  await assert.rejects(sendActionEmail("person@example.com", "verify", "token"), error => {
    assert.match(error.message, /Brevo email delivery request failed/);
    assert.doesNotMatch(error.message, /private network detail/);
    return true;
  });
});
