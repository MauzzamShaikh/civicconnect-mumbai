# CivicConnect Mumbai

**An integrated civic intelligence and complaint-management platform** connecting citizen reporting, BMC staff operations, geospatial complaint clustering, and a CivicSocial community-reporting prototype.

> **Project status:** Academic project in active development. Features, ports, and setup steps below describe the current intended local Docker setup. Check each application's README and `.env.example` files for the latest requirements.

## Overview

CivicConnect Mumbai contains two independently run applications in one repository:

- **BMC Civic Complaint Clustering System** (`Mini Proj`) — citizen and staff portals backed by a FastAPI API, PostgreSQL/PostGIS, Redis, and a background worker.
- **CivicSocial** (`CivicSocial`) — a local social-feed prototype with its own API, frontend, and PostgreSQL database. It is designed to support relevant civic posts being submitted to BMC for staff review.

The applications live in separate folders and have separate Compose configurations. Their databases and runtime configuration must remain separate; the integration between them is configured explicitly through environment variables and an integration secret.

## Key capabilities

### BMC Civic Complaint Clustering System

- Citizen complaint submission and complaint tracking.
- Staff portal for reviewing and managing complaints.
- Text-based location extraction and geocoding, with bounded geographic checks and fallback handling.
- Complaint severity inference using the project's model artifacts.
- Geographic and semantic clustering, plus a map view of active complaints.
- Review workflow for citizen-submitted complaints before they enter the active operations workflow.

**Important workflow note:** Citizen complaints can remain pending review and have `cluster_id = -1` until staff accept them. Complaints without reliable coordinates may also remain unclustered; the system should not invent precise map coordinates when location resolution fails.

### CivicSocial

- Local social-feed experience with a post composer.
- Backend and frontend deployed as separate Docker services.
- Automated tests for post submission, notifications, user search, and BMC integration.
- Integration path for relevant civic reports to enter BMC's review workflow.

CivicSocial is a project prototype; do not assume that it is connected to or publishing through the official X/Twitter API.

## Repository layout

```text
CivicConnect-Mumbai/
├── README.md
├── .gitignore
├── Mini Proj/
│   ├── backend/              # FastAPI application and tests
│   ├── frontend/              # BMC staff portal
│   ├── citizen-frontend/      # Citizen portal
│   ├── artifacts/             # Required model files and local datasets
│   └── docker-compose.yml
└── CivicSocial/
    ├── backend/               # Node.js/Express application and tests
    ├── frontend/              # Social-feed frontend and Nginx config
    └── docker-compose.yml
```

## Technology stack

| Component | Main technologies |
|---|---|
| BMC API | Python, FastAPI, SQLAlchemy |
| BMC persistence | PostgreSQL with PostGIS |
| BMC portals | React, Vite, Leaflet (where configured) |
| BMC background services | Redis and Celery |
| BMC inference | PyTorch, sentence-transformers, joblib and project model artifacts |
| CivicSocial API | Node.js, Express, Sequelize, PostgreSQL |
| CivicSocial frontend | JavaScript modules, HTML/CSS, Nginx |
| Local orchestration | Docker Compose |

## Prerequisites

Install and start:

- Docker Desktop with its Docker engine running.
- Git.
- A modern web browser.

Some location, email, or inference features may depend on configured provider credentials or model files. Review the application-specific documentation before running the services.

## Configuration and secrets

1. Open each application's folder and read its `.env.example` and README.
2. Create a local `.env` from the relevant example file where required, then fill in valid local configuration values.
3. Configure the BMC and CivicSocial integration settings consistently, including the shared integration secret expected by the integration code.
4. Keep real `.env` files, passwords, API keys, tokens, private keys, and database credentials out of Git. Never paste them into issue reports or commit them to this repository.
5. Verify that `.env.example` files contain placeholders only.

Example PowerShell commands (run only where the example file exists and the `.env` file does not already exist):

```powershell
Copy-Item .env.example .env
```

Do not overwrite an existing `.env`; it may contain working local configuration.

## Run the BMC application

From PowerShell, at the repository root:

```powershell
cd "Mini Proj"
docker compose up -d --build
```

Intended local endpoints:

| Service | URL / port |
|---|---|
| BMC staff portal | http://localhost:5173 |
| BMC citizen portal | http://localhost:5174 |
| BMC API | http://localhost:8000 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

Useful commands, run from `Mini Proj`:

```powershell
docker compose ps
docker compose logs --tail=150 api
docker compose logs --tail=100 frontend
docker compose logs --tail=100 citizen-frontend
```

Run backend tests from the `Mini Proj` folder using the test instructions in its README. A common command in the API container is:

```powershell
docker compose exec api python -m unittest discover -s tests -p "test_*.py"
```

If the API is still starting, inspect its logs before repeatedly restarting it. Model loading can take time, and a `Waiting for application startup` message by itself does not establish the cause of a delay.

## Run CivicSocial

In a separate PowerShell window, from the repository root:

```powershell
cd "CivicSocial"
docker compose up -d --build
```

Intended local endpoints:

| Service | URL / port |
|---|---|
| CivicSocial frontend | http://localhost:5180 |
| CivicSocial API | http://localhost:8001 |
| PostgreSQL | localhost:5433 |

Useful commands, run from `CivicSocial`:

```powershell
docker compose ps
docker compose logs --tail=150 api
docker compose logs --tail=100 frontend
npm test
```

Use the exact test command documented in `CivicSocial/README.md` if the root-level `npm test` command is not defined. Run tests from the directory whose `package.json` contains the relevant test script.

## BMC–CivicSocial integration smoke test

1. Start both applications and confirm all intended containers are healthy/running.
2. Sign in to CivicSocial and publish a normal post. Confirm the post appears in the CivicSocial feed.
3. Submit a clearly relevant civic report through the intended `@mybmc` flow.
4. Check the CivicSocial backend logs for the integration request without exposing secrets.
5. Sign in to the BMC staff portal and verify that the civic report enters the expected review queue.
6. Accept the report through the normal staff workflow, then verify its location, category, cluster assignment, and map representation.
7. Test an unrelated social post to confirm that it is not incorrectly submitted to BMC.

Do not bypass the review workflow to make reports appear on the active operations map.

## Location and clustering expectations

- Location extraction should support the languages and spelling variations implemented by the project.
- Geocoding results should be checked against the requested locality and context; a matching landmark name alone may not be enough.
- If an exact location cannot be resolved, the application should preserve uncertainty rather than fabricate precise coordinates.
- Clustering should consider location compatibility and semantic similarity. Nearby but unrelated reports should not be merged solely because their text is similar.
- Reports awaiting staff acceptance may remain pending and unclustered until accepted.

## Data and backup safety

- Do **not** run `docker compose down -v` unless you intentionally want to remove volumes and understand the data-loss implications.
- Do not truncate, reset, or reseed either database as a general troubleshooting step.
- Before migrations, bulk edits, or data repairs, create and verify an appropriate database backup.
- Keep private complaint records, user uploads, database dumps, and real user data out of public repositories.
- The BMC model files are included because inference may require them. Verify their provenance and redistribution permissions before publishing the repository publicly.
- The complaint CSV dataset is intentionally excluded from the root Git ignore configuration pending a review of its contents and sharing permissions.

## Git workflow

Save a checkpoint before making significant changes:

```powershell
git status --short
git add -A
git diff --cached --check
git commit -m "Describe the change"
git push
```

Before committing, inspect the staged file list and verify that no secrets, local environment files, private data, or unintended generated files are included. If a credential is ever committed, removing it in a later commit is not sufficient; revoke/rotate it and follow the repository's incident-recovery process.

## Troubleshooting

- **BMC staff portal returns 502:** check `docker compose ps`, then the BMC API logs. A frontend proxy `Connection refused` typically means the API is not yet accepting connections.
- **API appears stuck during startup:** inspect later API logs and container resource usage before editing code. Model initialization can take time.
- **Complaint receives `cluster_id = -1`:** first check its review state and whether it has reliable coordinates. Pending review or unresolved location can legitimately leave a complaint unclustered.
- **CivicSocial post composer fails to load:** inspect the browser console and Network tab for the failing module URL; verify the frontend serves it with a JavaScript MIME type and that the file exists in the built image.
- **An integration request fails:** check both applications' logs and configuration names, but do not print or share integration secrets.

## Development principles

- Prefer small, reviewable changes and regression tests over broad rewrites.
- Preserve existing user data and unrelated manual changes.
- Do not hardcode individual landmark names to solve general geocoding problems.
- Do not claim a fix is complete until the relevant tests and live smoke checks pass.

## License

No license has been selected yet. Until a license is added, do not assume that others have permission to reuse, redistribute, or publish this project's code or model artifacts.
