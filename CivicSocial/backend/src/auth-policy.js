export class AuthFlowError extends Error {
  constructor(status, code, message, details = {}) {
    super(message);
    this.status = status;
    this.code = code;
    Object.assign(this, details);
  }
}

export function requireVerifiedEmail(user) {
  if (!user.emailVerified) {
    throw new AuthFlowError(403, "EMAIL_NOT_VERIFIED", "Verify your email before signing in.", { email: user.email });
  }
}

export async function verifyGoogleIdentity(idToken, { clientId, verifyIdToken }) {
  if (!clientId) {
    throw new AuthFlowError(503, "GOOGLE_AUTH_NOT_CONFIGURED", "Google sign-in is not configured on this server.");
  }

  let claims;
  try {
    claims = await verifyIdToken(idToken, clientId);
  } catch {
    throw new AuthFlowError(401, "GOOGLE_TOKEN_INVALID", "Google ID token is invalid, expired, or for a different client.");
  }

  if (!claims?.email || claims.email_verified !== true) {
    throw new AuthFlowError(401, "GOOGLE_EMAIL_UNVERIFIED", "Google sign-in requires a verified email address.");
  }
  return claims;
}

export function signupVerificationResponse(email, emailSent) {
  return {
    email,
    verification_required: true,
    email_sent: emailSent,
    message: emailSent
      ? "Check your email for a verification link before signing in."
      : "Your account was created, but the verification email could not be delivered. Use resend after mail delivery is configured.",
  };
}
