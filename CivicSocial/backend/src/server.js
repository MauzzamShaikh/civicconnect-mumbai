import crypto from "crypto";
import cors from "cors";
import express from "express";
import { Op } from "sequelize";
import { config } from "./config.js";
import { sequelize } from "./db.js";
import { hashPassword, issueToken, optionalAuth, passwordsMatch, requireAuth, verifyGoogleToken } from "./auth.js";
import { createEmailToken, sendActionEmail } from "./email.js";
import { BmcComplaintDelivery, EmailToken, Follow, Notification, Post, User } from "./models.js";
import { publicUser } from "./serializers.js";
import { socialPlatform } from "./social-platform.js";
import { seed } from "./seed.js";
import { createEmailVerificationService } from "./email-verification.js";
import { requireVerifiedEmail, signupVerificationResponse } from "./auth-policy.js";
import { createBmcIntegration } from "./bmc-integration.js";
import { createUserSearch } from "./user-search.js";

const app = express();
app.use(cors());
app.use(express.json({ limit: "50mb" }));
// API responses can contain viewer-specific state, authentication material,
// private messages, or media. Never let browsers or shared caches retain them.
app.use((_req, res, next) => {
  res.setHeader("Cache-Control", "no-store, private");
  next();
});
const asyncRoute = (handler) => (req, res, next) => Promise.resolve(handler(req, res, next)).catch(next);
const emailVerification = createEmailVerificationService({ User, EmailToken, createEmailToken, sendActionEmail, greaterThan: Op.gt });
const bmcIntegration = createBmcIntegration({ Delivery: BmcComplaintDelivery, User, Post, socialPlatform, config, operators: Op });
const searchUsers = createUserSearch({ UserModel: User, FollowModel: Follow, operators: Op });

function safeHandle(value) { return String(value || "").replace(/^@/, "").toLowerCase(); }
function authResponse(user) { return { access_token: issueToken(user), token_type: "bearer", user: { id: user.id, handle: user.handle, display_name: user.displayName, is_official: user.isOfficial } }; }

app.post("/auth/signup", asyncRoute(async (req, res) => {
  const { handle, display_name, email, password, bio = "", avatar_data = null } = req.body;
  if (!handle || !display_name || !email || !password) return res.status(400).json({ error: "handle, display_name, email, and password are required" });
  const normalizedHandle = safeHandle(handle);
  if (!/^[a-z0-9_]{3,30}$/.test(normalizedHandle)) return res.status(400).json({ error: "Handle must be 3-30 letters, numbers, or underscores" });
  if (await User.findOne({ where: { [Op.or]: [{ handle: normalizedHandle }, { email: email.trim().toLowerCase() }] } })) return res.status(409).json({ error: "Handle or email is already registered" });
  const normalizedEmail = email.trim().toLowerCase();
  const user = await User.create({ handle: normalizedHandle, displayName: display_name, email: normalizedEmail, emailVerified: false, bio, avatarSeed: normalizedHandle, avatarData: avatar_data, passwordHash: await hashPassword(password) });
  const emailSent = await emailVerification.sendSignupVerification(user);
  res.status(202).json(signupVerificationResponse(user.email, emailSent));
}));

app.post("/auth/login", asyncRoute(async (req, res) => {
  const { handle, password } = req.body;
  const user = await User.findOne({ where: { handle: safeHandle(handle) } });
  if (!user || !user.passwordHash || !(await passwordsMatch(password, user.passwordHash))) return res.status(401).json({ error: "Invalid handle or password" });
  try { requireVerifiedEmail(user); }
  catch (error) { return res.status(error.status).json({ error: error.message, error_code: error.code, email: error.email }); }
  res.json(authResponse(user));
}));

app.get("/auth/verify-email", asyncRoute(async (req, res) => {
  if (!await emailVerification.verify(req.query.token)) return res.status(400).json({ error: "Invalid or expired verification token", error_code: "EMAIL_VERIFICATION_INVALID" });
  res.json({ message: "Email verified" });
}));

app.post("/auth/resend-verification", asyncRoute(async (req, res) => {
  res.status(202).json(await emailVerification.resend(req.body.email));
}));

app.get("/auth/config", (_req, res) => res.json({ google_client_id: config.googleClientId || null }));

app.get("/auth/session", requireAuth, (req, res) => {
  res.json({ user: { id: req.user.id, handle: req.user.handle, display_name: req.user.displayName, is_official: req.user.isOfficial } });
});

app.post("/auth/forgot-password", asyncRoute(async (req, res) => {
  const user = await User.findOne({ where: { email: String(req.body.email || "").toLowerCase() } });
  if (user) { const token = await createEmailToken(user.id, "reset"); await sendActionEmail(user.email, "reset", token); }
  res.json({ message: "If that email is registered, a reset link has been sent." });
}));

app.post("/auth/reset-password", asyncRoute(async (req, res) => {
  const token = await EmailToken.findOne({ where: { token: req.body.token, purpose: "reset", expiresAt: { [Op.gt]: new Date() } } });
  if (!token || !req.body.new_password) return res.status(400).json({ error: "Invalid or expired reset token" });
  await User.update({ passwordHash: await hashPassword(req.body.new_password) }, { where: { id: token.userId } });
  await token.destroy();
  res.json({ message: "Password reset" });
}));

app.post("/auth/google", asyncRoute(async (req, res) => {
  const claims = await verifyGoogleToken(req.body.google_id_token);
  let user = await User.findOne({ where: { email: claims.email.toLowerCase() } });
  if (!user) {
    const base = safeHandle(claims.email.split("@")[0]).replace(/[^a-z0-9_]/g, "_").slice(0, 24) || "user";
    const handle = `${base}_${crypto.randomBytes(3).toString("hex")}`;
    user = await User.create({ handle, displayName: claims.name || handle, email: claims.email.toLowerCase(), emailVerified: true, avatarSeed: claims.sub, avatarData: claims.picture || null });
  } else if (!user.emailVerified) {
    await user.update({ emailVerified: true });
  }
  res.json(authResponse(user));
}));

app.patch("/users/me/profile", requireAuth, asyncRoute(async (req, res) => {
  const updates = {};
  for (const [input, column] of [["display_name", "displayName"], ["bio", "bio"], ["avatar_data", "avatarData"]]) if (input in req.body) updates[column] = req.body[input];
  await req.user.update(updates);
  res.json(await publicUser(req.user));
}));

app.post("/posts", requireAuth, asyncRoute(async (req, res) => {
  const { text = "", image_base64 = null, video_base64 = null, parent_post_id = null } = req.body;
  if (!text.trim() && !image_base64 && !video_base64) return res.status(400).json({ error: "A post needs text or media" });
  const post = await socialPlatform.createPost({ authorId: req.user.id, text, imageData: image_base64, videoData: video_base64, parentPostId: parent_post_id });
  if (!post) return res.status(404).json({ error: "Parent post not found" });
  const integration = await bmcIntegration.processPost(post);
  res.status(201).json(integration ? { ...post, integration: {
    state: integration.status,
    complaint_reference: integration.complaintReference || null,
    error: integration.lastError || null,
    media_warning: integration.mediaWarning || null,
  } } : post);
}));

app.get("/posts/feed", optionalAuth, asyncRoute(async (req, res) => {
  res.json(await socialPlatform.getFeed({ sinceId: Number(req.query.since_id || 0), viewerId: req.user?.id }));
}));

app.get("/posts/:id", optionalAuth, asyncRoute(async (req, res) => {
  const result = await socialPlatform.getPost({ id: req.params.id, viewerId: req.user?.id });
  if (!result) return res.status(404).json({ error: "Post not found" });
  res.json(result);
}));

app.get("/users/search", requireAuth, asyncRoute(async (req, res) => {
  res.json({ users: await searchUsers({ query: req.query.q || "", viewerId: req.user.id }) });
}));

app.get("/users/:handle", optionalAuth, asyncRoute(async (req, res) => {
  const profile = await socialPlatform.getProfile({ handle: req.params.handle, viewerId: req.user?.id });
  if (!profile) return res.status(404).json({ error: "User not found" });
  res.json(profile);
}));

app.post("/users/:handle/follow", requireAuth, asyncRoute(async (req, res) => {
  const following = await socialPlatform.toggleFollow({ handle: req.params.handle, followerId: req.user.id });
  if (following === null) return res.status(404).json({ error: "User not found" });
  if (following?.error === "self") return res.status(400).json({ error: "Cannot follow yourself" });
  res.json({ following });
}));

app.post("/posts/:id/like", requireAuth, asyncRoute(async (req, res) => {
  const liked = await socialPlatform.toggleLike({ postId: req.params.id, userId: req.user.id });
  if (liked === null) return res.status(404).json({ error: "Post not found" });
  res.json({ liked });
}));

app.get("/notifications", requireAuth, asyncRoute(async (req, res) => {
  const notifications = await Notification.findAll({ where: { recipientUserId: req.user.id }, include: [{ model: User, as: "actor" }, { model: Post, as: "post" }], order: [["id", "DESC"]] });
  res.json({ notifications: await Promise.all(notifications.map(async (item) => ({ id: item.id, type: item.type, read: item.read, created_at: item.createdAt, actor: await publicUser(item.actor, req.user.id), post_id: item.postId }))) });
}));

app.post("/notifications/:id/read", requireAuth, asyncRoute(async (req, res) => {
  const notification = await Notification.findOne({ where: { id: req.params.id, recipientUserId: req.user.id } });
  if (!notification) return res.status(404).json({ error: "Notification not found" });
  await notification.update({ read: true });
  res.json({ read: true });
}));

app.get("/health", (_req, res) => res.json({ ok: true }));
app.use((error, _req, res, _next) => {
  if (error.status && error.code) return res.status(error.status).json({ error: error.message, error_code: error.code });
  console.error(error);
  res.status(500).json({ error: "Internal server error" });
});

async function start() {
  await sequelize.authenticate();
  await sequelize.sync();
  if (config.seedOnStartup) await seed();
  app.listen(config.apiPort, () => console.log(`CivicSocial API listening on :${config.apiPort}`));
  bmcIntegration.processPending().catch(() => console.error("BMC delivery recovery pass failed"));
  const retryTimer = setInterval(() => bmcIntegration.processPending().catch(() => console.error("BMC delivery recovery pass failed")), 30_000);
  retryTimer.unref?.();
}
start();
