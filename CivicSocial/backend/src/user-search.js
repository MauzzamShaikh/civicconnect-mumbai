/** Search public account fields and put followed matches before other matches. */
export function createUserSearch({ UserModel, FollowModel, operators: Op }) {
  return async function searchUsers({ query = "", viewerId, limit = 20 }) {
    const normalized = String(query).trim();
    if (normalized.length > 30) {
      const error = new Error("Search query must be 30 characters or fewer");
      error.status = 400;
      error.code = "INVALID_USER_SEARCH";
      throw error;
    }

    // Escape SQL LIKE metacharacters so the query is interpreted literally.
    const escaped = normalized.replace(/[\\%_]/g, "\\$&");
    const max = Math.min(Math.max(Number(limit) || 20, 1), 20);
    const follows = await FollowModel.findAll({ where: { followerId: viewerId }, attributes: ["followeeId"] });
    const followedIds = [...new Set(follows.map(follow => Number(follow.followeeId)))];
    const matchingFields = normalized ? { [Op.or]: [
      { handle: { [Op.iLike]: `%${escaped}%` } },
      { displayName: { [Op.iLike]: `%${escaped}%` } },
    ] } : {};
    const fields = ["id", "handle", "displayName", "avatarData", "avatarSeed", "isOfficial"];
    const followedMatches = followedIds.length ? await UserModel.findAll({
      where: { id: { [Op.in]: followedIds }, ...matchingFields },
      attributes: fields,
      order: [["handle", "ASC"]],
      limit: max,
    }) : [];
    const rankedFollowed = new Set(followedMatches.map(user => Number(user.id)));
    const remaining = max - followedMatches.length;
    const otherMatches = remaining ? await UserModel.findAll({
      where: { id: { [Op.notIn]: [Number(viewerId), ...rankedFollowed] }, ...matchingFields },
      attributes: fields,
      order: [["handle", "ASC"]],
      limit: remaining,
    }) : [];
    const matches = [...followedMatches, ...otherMatches];
    if (!matches.length) return [];
    const followedSet = new Set(followedIds);

    return matches.map(user => ({
      id: user.id,
      handle: user.handle,
      display_name: user.displayName,
      avatar: user.avatarData || `https://api.dicebear.com/9.x/initials/svg?seed=${encodeURIComponent(user.avatarSeed)}`,
      is_official: Boolean(user.isOfficial),
      is_followed_by_viewer: followedSet.has(Number(user.id)),
    }));
  };
}
