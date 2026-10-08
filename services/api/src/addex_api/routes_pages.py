"""Landing and configure page. Stremio's "Configure" button opens `{addon base}/configure`,
so an installed configured addon lands on `/{config}/configure` with its settings filled in."""

import json
from functools import cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from addex_api.routes_stremio import Config

router = APIRouter()
PAGE = Path(__file__).parent / "pages" / "configure.html"


@cache
def _template() -> str:
    return PAGE.read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse)
@router.get("/configure", response_class=HTMLResponse)
@router.get("/{config}/configure", response_class=HTMLResponse)
async def configure(cfg: Config):
    initial = {"have": sorted(cfg.have), "hide_p2p": cfg.hide_p2p} if cfg.have or cfg.hide_p2p \
        else None
    # "</" can't appear inside a <script> block.
    data = json.dumps(initial).replace("</", "<\\/")
    return HTMLResponse(_template().replace("__INITIAL_CONFIG__", data))
