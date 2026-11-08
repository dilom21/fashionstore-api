"""Global pytest bootstrap: keep every test suite strictly offline."""
import os


# Settings loads repository .env values at import time.  The shared Supabase
# pools must be replaced before any test imports app.core.config.  This is a
# process-local test boundary; launch_guard remains enabled and validates the
# resulting SQLite DSNs in APP_ENV=test.
os.environ["APP_ENV"] = "test"
os.environ["LEGACY_SURFACE_ENABLED"] = "true"
_requested_test_dsn = os.environ.get("FASHIONSTORE_TEST_DATABASE_URL", "")
os.environ["DATABASE_URL"] = (
    _requested_test_dsn
    if _requested_test_dsn.lower().startswith("sqlite")
    else "sqlite+pysqlite:///./fashionstore_pytest.db"
)
for _name in (
    "DATABASE_BOOTSTRAP_URL",
    "DATABASE_PLATFORM_URL",
    "SAAS_ADMIN_DATABASE_URL",
):
    os.environ[_name] = "sqlite+pysqlite:///:memory:"
os.environ.pop("LEGACY_DATABASE_URL", None)
