from __future__ import annotations

import os
import tempfile

# Drop any inherited proxy env (e.g. a SOCKS proxy from clash) so the app's
# httpx client talks directly to the respx-mocked upstreams during tests.
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
             "https_proxy", "all_proxy"):
    os.environ.pop(_var, None)
os.environ["NO_PROXY"] = "*"

# Configure settings BEFORE importing the app (get_settings is cached).
_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.environ["GW_MASTER_KEY"] = "test-master"
os.environ["GW_DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_path}"
os.environ["GW_REDIS_URL"] = ""
os.environ["GW_MAX_RETRIES"] = "2"
os.environ["GW_RETRY_BACKOFF_BASE"] = "0"  # no sleeping in tests
# Synchronous logging so "request then read /admin/logs" assertions are
# deterministic (SQLite would force inline anyway; be explicit).
os.environ["GW_LOG_ASYNC"] = "false"

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.core.security import (  # noqa: E402
    encrypt_secret,  # noqa: E402
    generate_virtual_key,
    hash_key,
    key_display_prefix,
)
from app.db.models import Alias, Credential, Deployment, Provider, VirtualKey  # noqa: E402

MASTER_HEADERS = {"Authorization": "Bearer test-master"}


@pytest_asyncio.fixture
async def app_client():
    from app.core.config_store import config_store
    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    from app.main import app

    async with app.router.lifespan_context(app):
        # fresh schema each test for isolation
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        # seed config + a virtual key
        plaintext = generate_virtual_key()
        async with SessionLocal() as s:
            prov = Provider(name="mockoai", provider_type="openai_compat",
                            default_base_url="https://up.test/v1", enabled=True)
            s.add(prov)
            await s.flush()
            c1 = Credential(provider_id=prov.id, name="c1", api_key_enc=encrypt_secret("k1"),
                            weight=1, enabled=True)
            c2 = Credential(provider_id=prov.id, name="c2", api_key_enc=encrypt_secret("k2"),
                            weight=1, enabled=True)
            s.add_all([c1, c2])
            await s.flush()
            alias = Alias(name="balanced", lb_strategy="round_robin", fallback_aliases=[], enabled=True)
            s.add(alias)
            await s.flush()
            s.add_all([
                Deployment(alias_id=alias.id, credential_id=c1.id, upstream_model="gpt-x",
                           pinned_params={"temperature": 0.0}, input_price=1.0, output_price=2.0,
                           enabled=True),
                Deployment(alias_id=alias.id, credential_id=c2.id, upstream_model="gpt-x",
                           input_price=1.0, output_price=2.0, enabled=True),
            ])
            vk = VirtualKey(key_hash=hash_key(plaintext), key_prefix=key_display_prefix(plaintext),
                            name="test", allowed_aliases=["*"], enabled=True, budget_period="total")
            s.add(vk)
            await s.commit()
        config_store.invalidate()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.headers.update({"Authorization": f"Bearer {plaintext}"})
            yield client
