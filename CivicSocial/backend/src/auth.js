import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import { OAuth2Client } from "google-auth-library";
import { config } from "./config.js";
import { User } from "./models.js";
import { requireVerifiedEmail, verifyGoogleIdentity } from "./auth-policy.js";

const googleClient = new OAuth2Client();

export const hashPassword = (password) => bcrypt.hash(password, 12);
export const passwordsMatch = (password, hash) => bcrypt.compare(password, hash);
export const issueToken = (user) => jwt.sign({ sub: user.id, handle: user.handle }, config.jwtSecret, { expiresIn: config.jwtExpiresIn });

export async function requireAuth(req, res, next) {
  const value = req.headers.authorization || "";
  const token = value.startsWith("Bearer ") ? value.slice(7) : null;
  if (!token) return res.status(401).json({ error: "Authentication required" });
  try {
    const payload = jwt.verify(token, config.jwtSecret);
    req.user = await User.findByPk(payload.sub);
    if (!req.user) return res.status(401).json({ error: "Account no longer exists" });
  } catch {
    return res.status(401).json({ error: "Invalid or expired token" });
  }
  try { requireVerifiedEmail(req.user); }
  catch (error) { return res.status(error.status).json({ error: error.message, error_code: error.code, email: error.email }); }
  next();
}

// Enrich public reads for a signed-in viewer without requiring a token.
export async function optionalAuth(req, _res, next) {
  const value = req.headers.authorization || "";
  const token = value.startsWith("Bearer ") ? value.slice(7) : null;
  if (!token) return next();
  try {
    const payload = jwt.verify(token, config.jwtSecret);
    const user = await User.findByPk(payload.sub);
    if (user?.emailVerified) req.user = user;
  } catch {
    // Invalid optional credentials still permit anonymous public reads.
  }
  next();
}

export async function verifyGoogleToken(idToken) {
  return verifyGoogleIdentity(idToken, {
    clientId: config.googleClientId,
    verifyIdToken: async (credential, clientId) => {
      const ticket = await googleClient.verifyIdToken({ idToken: credential, audience: clientId });
      return ticket.getPayload();
    },
  });
}
