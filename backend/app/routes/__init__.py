"""HTTP route modules.

Each submodule exposes a module-level ``router`` (an ``APIRouter``).
``all_routers`` aggregates them for ``app.main`` to mount.
"""

from app.routes import config as config_routes
from app.routes import health, imports, llm_logs, llm_profiles, meta, personas, sims

all_routers = [
    health.router,
    sims.router,
    llm_logs.router,
    personas.router,
    config_routes.router,
    imports.router,
    llm_profiles.router,
    meta.router,
]

__all__ = ["all_routers"]
