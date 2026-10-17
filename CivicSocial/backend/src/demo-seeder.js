import { demoPosts, demoReplies } from "./demo-post-seeds.js";

/** Add curated demo feed items only to existing seed accounts; never mutate existing records. */
export async function seedDemoPosts({ UserModel, PostModel, operators: Op, production = false, logger = console }) {
  if (production) throw new Error("Demo post seeding is disabled in production");
  const users = await UserModel.findAll({ where: { isOfficial: false, email: { [Op.iLike]: "%@seed.civicsocial.local" } } });
  const byHandle = new Map(users.map(user => [user.handle, user]));
  let postsCreated = 0;
  let repliesCreated = 0;
  const seededPosts = [];

  for (const [handle, text, imageData = null, videoData = null] of demoPosts) {
    const user = byHandle.get(handle);
    if (!user) { seededPosts.push(null); continue; }
    let post = await PostModel.findOne({ where: { authorId: user.id, text, parentPostId: null } });
    if (!post) {
      post = await PostModel.create({ authorId: user.id, text, imageData: imageData || null, videoData: videoData || null, parentPostId: null });
      postsCreated++;
    }
    seededPosts.push(post);
  }

  for (const [parentIndex, handle, text] of demoReplies) {
    const parent = seededPosts[parentIndex - 1];
    const user = byHandle.get(handle);
    if (!parent || !user || Number(parent.authorId) === Number(user.id)) continue;
    const existing = await PostModel.findOne({ where: { authorId: user.id, parentPostId: parent.id, text } });
    if (!existing) {
      await PostModel.create({ authorId: user.id, text, parentPostId: parent.id });
      repliesCreated++;
    }
  }
  logger.log?.(`Demo feed seed complete: ${postsCreated} posts and ${repliesCreated} replies added.`);
  return { postsCreated, repliesCreated };
}
