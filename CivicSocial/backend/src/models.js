import { DataTypes } from "sequelize";
import { sequelize } from "./db.js";

export const User = sequelize.define("User", {
  id: { type: DataTypes.INTEGER, primaryKey: true, autoIncrement: true },
  handle: { type: DataTypes.STRING(30), allowNull: false, unique: true },
  displayName: { type: DataTypes.STRING(80), allowNull: false },
  email: { type: DataTypes.STRING(254), allowNull: false, unique: true },
  emailVerified: { type: DataTypes.BOOLEAN, allowNull: false, defaultValue: false },
  bio: { type: DataTypes.TEXT, allowNull: false, defaultValue: "" },
  avatarSeed: { type: DataTypes.STRING(100), allowNull: false },
  avatarData: { type: DataTypes.TEXT, allowNull: true },
  passwordHash: { type: DataTypes.STRING, allowNull: true },
  isOfficial: { type: DataTypes.BOOLEAN, allowNull: false, defaultValue: false },
}, { tableName: "users", underscored: true, updatedAt: false });

export const Post = sequelize.define("Post", {
  id: { type: DataTypes.INTEGER, primaryKey: true, autoIncrement: true },
  text: { type: DataTypes.TEXT, allowNull: false, defaultValue: "" },
  imageData: { type: DataTypes.TEXT, allowNull: true },
  videoData: { type: DataTypes.TEXT, allowNull: true },
}, { tableName: "posts", underscored: true, updatedAt: false });

export const Follow = sequelize.define("Follow", {
  followerId: { type: DataTypes.INTEGER, primaryKey: true },
  followeeId: { type: DataTypes.INTEGER, primaryKey: true },
}, { tableName: "follows", underscored: true, timestamps: false });

export const Like = sequelize.define("Like", {
  userId: { type: DataTypes.INTEGER, primaryKey: true },
  postId: { type: DataTypes.INTEGER, primaryKey: true },
}, { tableName: "likes", underscored: true, timestamps: false });

export const Notification = sequelize.define("Notification", {
  id: { type: DataTypes.INTEGER, primaryKey: true, autoIncrement: true },
  type: { type: DataTypes.ENUM("like", "follow", "reply", "mention"), allowNull: false },
  read: { type: DataTypes.BOOLEAN, allowNull: false, defaultValue: false },
}, { tableName: "notifications", underscored: true, updatedAt: false });

export const EmailToken = sequelize.define("EmailToken", {
  token: { type: DataTypes.STRING(128), primaryKey: true },
  purpose: { type: DataTypes.ENUM("verify", "reset"), allowNull: false },
  expiresAt: { type: DataTypes.DATE, allowNull: false },
}, { tableName: "email_tokens", underscored: true, createdAt: false, updatedAt: false });

// Additive outbox table for idempotent CivicSocial -> BMC complaint delivery.
export const BmcComplaintDelivery = sequelize.define("BmcComplaintDelivery", {
  id: { type: DataTypes.INTEGER, primaryKey: true, autoIncrement: true },
  originalPostId: { type: DataTypes.STRING(32), allowNull: false, unique: true },
  status: { type: DataTypes.STRING(24), allowNull: false, defaultValue: "pending" },
  attemptCount: { type: DataTypes.INTEGER, allowNull: false, defaultValue: 0 },
  nextAttemptAt: { type: DataTypes.DATE, allowNull: true },
  lockedUntil: { type: DataTypes.DATE, allowNull: true },
  lastError: { type: DataTypes.TEXT, allowNull: true },
  mediaWarning: { type: DataTypes.TEXT, allowNull: true },
  complaintReference: { type: DataTypes.TEXT, allowNull: true },
  acknowledgementPostId: { type: DataTypes.INTEGER, allowNull: true },
}, { tableName: "bmc_complaint_deliveries", underscored: true });

User.hasMany(Post, { as: "posts", foreignKey: "authorId" });
Post.belongsTo(User, { as: "author", foreignKey: "authorId" });
Post.belongsTo(Post, { as: "parent", foreignKey: "parentPostId" });
Post.hasMany(Post, { as: "replies", foreignKey: "parentPostId" });
User.belongsToMany(User, { as: "following", through: Follow, foreignKey: "followerId", otherKey: "followeeId" });
User.belongsToMany(User, { as: "followers", through: Follow, foreignKey: "followeeId", otherKey: "followerId" });
User.belongsToMany(Post, { as: "likedPosts", through: Like, foreignKey: "userId", otherKey: "postId" });
Post.belongsToMany(User, { as: "likedBy", through: Like, foreignKey: "postId", otherKey: "userId" });
Notification.belongsTo(User, { as: "recipient", foreignKey: "recipientUserId" });
Notification.belongsTo(User, { as: "actor", foreignKey: "actorUserId" });
Notification.belongsTo(Post, { as: "post", foreignKey: "postId" });
User.hasMany(EmailToken, { foreignKey: "userId" });
EmailToken.belongsTo(User, { foreignKey: "userId" });
