import { Follow, Like, Post, User } from "./models.js";

export function avatarUrl(user) {
  return user.avatarData || `https://api.dicebear.com/9.x/initials/svg?seed=${encodeURIComponent(user.avatarSeed)}`;
}

export async function publicUser(user, viewerId = null) {
  const [followers, following, followedByViewer] = await Promise.all([
    Follow.count({ where: { followeeId: user.id } }),
    Follow.count({ where: { followerId: user.id } }),
    viewerId && viewerId !== user.id ? Follow.findOne({ where: { followerId: viewerId, followeeId: user.id } }) : null,
  ]);
  return { id: user.id, handle: user.handle, display_name: user.displayName, bio: user.bio, avatar: avatarUrl(user), is_official: user.isOfficial, follower_count: followers, following_count: following, is_followed_by_viewer: Boolean(followedByViewer), created_at: user.createdAt };
}

export async function publicPost(post, viewerId = null) {
  const author = post.author || await User.findByPk(post.authorId);
  const [likeCount, replyCount, likedByViewer] = await Promise.all([
    Like.count({ where: { postId: post.id } }),
    Post.count({ where: { parentPostId: post.id } }),
    viewerId ? Like.findOne({ where: { postId: post.id, userId: viewerId } }) : null,
  ]);
  return { id: post.id, text: post.text, image_data: post.imageData, video_data: post.videoData, parent_post_id: post.parentPostId, created_at: post.createdAt, author: await publicUser(author, viewerId), like_count: likeCount, reply_count: replyCount, liked_by_viewer: Boolean(likedByViewer) };
}
