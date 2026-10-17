# CivicSocial

Independent Express/PostgreSQL social platform and browser frontend. It has no imports, services, database, or application logic shared with the sibling Mini Proj codebase.

## Start

Copy `.env.example` to `.env`, set secrets/SMTP/Google values as needed, optionally set `SEED_ON_STARTUP=true` for demo accounts in an empty local database, then run `docker compose up --build`.

- API: `http://localhost:8001`
- PostgreSQL: `localhost:5433`
- Frontend: `http://localhost:5180` (the BMC portal retains port 5174)

The frontend is a responsive dark timeline with Home, Explore/search, Notifications, Bookmarks, profiles, post/reply detail, account settings, signup/sign-in, email verification, password reset, and Google sign-in. Feed, profiles, likes, follows, notifications, posting, and replies use the Express API; bookmarks are stored locally in the browser. Photo and video uploads are supported on posts. Direct Messages is currently a browser-only demo and does not persist or send messages.

`SEED_ON_STARTUP=true` explicitly enables demo-account seeding for an empty development database. It defaults off in backend configuration and is rejected in production. Demo accounts share a development-only password defined by the seed script; do not use demo seeding in a deployed environment. Existing database rows are preserved and seeding exits when any user already exists.

To add the curated community timeline to an existing local database, first create the standard demo accounts in an empty development database (`SEED_ON_STARTUP=true` on the first API startup), then run `cd backend && npm run seed:demo-posts`. This separate command is opt-in, refuses production mode, only uses existing non-official demo-seed accounts, and adds missing posts/replies without changing or deleting existing rows. It is idempotent. Media is referenced using the existing post media fields; some demo previews require an internet connection.

## Provider boundary

The backend exposes a `SocialPlatform` contract and selects `civicsocial` as the only implemented provider (`SOCIAL_PLATFORM=civicsocial`). The interface establishes the normalized feed, post, profile, like, and follow operations for a future provider. No X requests or X API credentials are implemented. Keep any future provider credentials in backend environment configuration only; never put them in the SPA, HTML, or manifest.

## Install the PWA

Deploy the frontend behind HTTPS (required for service workers and installation outside localhost), serve the manifest and SVG app icon from the site root, and set the public API origin in the `civicsocial-api-url` meta tag in `frontend/index.html` (or define `window.CIVICSOCIAL_API` before `app.js`). Leave the meta value blank for the local default, which uses the current hostname on port 8001. Open CivicSocial in a supported mobile browser, then choose **Install app** or **Add to Home Screen** from the browser menu. The service worker caches only the app shell; API responses and cross-origin requests are not cached. Updates are checked when the app becomes visible or reconnects, and an activated update reloads a previously controlled page.

For production, set `NODE_ENV=production`, configure a strong `JWT_SECRET`, set `SEED_ON_STARTUP=false`, and keep provider/SMTP credentials in backend environment variables. The backend refuses production startup without a configured JWT secret or when demo seeding is enabled.

## Google Sign-In setup

In Google Cloud Console, create an OAuth 2.0 Client ID of type **Web application**. Add the CivicSocial frontend origins under **Authorized JavaScript origins**: `http://localhost:5180` for local development and the deployed origin such as `https://social.example.com` (origin only; no path). Set the resulting Web Client ID as `GOOGLE_CLIENT_ID` in the backend `.env`; the frontend obtains this same public ID from `GET /auth/config`. Do not configure a client secret in the frontend. Restart the API after changing `.env`. Sign-in uses GIS popup/button mode and does not require an authorized redirect URI.

## BMC civic complaint handoff

Set `BMC_API_BASE_URL` to the BMC API origin/base URL, `BMC_INTEGRATION_SECRET` to the server-to-server bearer credential, and `BMC_CITIZEN_FRONTEND_URL` to the Citizen Portal origin (local default `http://localhost:5174`). All are backend-only settings. An eligible user-authored post must mention the exact `@mybmc` handle and match a conservative English, Hindi, or Marathi civic-issue phrase. Matching is keyword-based and does not provide complete semantic understanding. The integration forwards complete JPEG/PNG/WebP image and MP4 video data URLs only when the incoming MIME type is supported, saves a durable delivery record, and retries transient failures. BMC registrations are acknowledged by the `@mybmc` account only after a complaint reference is returned. This requires an existing `mybmc` user with `isOfficial=true`; provision it through trusted administration, never a public endpoint.

The API adds the `bmc_complaint_deliveries` table through Sequelize's non-destructive `sync()` creation of missing tables; it does not reset or rewrite existing tables. Keep `SEED_ON_STARTUP=false` in production. BMC failures do not undo a CivicSocial post; inspect delivery state in that table and API post response. No BMC calls occur for unrelated posts, and no live credentials are required for automated tests.
