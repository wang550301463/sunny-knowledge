# V2 Docker deployment and regression

Run from the V2 worktree. All deployment files live in this directory. The default isolated project is `sunny-knowledge-v2`; it does not share volumes or networks with other projects.

```sh
python3 knowledge-docker/scripts/init.py
python3 knowledge-docker/scripts/regress.py foundation --build
```

The foundation command builds the current gateway, IAM, auth and knowledge services, starts their dependencies, then runs Go race tests/vet, Python authorization/knowledge/parser tests and actual Keycloak PKCE + knowledge publication HTTP regression **inside Docker**. It retains per-step logs and a JSON report under ignored `knowledge-docker/artifacts/`. Database tests use randomly named temporary schemas and remove only their own schemas. HTTP tests create their own named resources and retain their audit history.

`foundation` is a deliberately bounded test stage. It does not validate the unfinished ingestion, retrieval, graph, Agent, WebUI, MCP or WeCom workflows, model quality, real robot integration, 100k-fragment load, or backup recovery. There is no full-acceptance success shortcut.

Local gateway: <http://localhost:18180>. PostgreSQL for local test tools binds only `127.0.0.1:15439`. Change ports using the generated `.env` before starting; the public URL must match Keycloak issuer and redirect configuration. Generated admin credentials, database passwords, encryption keys, service private keys and test configuration stay under ignored `.env`/`.local`. Initialization does not print them and is idempotent. Do not commit or attach these files to reports.

Each application container mounts its own Ed25519 private key and a shared public-key registry. Separate service roles and PostgreSQL databases enforce ownership. Numeric local UID/GID permits read-only access to owner-only bind-mounted credentials on Docker Desktop. Production TLS, hostnames and credential rotation require deployment-specific configuration; the localhost setup uses Keycloak's development mode.

Useful commands:

```sh
knowledge-docker/scripts/compose.sh ps
knowledge-docker/scripts/compose.sh logs --tail 100 knowledge
knowledge-docker/scripts/compose.sh run --rm regression
knowledge-docker/scripts/compose.sh stop
```

`stop` preserves all persistent volumes. Do not use `down -v` when preserving knowledge or audit history. Future restore/rebuild verification will have its own explicit stage and operations runbook.
