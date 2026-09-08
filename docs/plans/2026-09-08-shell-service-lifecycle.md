# Shell Service Lifecycle Plan

Goal: provide local and ECS start.sh/restart.sh/stop.sh with shell-owned lifecycle operations.

- [x] Test real shell entry points with a fake Docker executable at the external boundary: help/dry-run, config isolation, preservation, exact target, idempotent stop, restart readiness, failures and ECS maintenance/active-run guards.
- [x] Add local shell lifecycle, keep bin/local as compatibility dispatcher, remove Python local startup orchestration. Keep packaging and AgentArts unchanged.
- [x] Add ECS shell lifecycle using existing JSON config (Python 3.6 standard library only for parsing), share deployment lock, and include all shell assets/checksums in the offline package. Start existing containers only; deployments still use deploy.sh.
- [x] Update docs/Makefile and package tests. Verify shell syntax, focused/full offline tests and static checks. The later user request additionally authorizes switching the local backend from Mock to real mode; ECS lifecycle/deployment stays unexecuted.

Commands: `.venv/bin/pytest --no-cov -o addopts='' tests/unit/test_service_shell_scripts.py tests/unit/test_deployment_entrypoints.py tests/unit/test_ecs_deployment.py tests/unit/test_ecs_package.py -q`.

Follow-up delivered: local shell startup defaults to the existing .env and explicit formal/tianyancha overlay, with mandatory credential fields and no Mock fallback. Mock fixtures remain opt-in via --mock. 73 targeted tests passed; full offline suite 913 passed, 1 pre-existing frozen-manifest mismatch, 4 skipped. Actual local start completed after confirming zero active Runs; mode, both health endpoints and unchanged historical file hashes verified. No paid business smoke or ECS lifecycle operation performed.
