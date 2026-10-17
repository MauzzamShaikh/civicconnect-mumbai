import crypto from "crypto";
import nodemailer from "nodemailer";
import { config } from "./config.js";
import { EmailToken } from "./models.js";

const hasSmtp = () => Boolean(config.smtp.host && config.smtp.user && config.smtp.pass);

export async function createEmailToken(userId, purpose) {
  const token = crypto.randomBytes(32).toString("hex");
  const expiresAt = new Date(Date.now() + 60 * 60 * 1000);
  await EmailToken.create({ token, userId, purpose, expiresAt });
  return token;
}

export async function sendActionEmail(email, purpose, token) {
  const path = purpose === "verify" ? "verify-email" : "reset-password";
  const url = `${config.frontendUrl}/${path}?token=${token}`;
  if (!hasSmtp()) {
    throw new Error("Email delivery is not configured");
  }
  const transporter = nodemailer.createTransport({ host: config.smtp.host, port: config.smtp.port, auth: { user: config.smtp.user, pass: config.smtp.pass } });
  await transporter.sendMail({ from: config.smtp.from, to: email, subject: `CivicSocial ${purpose}`, text: `Open this link: ${url}` });
}
