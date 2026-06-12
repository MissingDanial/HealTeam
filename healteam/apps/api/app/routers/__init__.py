"""Routers package — thin HTTP layer.

Each router in this package MUST:
- Validate the request with Pydantic models.
- Call into a service in `app/services/*`.
- Return a Pydantic response model.

NO business logic in routers. The W1 routers are stubs that exist only to
prove the mount points work.
"""
