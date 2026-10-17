import assert from "node:assert/strict";
import test from "node:test";
import { createPostWithNotifications } from "../src/post-persistence.js";

test("a BMC acknowledgement reply persists a reply notification for the original author", async () => {
  const notifications = [];
  const parent = { id: 24, authorId: 7 };
  const reply = { id: 25, authorId: 1, parentPostId: 24, text: "Registered with BMC." };
  const result = await createPostWithNotifications({
    Post: {
      async findByPk(id) { return Number(id) === parent.id ? parent : null; },
      async create(values) { return { ...reply, ...values }; },
    },
    User: { async findAll() { return []; } },
    Notification: { async create(values) { notifications.push(values); } },
    serializePost: async post => ({ id: post.id, parent_post_id: post.parentPostId }),
  }, {
    authorId: 1,
    text: "Registered with BMC.",
    parentPostId: parent.id,
  });

  assert.deepEqual(result, { id: 25, parent_post_id: 24 });
  assert.deepEqual(notifications, [{
    recipientUserId: 7,
    actorUserId: 1,
    postId: 25,
    type: "reply",
  }]);
});
