/** Persist a post and its reply/mention notifications through the configured models. */
export async function createPostWithNotifications({ User, Post, Notification, serializePost }, {
  authorId, text, imageData, videoData, parentPostId,
}) {
  const parent = parentPostId ? await Post.findByPk(parentPostId) : null;
  if (parentPostId && !parent) return null;
  const post = await Post.create({ authorId, text, imageData, videoData, parentPostId });
  if (parent && parent.authorId !== authorId) {
    await Notification.create({ recipientUserId: parent.authorId, actorUserId: authorId, postId: post.id, type: "reply" });
  }
  const handles = [...new Set((text.match(/@[a-zA-Z0-9_]+/g) || []).map(value => value.slice(1).toLowerCase()))];
  const mentioned = await User.findAll({ where: { handle: handles } });
  for (const user of mentioned) if (user.id !== authorId) await Notification.create({ recipientUserId: user.id, actorUserId: authorId, postId: post.id, type: "mention" });
  return serializePost(post, authorId);
}
