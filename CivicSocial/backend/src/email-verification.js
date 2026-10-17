const RESEND_COOLDOWN_MS = 60_000;
const GENERIC_RESEND_MESSAGE = "If this address belongs to an unverified account, a verification email will be sent shortly.";

export function createEmailVerificationService({ User, EmailToken, createEmailToken, sendActionEmail, greaterThan, now = () => new Date(), cooldownMs = RESEND_COOLDOWN_MS }) {
  const resendTimes = new Map();

  async function deliver(user) {
    await EmailToken.destroy({ where: { userId: user.id, purpose: "verify" } });
    let token;
    try {
      token = await createEmailToken(user.id, "verify");
      await sendActionEmail(user.email, "verify", token);
      return true;
    } catch (error) {
      if (token) await EmailToken.destroy({ where: { token } });
      throw error;
    }
  }

  return {
    async sendSignupVerification(user) {
      try { return await deliver(user); }
      catch {
        console.error("Verification email could not be delivered");
        return false;
      }
    },

    async verify(token) {
      if (typeof token !== "string" || token.length !== 64) return false;
      const record = await EmailToken.findOne({ where: { token, purpose: "verify", expiresAt: { [greaterThan]: now() } } });
      if (!record) return false;
      const user = await User.findByPk(record.userId);
      if (!user) { await record.destroy(); return false; }
      await user.update({ emailVerified: true });
      await record.destroy();
      return true;
    },

    async resend(email) {
      const normalizedEmail = String(email || "").trim().toLowerCase();
      const currentTime = now().getTime();
      for (const [key, at] of resendTimes) if (currentTime - at >= cooldownMs) resendTimes.delete(key);

      const user = normalizedEmail ? await User.findOne({ where: { email: normalizedEmail } }) : null;
      const lastSent = resendTimes.get(normalizedEmail);
      if (!user || user.emailVerified || (lastSent !== undefined && currentTime - lastSent < cooldownMs)) {
        return { message: GENERIC_RESEND_MESSAGE };
      }

      resendTimes.set(normalizedEmail, currentTime);
      try { await deliver(user); }
      catch { console.error("Verification email could not be delivered"); }
      return { message: GENERIC_RESEND_MESSAGE };
    },
  };
}
