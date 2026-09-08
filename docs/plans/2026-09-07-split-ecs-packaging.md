# Split ECS Packaging and Server Deployment Implementation Plan

**Goal:** Replace local SSH-driven ECS publishing with an offline local package and a separately invoked server-side deployment script.

**Architecture:** `bin/ecs-package` creates a versioned tar.gz plus checksum containing the source snapshot, standalone controller, Bash entry and non-secret JSON template. Upload/login are manual. The ECS controller uses only Python 3.6 standard-library features and retains existing maintenance, volume verification and rollback guards. No real deployment is authorized by this implementation task.

**Tech Stack:** Bash, Python 3.11 locally / Python 3.6+ on ECS, Docker, pytest.

## Tasks

- [x] Write failing tests in `tests/unit/test_ecs_package.py`; update existing deployment tests for `ecs-package`, server JSON config and Python 3.6 compatibility. Assert package completeness, no SSH/subprocess calls while packaging, no credentials, no overwrite, corruption rejection, server help/dry-run and maintenance gating.
- [x] Replace `scripts/deployment/ecs.py` with offline packaging; replace `bin/ecs` with `bin/ecs-package`; update `scripts/deploy.py` and Makefile.
- [x] Add `deploy/ecs/deploy.sh` and JSON config; adapt `ecs_host.py` to Python 3.6 and introduce standalone CLI/package checks before any host write or Docker command. Keep existing volume migration and rollback tests passing.
- [x] Update README and deployment documentation: package → manual transfer → validate/extract → server preview → explicit deployment. Remove the obsolete SSH orchestration/config documentation; keep local and AgentArts behavior unchanged.
- [x] Run focused tests, real local packaging/unpacking/dry-run, shell syntax, Ruff/mypy and full offline regression. If feasible, only read-only Python 3.6 compatibility checks on the existing host; no upload/deploy/build/restart. Report pre-existing frozen-manifest failure separately.

Verification command: `.venv/bin/pytest --no-cov -o addopts='' tests/unit/test_deployment_entrypoints.py tests/unit/test_ecs_deployment.py tests/unit/test_ecs_package.py -q`.

## Verified 2026-09-08

51 targeted tests passed. Real workspace package creation, checksum validation, extraction and server-entry dry-run passed in a temporary directory. Ruff and mypy passed (10 files), and four Bash entry points passed syntax checks. The unchanged controller was loaded in memory on the existing ECS using Python 3.6.8; help and a read-only Docker version query passed without uploading files. Full offline suite: 891 passed, 1 pre-existing frozen-manifest mismatch, 4 skipped. No real deployment, image build, service restart or push was performed.
