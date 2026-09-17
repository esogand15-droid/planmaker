worker: ./docker-entrypoint.sh bot
# `release` runs once per deploy, before the new worker takes over. The worker
# also migrates at boot (RUN_MIGRATIONS_ON_START), so a missing release phase can
# never leave the bot pointing at an empty database.
release: alembic upgrade head
