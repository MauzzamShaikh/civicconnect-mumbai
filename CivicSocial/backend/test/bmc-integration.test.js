import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { createBmcIntegration } from "../src/bmc-integration.js";
import { isCivicIssue, isEligibleBmcComplaint, mentionsMyBmc } from "../src/civic-relevance.js";

const Op = { in: Symbol.for("in"), or: Symbol.for("or"), lte: Symbol.for("lte") };

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");

function harness({ fetchResponses = [], official = true, ackFailsAfterCreate = false, fetchDelay = 0, configOverrides = {} } = {}) {
  let clock = new Date("2026-10-14T00:00:00.000Z");
  let recordId = 0;
  let replyId = 1000;
  let ackAttempts = 0;
  const rows = new Map();
  const calls = [];
  const replies = [];
  const logs = [];
  const originalPost = {
    id: 317,
    authorId: 7,
    text: "@mybmc Please repair the pothole outside the school.",
    imageData: null,
    videoData: null,
    parentPostId: null,
    author: { id: 7, handle: "neighbor", isOfficial: false },
  };
  const publicPost = {
    id: originalPost.id,
    text: originalPost.text,
    image_data: originalPost.imageData,
    video_data: originalPost.videoData,
    parent_post_id: originalPost.parentPostId,
    author: { id: 7, handle: "neighbor", is_official: false },
  };
  let officialUser = official === false ? null : { id: 1, handle: "mybmc", isOfficial: official !== "unofficial" };
  const User = {
    async findOne({ where }) { return where.handle === "mybmc" ? officialUser : null; },
  };
  function makeRow(values) {
    return {
      id: ++recordId,
      ...values,
      async update(fields) { Object.assign(this, fields); return this; },
    };
  }
  const Delivery = {
    async findOrCreate({ where, defaults }) {
      let row = rows.get(where.originalPostId);
      const created = !row;
      if (!row) { row = makeRow(defaults); rows.set(where.originalPostId, row); }
      return [row, created];
    },
    async findOne({ where }) { return rows.get(where.originalPostId) || null; },
    async update(fields, { where }) {
      const row = [...rows.values()].find(item => item.id === where.id);
      if (!row) return [0];
      const allowed = where.status[Op.in];
      if (!allowed.includes(row.status)) return [0];
      const lockAvailable = where[Op.or].some(condition => {
        if (condition.lockedUntil === null) return row.lockedUntil == null;
        return row.lockedUntil != null && row.lockedUntil <= condition.lockedUntil[Op.lte];
      });
      if (!lockAvailable) return [0];
      Object.assign(row, fields);
      return [1];
    },
    async findAll({ where, limit }) { return [...rows.values()].filter(row => where.status[Op.in].includes(row.status)).slice(0, limit ?? rows.size); },
  };
  const Post = {
    async findByPk(id) { return Number(id) === originalPost.id ? originalPost : null; },
    async findAll({ where }) { return replies.filter(reply => reply.parentPostId === where.parentPostId && reply.authorId === where.authorId); },
  };
  const socialPlatform = {
    async createPost(input) {
      ackAttempts += 1;
      const reply = { id: ++replyId, parentPostId: input.parentPostId, authorId: input.authorId, text: input.text };
      replies.push(reply);
      if (ackFailsAfterCreate && ackAttempts === 1) throw new Error("notification persistence failed after reply creation");
      return { id: reply.id };
    },
  };
  const integration = createBmcIntegration({
    Delivery, User, Post, socialPlatform,
    config: {
      bmcApiBaseUrl: "https://bmc.example.test",
      bmcIntegrationSecret: "mock-secret-only",
      bmcCitizenFrontendUrl: "http://localhost:5174",
      frontendUrl: "http://localhost:5180",
      ...configOverrides,
    },
    fetchImpl: async (url, options) => {
      calls.push({ url, ...options, body: JSON.parse(options.body) });
      if (fetchDelay) await new Promise(resolve => setTimeout(resolve, fetchDelay));
      const next = fetchResponses.length ? fetchResponses.shift() : { status: 201, body: { complaint_reference: "CMP-1234567890", review_state: "pending", status: "received", created: true, already_existed: false } };
      if (next instanceof Error) throw next;
      return { ok: next.status >= 200 && next.status < 300, status: next.status, async json() { return next.body; } };
    },
    now: () => new Date(clock),
    logger: { error(message) { logs.push(message); }, warn(message) { logs.push(message); }, info(message) { logs.push(message); } },
  });
  return {
    integration, calls, replies, rows, logs, publicPost, originalPost,
    setClock(value) { clock = new Date(value); },
    get ackAttempts() { return ackAttempts; },
    setOfficial(value) { officialUser = value ? { id: 1, handle: "mybmc", isOfficial: true } : null; },
  };
}

function successfulResponse(reference = "CMP-1234567890") {
  return { status: 201, body: { complaint_reference: reference, review_state: "pending", status: "received", created: true, already_existed: false } };
}

test("classifier ignores ordinary posts, partial handles, and unrelated @mybmc mentions", () => {
  assert.equal(mentionsMyBmc("Thanks @mybmc for the update"), true);
  assert.equal(mentionsMyBmc("@mybmc_updates"), false);
  assert.equal(isCivicIssue("Looking forward to the cricket match"), false);
  assert.equal(isCivicIssue("The Garbage movie with @mybmc was funny"), false);
  assert.equal(isEligibleBmcComplaint({ text: "@mybmc thanks for sharing the festival photos", author: { handle: "neighbor", is_official: false } }), false);
  assert.equal(isEligibleBmcComplaint({ text: "The pothole needs repair", author: { handle: "neighbor", is_official: false } }), false);
});

test("classifier recognizes reviewed English, Hindi, and Marathi civic issue patterns", () => {
  assert.equal(isCivicIssue("@mybmc the pothole is dangerous"), true);
  assert.equal(isCivicIssue("@mybmc नाली जाम है"), true);
  assert.equal(isCivicIssue("@mybmc रस्त्यावर खड्डे आहेत"), true);
  assert.equal(isCivicIssue("@mybmc सड़क पर गड्ढे हैं"), true);
  assert.equal(isCivicIssue("@mybmc आजचा सामना छान होता"), false);
});

test("classifier recognizes general public-health and vending complaints across mixed scripts", () => {
  for (const report of [
    "@mybmc unlicensed food stalls are selling contaminated food",
    "@mybmc illegal street vendors are causing food poisoning; please inspect",
    "@mybmc खाद्य ठेले अवैध हैं और दूषित खाना बेच रहे हैं",
    "@mybmc अनधिकृत खाद्य स्टॉलमुळे अन्नातून विषबाधा होऊ शकते",
    "@mybmc the नाली is blocked and overflowing",
  ]) assert.equal(isCivicIssue(report), true, report);
  for (const report of [
    "@mybmc I ate at a restaurant and enjoyed the meal",
    "@mybmc tell me where to find a food stall",
    "We should discuss food safety at the next community meeting",
  ]) assert.equal(isCivicIssue(report), false, report);
  assert.equal(isEligibleBmcComplaint({ text: "@mybmc अनधिकृत खाद्य स्टॉलमुळे अन्नातून विषबाधा होऊ शकते", author: { handle: "resident", is_official: false } }), true);
});

test("official-account posts and official acknowledgement replies cannot re-enter delivery", async () => {
  const h = harness();
  const officialPost = { ...h.publicPost, author: { handle: "mybmc", is_official: true } };
  assert.equal(isEligibleBmcComplaint(officialPost), false);
  assert.equal(isEligibleBmcComplaint({ ...h.publicPost, author: { handle: "mybmc", is_official: false } }), false);
  assert.equal(await h.integration.processPost(officialPost), null);
  const automatedAcknowledgement = { ...officialPost, id: 999, parent_post_id: 317, text: "BMC complaint BMC-1 registered. @mybmc pothole track link" };
  assert.equal(await h.integration.processPost(automatedAcknowledgement), null);
  assert.equal(h.calls.length, 0);
  assert.equal(h.rows.size, 0);
});

test("eligible post uses stable ID, canonical link, and bearer authentication", async () => {
  const h = harness();
  const result = await h.integration.processPost(h.publicPost);
  assert.equal(result.status, "acknowledged");
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, "https://bmc.example.test/integrations/civicsocial/complaints");
  assert.equal(h.calls[0].headers.authorization, "Bearer mock-secret-only");
  assert.equal(h.calls[0].headers["idempotency-key"], "317");
  assert.deepEqual(h.calls[0].body, {
    original_post_id: "317",
    text: h.publicPost.text,
    author: "neighbor",
    source_post_url: "http://localhost:5180/#post/317",
  });
  assert.equal(h.replies[0].text.includes("CMP-1234567890"), true);
  assert.equal(h.replies[0].text.includes("http://localhost:5174/track?reference=CMP-1234567890"), true);
  assert.match(h.replies[0].text, /http:\/\/localhost:5174\/track\?reference=CMP-1234567890/);
});

test("supported image and video data URLs are forwarded; unsupported formats are omitted", async () => {
  const h = harness();
  const post = { ...h.publicPost, image_data: "data:image/png;base64,aGVsbG8=", video_data: "data:video/mp4;base64,AAECAw==" };
  h.originalPost.imageData = post.image_data;
  h.originalPost.videoData = post.video_data;
  await h.integration.processPost(post);
  assert.equal(h.calls[0].body.image_base64, "data:image/png;base64,aGVsbG8=");
  assert.equal(h.calls[0].body.video_base64, "data:video/mp4;base64,AAECAw==");

  const unsupported = harness();
  unsupported.originalPost.imageData = "data:image/gif;base64,aGVsbG8=";
  unsupported.originalPost.videoData = "data:video/webm;base64,aGVsbG8=";
  await unsupported.integration.processPost({ ...unsupported.publicPost, image_data: unsupported.originalPost.imageData, video_data: unsupported.originalPost.videoData });
  assert.equal("image_base64" in unsupported.calls[0].body, false);
  assert.equal("video_base64" in unsupported.calls[0].body, false);
  assert.match([...unsupported.rows.values()][0].mediaWarning, /Unsupported image format/);
  assert.equal([...unsupported.rows.values()][0].status, "acknowledged");
});

test("BMC validation-compatible media fields retain complete normalized data URLs", async () => {
  for (const mime of ["image/jpeg", "image/png", "image/webp"]) {
    const h = harness();
    const dataUrl = `data:${mime};base64,aGVsbG8=\n`;
    h.originalPost.imageData = dataUrl;
    await h.integration.processPost(h.publicPost);
    assert.equal(h.calls[0].body.image_base64, `data:${mime};base64,aGVsbG8=`);
    assert.match(h.calls[0].body.image_base64, /^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/);
  }

  const h = harness();
  h.originalPost.videoData = "data:video/mp4;base64,AAECAw==";
  await h.integration.processPost(h.publicPost);
  assert.equal(h.calls[0].body.video_base64, "data:video/mp4;base64,AAECAw==");
  assert.match(h.calls[0].body.video_base64, /^data:video\/mp4;base64,[A-Za-z0-9+/=]+$/);
});

test("BMC authentication errors stop retries; temporary unavailability is retried with original identity", async () => {
  const unauthorized = harness({ fetchResponses: [{ status: 401, body: { detail: "unauthorized" } }] });
  const rejected = await unauthorized.integration.processPost(unauthorized.publicPost);
  assert.equal(rejected.status, "failed");
  assert.match(rejected.lastError, /HTTP 401/);

  const transient = harness({ fetchResponses: [{ status: 503, body: {} }, successfulResponse()] });
  const waiting = await transient.integration.processPost(transient.publicPost);
  assert.equal(waiting.status, "retry");
  assert.equal(waiting.attemptCount, 1);
  transient.setClock("2026-10-14T00:00:31.000Z");
  await transient.integration.processPending();
  assert.equal(transient.calls.length, 2);
  assert.equal(transient.calls[0].body.original_post_id, transient.calls[1].body.original_post_id);
  assert.equal(transient.rows.get("317").status, "acknowledged");
});

test("configuration and BMC validation failures are recorded without undoing the CivicSocial post", async () => {
  const missingConfig = harness({ configOverrides: { bmcApiBaseUrl: "" } });
  const pending = await missingConfig.integration.processPost(missingConfig.publicPost);
  assert.equal(pending.status, "retry");
  assert.match(pending.lastError, /BMC_API_BASE_URL is required/);
  assert.equal(missingConfig.calls.length, 0);

  const validation = harness({ fetchResponses: [{ status: 422, body: { detail: "invalid payload" } }] });
  const failed = await validation.integration.processPost(validation.publicPost);
  assert.equal(failed.status, "failed");
  assert.match(failed.lastError, /HTTP 422/);
  assert.equal(validation.originalPost.id, validation.publicPost.id);
  assert.equal(validation.replies.length, 0);

  const malformed = harness({ fetchResponses: [{ status: 201, body: { status: "received" } }] });
  const invalidResponse = await malformed.integration.processPost(malformed.publicPost);
  assert.equal(invalidResponse.status, "retry");
  assert.match(invalidResponse.lastError, /without complaint_reference/);
  assert.equal(malformed.replies.length, 0);
  malformed.setClock("2026-10-14T00:00:31.000Z");
  await malformed.integration.processPending();
  assert.equal(malformed.rows.get("317").status, "acknowledged");
  assert.equal(malformed.calls.length, 2);
});

test("BMC credentials are not copied into delivery errors or logs", async () => {
  const h = harness({ fetchResponses: [new Error("authorization rejected mock-secret-only")] });
  const delivery = await h.integration.processPost(h.publicPost);
  assert.equal(delivery.status, "retry");
  assert.doesNotMatch(delivery.lastError, /mock-secret-only/);
  assert.ok(h.logs.every(message => !message.includes("mock-secret-only")));
});

test("a CivicSocial post remains successful when the BMC service is unavailable", async () => {
  const h = harness({ fetchResponses: [new Error("BMC service unavailable")] });
  const result = await h.integration.processPost(h.publicPost);
  assert.equal(result.status, "retry");
  assert.equal(h.originalPost.id, h.publicPost.id);
  assert.equal(h.replies.length, 0);
});

test("duplicate delivery calls and concurrent requests send only once and acknowledge once", async () => {
  const sequential = harness();
  await sequential.integration.processPost(sequential.publicPost);
  await sequential.integration.processPost(sequential.publicPost);
  assert.equal(sequential.calls.length, 1);
  assert.equal(sequential.replies.length, 1);
  assert.equal(sequential.ackAttempts, 1);

  const concurrent = harness({ fetchDelay: 20 });
  await Promise.all([
    concurrent.integration.processPost(concurrent.publicPost),
    concurrent.integration.processPost(concurrent.publicPost),
  ]);
  assert.equal(concurrent.calls.length, 1);
  assert.equal(concurrent.replies.length, 1);
});

test("missing official account retains registration and waits for safe provisioning", async () => {
  const h = harness({ official: false, fetchResponses: [successfulResponse()] });
  const delivery = await h.integration.processPost(h.publicPost);
  assert.equal(delivery.status, "registered");
  assert.equal(delivery.complaintReference, "CMP-1234567890");
  assert.match(delivery.lastError, /provision the @mybmc account/);
  assert.equal(h.replies.length, 0);
  assert.match(h.logs.join("\n"), /official @mybmc account is missing/);
  h.setOfficial(true);
  h.setClock("2026-10-14T00:05:01.000Z");
  await h.integration.processPending();
  assert.equal(h.rows.get("317").status, "acknowledged");
  assert.equal(h.calls.length, 1);
  assert.equal(h.replies.length, 1);
});

test("an existing ordinary mybmc user is never silently promoted", async () => {
  const h = harness({ official: "unofficial", fetchResponses: [successfulResponse()] });
  const delivery = await h.integration.processPost(h.publicPost);
  assert.equal(delivery.status, "registered");
  assert.equal(delivery.lastError.includes("isOfficial=true"), true);
  assert.equal(h.replies.length, 0);
  assert.equal(h.calls.length, 1);
});

test("acknowledgement recovery finds a reply persisted before a partial failure", async () => {
  const h = harness({ ackFailsAfterCreate: true, fetchResponses: [successfulResponse()] });
  const pending = await h.integration.processPost(h.publicPost);
  assert.equal(pending.status, "registered");
  assert.equal(h.replies.length, 1);
  h.setClock("2026-10-14T00:00:31.000Z");
  await h.integration.processPending();
  assert.equal(h.replies.length, 1);
  assert.equal(h.rows.get("317").status, "acknowledged");
});

test("CivicSocial post route persists through the existing adapter before optional BMC handoff", async () => {
  const server = await readFile(resolve(root, "backend/src/server.js"), "utf8");
  const adapter = await readFile(resolve(root, "backend/src/social-platform.js"), "utf8");
  const postPersistence = await readFile(resolve(root, "backend/src/post-persistence.js"), "utf8");
  const route = server.match(/app\.post\("\/posts"[\s\S]*?\n\}\)\);/)?.[0] || "";
  assert.ok(route.indexOf("await socialPlatform.createPost") < route.indexOf("await bmcIntegration.processPost"));
  assert.match(route, /res\.status\(201\)\.json/);
  assert.match(adapter, /createPostWithNotifications/);
  assert.match(postPersistence, /Post\.create\(\{ authorId, text, imageData, videoData, parentPostId \}\)/);
  assert.match(postPersistence, /type: "reply"/);
  assert.match(postPersistence, /type: "mention"/);
});
