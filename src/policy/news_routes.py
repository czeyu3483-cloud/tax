"""FastAPI integration for independent policy-news discovery."""
from __future__ import annotations

import inspect
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from src.policy.news import NewsService

ConversationID = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]


class NewsRefreshRequest(BaseModel):
    question: str = Field(default='', max_length=20_000)
    domain: str = Field(default='all', max_length=40)
    region: str = Field(default='all', max_length=40)
    category: str = Field(default='all', max_length=40)


def build_news_router(service: NewsService, *, conversation_exists: Callable | None = None):
    """Install with the application's conversation-access check; absent check fails closed."""
    router = APIRouter(prefix='/api/conversations', tags=['policy-news'])

    async def check(conversation_id: str):
        if conversation_exists is None:
            raise HTTPException(503, '政策动态尚未接入咨询记录。')
        try:
            allowed = conversation_exists(conversation_id)
            if inspect.isawaitable(allowed):
                allowed = await allowed
        except (FileNotFoundError, ValueError):
            allowed = False
        if not allowed:
            raise HTTPException(404, '咨询记录不存在或不可访问。')

    @router.get('/{conversation_id}/policy-news')
    async def news(conversation_id: ConversationID, date_from: date | None = None,
                   date_to: date | None = None, category: str | None = None,
                   domain: str | None = None, region: str | None = None):
        await check(conversation_id)
        try:
            return service.snapshot(conversation_id,
                                    date_from=date_from.isoformat() if date_from else None,
                                    date_to=date_to.isoformat() if date_to else None,
                                    category=category, domain=domain, region=region)
        except ValueError:
            raise HTTPException(400, '动态筛选条件无效，请检查日期及分类。') from None

    @router.post('/{conversation_id}/policy-news/refresh', status_code=202)
    async def refresh(conversation_id: ConversationID, body: NewsRefreshRequest, request: Request):
        await check(conversation_id)
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, '请求来源校验未通过，请通过本系统页面重新操作。')
        try:
            return await service.trigger(conversation_id, body.question, domain=body.domain,
                                         region=body.region, category=body.category, force=True)
        except ValueError:
            raise HTTPException(400, '动态筛选条件无效，请检查日期及分类。') from None
        except RuntimeError:
            raise HTTPException(503, '政策动态服务暂不可用。') from None

    return router


def news_lifespan(service: NewsService):
    """Use as a lifespan or nest this context inside an existing application lifespan."""
    @asynccontextmanager
    async def lifespan(app):
        service.start()
        try:
            yield
        finally:
            await service.close()
    return lifespan
