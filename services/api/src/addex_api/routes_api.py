"""JSON API for the web front-end."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from addex_api.deps import get_session
from addex_api.queries import search_titles, title_group
from addex_api.schemas import SearchHitOut, TitleDetailOut

router = APIRouter(prefix="/api")
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/search", response_model=list[SearchHitOut])
async def search(
    session: Session,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return [SearchHitOut.of(h) for h in await search_titles(session, q, limit)]


@router.get("/titles/{title_id}", response_model=TitleDetailOut)
async def title(session: Session, title_id: int):
    """A title with its grouped entries and, per active addon, the latest results.
    For a child entry this returns its parent's group."""
    group = await title_group(session, title_id)
    if group is None:
        raise HTTPException(404, "title not found")
    return TitleDetailOut.of(group)
