import "dotenv/config";

export const config = {
  databaseUrl: process.env.DATABASE_URL,
  jwtSecret: process.env.JWT_SECRET || "development-only-secret",
  jwtExpiresIn: process.env.JWT_EXPIRES_IN || "7d",
  frontendUrl: process.env.FRONTEND_URL || "http://localhost:5180",
  socialPlatform: process.env.SOCIAL_PLATFORM || "civicsocial",
  production: process.env.NODE_ENV === "production",
  apiPort: Number(process.env.API_PORT || 8001),
  smtp: {
    host: process.env.SMTP_HOST,
    port: Number(process.env.SMTP_PORT || 587),
    user: process.env.SMTP_USER,
    pass: process.env.SMTP_PASS,
    from: process.env.SMTP_FROM || "CivicSocial <no-reply@civicsocial.local>",
  },
  brevo: {
    apiKey: process.env.BREVO_API_KEY,
    senderEmail: process.env.BREVO_SENDER_EMAIL,
    senderName: process.env.BREVO_SENDER_NAME || "CivicSocial",
  },
  googleClientId: process.env.GOOGLE_CLIENT_ID,
  bmcApiBaseUrl: process.env.BMC_API_BASE_URL,
  bmcIntegrationSecret: process.env.BMC_INTEGRATION_SECRET,
  bmcCitizenFrontendUrl: process.env.BMC_CITIZEN_FRONTEND_URL || "http://localhost:5174",
  groqApiKey: process.env.GROQ_API_KEY,
  groqModel: process.env.GROQ_MODEL || "openai/gpt-oss-20b",
  seedOnStartup: process.env.SEED_ON_STARTUP === "true",
};

if (config.production && (!process.env.JWT_SECRET || process.env.JWT_SECRET === "development-only-secret")) {
  throw new Error("JWT_SECRET must be configured with a production secret");
}
if (config.production && config.seedOnStartup) {
  throw new Error("SEED_ON_STARTUP must be false in production; demo accounts are not production credentials");
}
if (config.socialPlatform !== "civicsocial") {
  throw new Error(`Unsupported SOCIAL_PLATFORM: ${config.socialPlatform}. Only civicsocial is implemented.`);
}