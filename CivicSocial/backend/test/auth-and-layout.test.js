import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { requireVerifiedEmail, signupVerificationResponse, verifyGoogleIdentity } from "../src/auth-policy.js";
import { createEmailVerificationService } from "../src/email-verification.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");

function makeVerificationService({ clock = new Date("2026-10-13T00:00:00Z"), deliverFails = false } = {}) {
  const users = new Map([[1, { id: 1, email: "person@example.com", emailVerified: false, async update(fields) { Object.assign(this, fields); } }]]);
  const tokens = new Map();
  let tokenSequence = 0;
  let sends = 0;
  const greaterThan = Symbol("gt");
  const User = {
    async findByPk(id) { return users.get(Number(id)) || null; },
    async findOne({ where }) { return [...users.values()].find(user => user.email === where.email) || null; },
  };
  const EmailToken = {
    async destroy({ where }) {
      for (const [token, record] of tokens) {
        if ((where.token && where.token === token) || (where.userId === record.userId && where.purpose === record.purpose)) tokens.delete(token);
      }
    },
    async findOne({ where }) {
      const record = tokens.get(where.token);
      if (!record || record.purpose !== where.purpose || !(record.expiresAt > where.expiresAt[greaterThan])) return null;
      return { ...record, async destroy() { tokens.delete(record.token); } };
    },
  };
  const service = createEmailVerificationService({
    User,
    EmailToken,
    greaterThan,
    now: () => new Date(clock),
    createEmailToken: async (userId, purpose) => {
      tokenSequence += 1;
      const token = `${"a".repeat(63)}${tokenSequence.toString(16)}`;
      tokens.set(token, { token, userId, purpose, expiresAt: new Date(clock.getTime() + 3_600_000) });
      return token;
    },
    sendActionEmail: async () => { sends += 1; if (deliverFails) throw new Error("transport failed"); },
  });
  return { service, users, tokens, get sends() { return sends; }, setClock(value) { clock = value; } };
}

test("signup response requires verification and never includes a session token", () => {
  const result = signupVerificationResponse("person@example.com", true);
  assert.equal(result.verification_required, true);
  assert.equal(result.email_sent, true);
  assert.equal("access_token" in result, false);
  assert.equal("token" in result, false);
});

test("unverified password accounts are rejected while verified accounts remain eligible", () => {
  assert.throws(() => requireVerifiedEmail({ email: "person@example.com", emailVerified: false }), error => error.code === "EMAIL_NOT_VERIFIED" && error.status === 403);
  assert.doesNotThrow(() => requireVerifiedEmail({ email: "person@example.com", emailVerified: true }));
});

test("valid verification marks only its user and consumes the token", async () => {
  const { service, users, tokens } = makeVerificationService();
  const sent = await service.sendSignupVerification(users.get(1));
  assert.equal(sent, true);
  const token = [...tokens.keys()][0];
  assert.equal(await service.verify(token), true);
  assert.equal(users.get(1).emailVerified, true);
  assert.equal(tokens.has(token), false);
});

test("invalid and expired verification tokens do not verify an account", async () => {
  const store = makeVerificationService();
  assert.equal(await store.service.verify("not-a-token"), false);
  await store.service.sendSignupVerification(store.users.get(1));
  const token = [...store.tokens.keys()][0];
  store.setClock(new Date("2026-10-13T02:00:00Z"));
  assert.equal(await store.service.verify(token), false);
  assert.equal(store.users.get(1).emailVerified, false);
});

test("resend issues one fresh token, invalidates stale tokens, and applies cooldown", async () => {
  const store = makeVerificationService();
  const user = store.users.get(1);
  await store.service.sendSignupVerification(user);
  const firstToken = [...store.tokens.keys()][0];
  const response = await store.service.resend(" PERSON@example.com ");
  assert.equal(store.sends, 2);
  assert.equal(store.tokens.has(firstToken), false);
  assert.equal(store.tokens.size, 1);
  assert.equal("token" in response, false);
  assert.deepEqual(response, await store.service.resend("unknown@example.com"));
  assert.deepEqual(response, await store.service.resend(user.email));
  assert.equal(store.sends, 2);
});

test("failed verification delivery is reported as failure and its token is removed", async () => {
  const store = makeVerificationService({ deliverFails: true });
  const sent = await store.service.sendSignupVerification(store.users.get(1));
  assert.equal(sent, false);
  assert.equal(store.tokens.size, 0);
  assert.equal(signupVerificationResponse("person@example.com", sent).email_sent, false);
});

test("Google accepts only matching-audience tokens with verified email claims", async () => {
  const claims = await verifyGoogleIdentity("valid", {
    clientId: "web-client-id",
    verifyIdToken: async (_token, audience) => {
      assert.equal(audience, "web-client-id");
      return { email: "person@example.com", email_verified: true };
    },
  });
  assert.equal(claims.email_verified, true);
  await assert.rejects(verifyGoogleIdentity("invalid", {
    clientId: "web-client-id",
    verifyIdToken: async () => { throw new Error("audience mismatch"); },
  }), error => error.status === 401 && error.code === "GOOGLE_TOKEN_INVALID");
});

test("Google rejects absent configuration and unverified provider emails", async () => {
  await assert.rejects(verifyGoogleIdentity("token", { clientId: "", verifyIdToken: async () => ({}) }), error => error.status === 503 && error.code === "GOOGLE_AUTH_NOT_CONFIGURED");
  await assert.rejects(verifyGoogleIdentity("token", { clientId: "client", verifyIdToken: async () => ({ email: "person@example.com", email_verified: false }) }), error => error.status === 401 && error.code === "GOOGLE_EMAIL_UNVERIFIED");
});

test("auth layout fills desktop width, centers the form, and collapses for mobile", async () => {
  const css = await readFile(resolve(root, "frontend/styles.css"), "utf8");
  const app = await readFile(resolve(root, "frontend/app.js"), "utf8");
  assert.match(css, /#auth-landing\s*\{[^}]*flex:\s*1 1 100%[^}]*width:\s*100%/s);
  assert.match(css, /Auth layout:[\s\S]*?\.auth-split\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) minmax\(0, 1fr\)[^}]*width:\s*100%[^}]*min-width:\s*0/s);
  assert.match(css, /Auth layout:[\s\S]*?\.auth-form-inner\s*\{[^}]*max-width:\s*380px[^}]*margin-inline:\s*auto/s);
  assert.match(css, /\.auth-form-panel\s*\{[^}]*align-items:\s*center[^}]*justify-content:\s*center/s);
  assert.match(css, /@media\s*\(max-width:\s*700px\)[\s\S]*?\.auth-visual\s*\{\s*display:\s*none/s);
  assert.match(app, /class="auth-split"[\s\S]*?class="auth-form-panel"/);
  assert.match(app, /id="google-button-container"/);
  assert.match(app, /accounts\.id\.renderButton\(/);
  assert.match(app, /data-google-context="signup"/);
  assert.doesNotMatch(app, /window\.prompt\(['"]Enter the Google|cs_google_client_id/);
});

test("HTTP auth routes use signup, login, session, and resend verification policies", async () => {
  const server = await readFile(resolve(root, "backend/src/server.js"), "utf8");
  const auth = await readFile(resolve(root, "backend/src/auth.js"), "utf8");
  const signupRoute = server.match(/app\.post\("\/auth\/signup"[\s\S]*?\n\}\)\);/)?.[0] || '';
  const signupFlow = signupRoute.slice(signupRoute.indexOf('const user ='), signupRoute.indexOf('}));'));
  const loginRoute = server.match(/app\.post\("\/auth\/login"[\s\S]*?\n\}\)\);/)?.[0] || '';
  assert.match(signupRoute, /res\.status\(202\)\.json\(signupVerificationResponse/);
  assert.doesNotMatch(signupFlow, /authResponse|issueToken/);
  assert.match(loginRoute, /passwordsMatch/);
  assert.match(loginRoute, /requireVerifiedEmail\(user\)/);
  assert.match(server, /app\.get\("\/auth\/session", requireAuth/);
  assert.match(server, /app\.post\("\/auth\/resend-verification"/);
  assert.match(auth, /requireVerifiedEmail\(req\.user\)/);
});
