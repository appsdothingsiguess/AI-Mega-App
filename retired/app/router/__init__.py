"""app.router — three-layer smart router (PLAN.md §4.3).

Public surface (frozen — settings-api and eval import exactly this):
    async def route(chat, text, attachments, *, llm_client, config, trace_id) -> RouteResult
"""

from app.router.router import route

__all__ = ["route"]
