# BMC Civic Complaint Clustering System

TE-B Mini Project · Group 6. This project integrates the supplied models; it does not retrain or replace either model.

## Run locally

1. Ensure Docker Desktop is running and place the supplied CSV, joblib file, and `.pth` file in `./artifacts/`. Compose mounts that folder read-only at `/artifacts`.
2. Run `docker-compose up --build`.
3. Open the dashboard at `http://localhost:5173`; interactive API docs are at `http://localhost:8000/docs`.

On its first startup, the API downloads the required `paraphrase-multilingual-MiniLM-L12-v2` embedding model and encodes the 462 seed records in the same space used by Model 1. That startup can take longer than the live demo path. Seeding happens only if the database is empty.

## API

- `POST /complaints` accepts text, optional coordinates, and optional base64 photo. It detects display language, assigns an incremental centroid cluster at cosine threshold `.75`, runs the existing classifiers, fuses severity at 45% text/55% photo, persists it, and reranks the cluster inline for the live demonstration.
- `GET /clusters` and `GET /clusters/{cluster_id}` provide the map and explainability data.
- `GET /complaints?ward=&category=&status=` filters dashboard records.
- `PATCH /complaints/{id}/status` lets authorized BMC staff move complaints through Open and In Progress and mark them Resolved directly. Each successful transition records status history and notifies the complaint owner.
- `POST /integrations/civicsocial/complaints` accepts a CivicSocial post and optional validated media into Pending Review. Configure the BMC-only `CIVICSOCIAL_INTEGRATION_SECRET` in the root `.env` file and send it as `Authorization: Bearer <secret>` from the future service adapter. Keep the value private; it is read only by the backend. Leave it unset to disable the integration. The endpoint is idempotent by source platform and original post ID.

## BMC staff access

The BMC dashboard at `http://localhost:5173` is restricted to active accounts with the existing `admin` role. Public registration creates citizen accounts only. In the running Docker environment, an authorized operator can provision the five fixed staff identities with cryptographically random one-time passwords using:

```powershell
docker compose exec api python -m app.provision_bmc_staff
```

Save and distribute the printed credentials through a secure channel; rerunning the command does not display or reset existing credentials. If any of the five identities already exists as a partial or conflicting set, provisioning stops without changing accounts. To rotate a lost password, an authorized operator can run `docker compose exec api python -m app.provision_bmc_staff --rotate bmc.staff1@local.bmc`; this sets a new random password without requiring or revealing the previous one. Treat the one-time terminal output as a secret. This rotation does not revoke already-issued JWTs; they remain subject to their configured expiry.

## Important limitations

- Seed rows where `is_real=no` are synthetic augmentation and must remain visibly marked; they are never represented as real public complaints.
- There are 462 total complaints. Five-class severity was unreliable at this scale, so both models intentionally use Low/Medium/High only.
- Photo Model 2 was validated mainly for Roads/SWM imagery (79/124 labelled photos). Encroachment, posters, and other minority categories have insufficient evidence; confidence must be interpreted cautiously.
- Model 1 uses exactly `paraphrase-multilingual-MiniLM-L12-v2`, its trained embedding space. Synthetic text augmentation is deliberately not reintroduced.
- Historical records may retain the old `pending_human_review` status or resolution-evidence rows. The application no longer uses those rows or the former review workflow; status history and original complaint evidence remain intact.
