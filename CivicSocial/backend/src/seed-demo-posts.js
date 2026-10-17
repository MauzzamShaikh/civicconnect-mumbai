import "dotenv/config";
import { pathToFileURL } from "node:url";
import { Op } from "sequelize";
import { config } from "./config.js";
import { sequelize } from "./db.js";
import { Post, User } from "./models.js";
import { seedDemoPosts } from "./demo-seeder.js";

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (config.production) throw new Error("Demo post seeding is disabled in production");
  await sequelize.authenticate();
  try { await seedDemoPosts({ UserModel: User, PostModel: Post, operators: Op, production: config.production }); }
  finally { await sequelize.close(); }
}
