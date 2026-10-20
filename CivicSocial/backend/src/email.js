import crypto from "crypto";
import nodemailer from "nodemailer";
import { config } from "./config.js";
import { EmailToken } from "./models.js";

const hasSmtp = () => Boolean(config.smtp.host && config.smtp.user && config.smtp.pass);
const hasBrevoSettings = () => Boolean(config.brevo.apiKey || config.brevo.senderEmail);

const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);

export async function createEmailToken(userId, purpose) {
  const token = crypto.randomBytes(32).toString("hex");
  const expiresAt = new Date(Date.now() + 60 * 60 * 1000);
  await EmailToken.create({ token, userId, purpose, expiresAt });
  return token;
}

export async function sendActionEmail(email, purpose, token) {
  const path = purpose === "verify" ? "verify-email" : "reset-password";
  const frontendUrl = config.frontendUrl.replace(/\/+$/, "");
  const url = `${frontendUrl}/${path}?token=${encodeURIComponent(token)}`;
  const subject = `CivicSocial ${purpose}`;

  if (hasBrevoSettings()) {
    if (!config.brevo.apiKey || !config.brevo.senderEmail) {
      throw new Error("Brevo email configuration is incomplete");
    }

    const action = purpose === "verify" ? "verify your email address" : "reset your password";
    const htmlContent =
      `<html><body><h2>CivicSocial</h2><p>Use the link below to ${action}.</p>` +
      `<p><a href="${escapeHtml(url)}">Continue</a></p>` +
      "<p>This link expires in one hour.</p></body></html>";

    let response;
    try {
      response = await fetch("https://api.brevo.com/v3/smtp/email", {
        method: "POST",
        headers: {
          accept: "application/json",
          "api-key": config.brevo.apiKey,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          sender: {
            name: config.brevo.senderName,
            email: config.brevo.senderEmail,
          },
          to: [{ email }],
          subject,
          htmlContent,
        }),
        signal: AbortSignal.timeout(15000),
      });
    } catch {
      throw new Error("Brevo email delivery request failed");
    }

    if (!response.ok) {
      throw new Error(`Brevo email delivery request failed (HTTP ${response.status})`);
    }
    return;
  }

  if (!hasSmtp()) {
    throw new Error("Email delivery is not configured");
  }

  const transporter = nodemailer.createTransport({
    host: config.smtp.host,
    port: config.smtp.port,
    auth: { user: config.smtp.user, pass: config.smtp.pass },
  });

  await transporter.sendMail({
    from: config.smtp.from,
    to: email,
    subject,
    text: `Open this link: ${url}`,
  });
}
