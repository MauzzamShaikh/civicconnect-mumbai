import { Op } from "sequelize";
import { config } from "./config.js";
import { Follow, Like, Notification, Post, User } from "./models.js";
import { publicPost, publicUser } from "./serializers.js";
import { createPostWithNotifications } from "./post-persistence.js";

/** Normalized operations every platform adapter must provide to the API. */
export class SocialPlatform {
  async getFeed() { throw new Error("getFeed is not implemented"); }
  async createPost() { throw new Error("createPost is not implemented"); }
  async getPost() { throw new Error("getPost is not implemented"); }
  async getProfile() { throw new Error("getProfile is not implemented"); }
  async toggleLike() { throw new Error("toggleLike is not implemented"); }
  async toggleFollow() { throw new Error("toggleFollow is not implemented"); }
}

/** CivicSocial's existing PostgreSQL-backed behavior, exposed through the adapter contract. */
export class CivicSocialPlatform extends SocialPlatform {
  async getFeed({ sinceId = 0, viewerId = null }) {
    const posts = await Post.findAll({ where: { id: { [Op.gt]: sinceId }, parentPostId: null }, include: [{ model: User, as: "author" }], order: [["id", "DESC"]] });
    return { posts: await Promise.all(posts.map(post => publicPost(post, viewerId))), next_since_id: posts[0]?.id || sinceId };
  }

  async createPost({ authorId, text, imageData, videoData, parentPostId }) {
    return createPostWithNotifications({ User, Post, Notification, serializePost: publicPost }, { authorId, text, imageData, videoData, parentPostId });
  }

  async getPost({ id, viewerId = null }) {
    const post = await Post.findByPk(id, { include: [{ model: User, as: "author" }] });
    if (!post) return null;
    const replies = await Post.findAll({ where: { parentPostId: post.id }, include: [{ model: User, as: "author" }], order: [["id", "ASC"]] });
    return { post: await publicPost(post, viewerId), replies: await Promise.all(replies.map(reply => publicPost(reply, viewerId))) };
  }

  async getProfile({ handle, viewerId = null }) {
    const user = await User.findOne({ where: { handle: String(handle || "").replace(/^@/, "").toLowerCase() } });
    if (!user) return null;
    const posts = await Post.findAll({ where: { authorId: user.id }, include: [{ model: User, as: "author" }], order: [["id", "DESC"]], limit: 50 });
    return { user: await publicUser(user, viewerId), posts: await Promise.all(posts.map(post => publicPost(post, viewerId))) };
  }

  async toggleLike({ postId, userId }) {
    const post = await Post.findByPk(postId);
    if (!post) return null;
    const existing = await Like.findOne({ where: { userId, postId: post.id } });
    if (existing) { await existing.destroy(); return false; }
    await Like.create({ userId, postId: post.id });
    if (post.authorId !== userId) await Notification.create({ recipientUserId: post.authorId, actorUserId: userId, postId: post.id, type: "like" });
    return true;
  }

  async toggleFollow({ handle, followerId }) {
    const target = await User.findOne({ where: { handle: String(handle || "").replace(/^@/, "").toLowerCase() } });
    if (!target) return null;
    if (target.id === followerId) return { error: "self" };
    const existing = await Follow.findOne({ where: { followerId, followeeId: target.id } });
    if (existing) { await existing.destroy(); return false; }
    await Follow.create({ followerId, followeeId: target.id });
    await Notification.create({ recipientUserId: target.id, actorUserId: followerId, type: "follow" });
    return true;
  }
}

const providers = { civicsocial: CivicSocialPlatform };
const Provider = providers[config.socialPlatform];
if (!Provider) throw new Error(`Unsupported SOCIAL_PLATFORM: ${config.socialPlatform}`);

// Only CivicSocial is implemented today. Provider credentials, when a remote
// adapter is added, belong in backend configuration and never in the SPA.
export const socialPlatform = new Provider();
