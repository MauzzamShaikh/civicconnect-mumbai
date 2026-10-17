import assert from "node:assert/strict";
import test from "node:test";

const baseUrl = process.env.CIVICSOCIAL_FRONTEND_URL;

test("rebuilt CivicSocial serves the imported submission module as JavaScript", {
  skip: !baseUrl && "Set CIVICSOCIAL_FRONTEND_URL to run the live frontend check",
}, async () => {
  const response = await fetch(new URL("/post-submission.mjs", baseUrl));
  assert.equal(response.status, 200, "submission module must exist in the deployed frontend image");
  assert.match(response.headers.get("content-type") || "", /^application\/(?:javascript|x-javascript)(?:;|$)/i);
  const source = await response.text();
  assert.match(source, /export function createSubmissionController/);
  assert.match(source, /finally\s*\{/);
});
