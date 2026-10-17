import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { createUserSearch } from "../src/user-search.js";
import { demoPosts, demoReplies } from "../src/demo-post-seeds.js";
import { seedDemoPosts } from "../src/demo-seeder.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const operators = { ne: Symbol("ne"), notIn: Symbol("notIn"), or: Symbol("or"), iLike: Symbol("iLike"), in: Symbol("in") };

test("user search ranks followed matching accounts first and exposes suggestion fields only", async () => {
  const users = [
    { id: 3, handle: "mahi", displayName: "Mahi Rao", avatarData: null, avatarSeed: "mahi", isOfficial: true, email: "private@example.com" },
    { id: 2, handle: "mahesh", displayName: "Mahesh Patil", avatarData: "data:image/png;base64,abc", avatarSeed: "mahesh", isOfficial: false, passwordHash: "secret" },
    { id: 1, handle: "viewer", displayName: "Viewer", avatarSeed: "viewer", isOfficial: false },
  ];
  const searchUsers = createUserSearch({ operators,
    UserModel: { async findAll(options) {
      const include = options.where.id[operators.in];
      const exclude = options.where.id[operators.notIn];
      return users.filter(user => (include ? include.includes(user.id) : !exclude.includes(user.id))).slice(0, options.limit);
    } },
    FollowModel: { async findAll() { return [{ followeeId: 3 }]; } },
  });
  const results = await searchUsers({ query: "mah", viewerId: 1 });
  assert.deepEqual(results.map(user => user.handle), ["mahi", "mahesh"]);
  assert.equal(results[0].is_followed_by_viewer, true);
  assert.equal(results[0].is_official, true);
  assert.deepEqual(Object.keys(results[0]).sort(), ["avatar", "display_name", "handle", "id", "is_followed_by_viewer", "is_official"].sort());
});

test("user search validates query length and caps requested result count", async () => {
  let options;
  const searchUsers = createUserSearch({ operators, UserModel: { async findAll(value) { options = value; return []; } }, FollowModel: { async findAll() { return []; } } });
  await assert.rejects(searchUsers({ query: "x".repeat(31), viewerId: 1 }), error => error.status === 400 && error.code === "INVALID_USER_SEARCH");
  await searchUsers({ query: "", viewerId: 1, limit: 500 });
  assert.equal(options.limit, 20);
});

test("mention autocomplete endpoint requires auth and thread view filters exact parent replies", async () => {
  const server = await readFile(resolve(root, "backend/src/server.js"), "utf8");
  const app = await readFile(resolve(root, "frontend/app.js"), "utf8");
  const styles = await readFile(resolve(root, "frontend/styles.css"), "utf8");
  assert.match(server, /app\.get\("\/users\/search", requireAuth/);
  assert.match(server, /searchUsers\(\{ query: req\.query\.q \|\| "", viewerId: req\.user\.id \}\)/);
  assert.match(app, /filter\(reply => Number\(reply\.parent_post_id\) === Number\(post\.id\)\)/);
  assert.match(app, /repliesSection\.append\(tmp\.firstElementChild\)/);
  assert.match(app, /onkeydown="handleMentionKeydown\(event, this\)"/);
  assert.match(app, /mentionTokenAtCursor/);
  assert.match(app, /playsinline src=/);
  assert.match(app, /classList\.add\('media-error'\)/);
  assert.match(styles, /\.mention-suggestions/);
  assert.match(styles, /@media \(max-width: 700px\)[\s\S]*?\.thread-composer/);
});

test("demo content is fictional, opt-in seed includes media and idempotent meaningful replies", async () => {
  const seed = await readFile(resolve(root, "backend/src/demo-seeder.js"), "utf8");
  assert.equal(demoPosts.length, 29);
  assert.ok(demoPosts.some(row => row[2]));
  assert.ok(demoPosts.some(row => row[3]));
  assert.ok(demoReplies.length >= 8);
  assert.match(seed, /Demo post seeding is disabled in production/);
  assert.match(seed, /findOne\(\{ where: \{ authorId: user\.id, text, parentPostId: null \} \}\)/);
  assert.doesNotMatch(demoPosts.map(row => row[1]).join("\n"), /@mybmc/i);
});

test("every demo reply points to its intended conversation", () => {
  const parentTopics = [
    /quiet cafe/i, /quiet cafe/i,
    /close finish/i, /close finish/i,
    /herb garden/i, /herb garden/i,
    /fellow commuters/i, /fellow commuters/i,
    /leg-spin/i,
    /free afternoon/i,
  ];
  assert.equal(demoReplies.length, parentTopics.length);
  demoReplies.forEach(([parentIndex], replyIndex) => {
    assert.ok(Number.isInteger(parentIndex) && parentIndex >= 1 && parentIndex <= demoPosts.length,
      `reply ${replyIndex + 1} has an out-of-range 1-based parent index`);
    assert.match(demoPosts[parentIndex - 1][1], parentTopics[replyIndex],
      `reply ${replyIndex + 1} is attached to an unrelated demo post`);
  });
});

test("demo seeding is additive and idempotent across repeated runs", async () => {
  const users = [...new Set([...demoPosts.map(row => row[0]), ...demoReplies.map(row => row[1])])]
    .map((handle, index) => ({ id: index + 1, handle, isOfficial: false, email: `${handle}@seed.civicsocial.local` }));
  const posts = [];
  let id = 0;
  const models = {
    UserModel: { async findAll() { return users; } },
    PostModel: {
      async findOne({ where }) { return posts.find(post => String(post.authorId) === String(where.authorId) && String(post.parentPostId ?? "") === String(where.parentPostId ?? "") && post.text === where.text) || null; },
      async create(input) { const post = { id: ++id, ...input }; posts.push(post); return post; },
    },
    logger: { log() {} },
  };
  const first = await seedDemoPosts({ ...models, operators, production: false });
  const originalCount = posts.length;
  const second = await seedDemoPosts({ ...models, operators, production: false });
  assert.equal(first.postsCreated, demoPosts.length);
  assert.equal(first.repliesCreated, demoReplies.length);
  assert.equal(second.postsCreated, 0);
  assert.equal(second.repliesCreated, 0);
  assert.equal(posts.length, originalCount);
  await assert.rejects(seedDemoPosts({ ...models, operators, production: true }), /disabled in production/);
});
