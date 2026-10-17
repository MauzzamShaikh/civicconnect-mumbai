import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import test from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

test("persisted reply notifications are returned by the API and polled into the unread badge", async () => {
  const server = await readFile(resolve(root, "backend/src/server.js"), "utf8");
  const frontend = await readFile(resolve(root, "frontend/app.js"), "utf8");
  const listRoute = server.match(/app\.get\("\/notifications"[\s\S]*?\n\}\)\);/)?.[0] || "";
  const readRoute = server.match(/app\.post\("\/notifications\/:id\/read"[\s\S]*?\n\}\)\);/)?.[0] || "";
  assert.match(listRoute, /Notification\.findAll\(\{ where: \{ recipientUserId: req\.user\.id \}/);
  assert.match(listRoute, /read: item\.read/);
  assert.match(readRoute, /recipientUserId: req\.user\.id/);
  assert.match(readRoute, /notification\.update\(\{ read: true \}\)/);
  assert.match(frontend, /setInterval\(\(\) => \{[\s\S]*?refreshNotifBadge\(\)[\s\S]*?\}, 15000\)/);
  assert.match(frontend, /window\.addEventListener\('focus',[\s\S]*?refreshNotifBadge\(\)/);
  assert.match(frontend, /data\.notifications\.filter\(item => !item\.read\)\.length/);
});
