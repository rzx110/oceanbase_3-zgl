"""Loopback-only HTTP adapter for the Dify HTTP Request nodes."""
from contextlib import asynccontextmanager
import argparse
import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from powercontext.builtin.records import BaseValueNotFoundError
import uvicorn

from .memory import GameMemory, TurnConflict

ROOT = Path(__file__).resolve().parents[2]
Player = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,40}$")]
Item = Literal["umbrella", "clock", "key"]


class CommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    player_code: Player
    item: Item
    action: Literal["greet", "prank", "apologize", "gift", "explore"]
    utterance: str = Field(max_length=140)
    expected_turn: int = Field(ge=0, le=10000)
    operation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: dict
    narrative: str = Field(min_length=1, max_length=6000)


def create_app(database: Path, token: str, audit_path: Path | None = None):
    if len(token) < 24:
        raise ValueError("backend token must have at least 24 characters")
    memory = GameMemory(database, audit_path)

    @asynccontextmanager
    async def lifespan(app):
        await memory.start()
        try:
            yield
        finally:
            await memory.close()

    app = FastAPI(title="会记仇的奇物当铺 / PowerContext bridge", lifespan=lifespan)
    app.state.memory = memory

    def authorize(authorization: str | None = Header(default=None)):
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Invalid backend credential")

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "game": "oddity-pawnshop", "memory": "PowerContext Runtime"}

    @app.get("/v1/state", dependencies=[Depends(authorize)])
    async def state(player_code: Player, item: Item):
        return await memory.read(player_code, item)

    @app.post("/v1/interactions", dependencies=[Depends(authorize)])
    async def commit(value: CommitInput):
        try:
            return await memory.commit(value.model_dump())
        except (TurnConflict, ValueError) as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/v1/source", dependencies=[Depends(authorize)])
    async def source(player_code: Player, item: Item, source_id: Annotated[str, Field(pattern=r"^oddity:[0-9a-f]{64}$")]):
        try:
            return await memory.exact(player_code, item, source_id)
        except BaseValueNotFoundError:
            raise HTTPException(404, "Memory source not found in this player's scope") from None

    return app


def local_config():
    path = ROOT / ".local/backend.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {"token": secrets.token_urlsafe(32)}
    path.write_text(json.dumps(config), encoding="utf-8")
    os.chmod(path, 0o600)
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--database", type=Path, default=ROOT / ".local/pawnshop.db")
    parser.add_argument("--stop-file", type=Path, help="Optional local-only graceful shutdown marker")
    args = parser.parse_args()
    config = local_config()
    token = os.environ.get("ODDITY_BACKEND_TOKEN") or config["token"]
    print("Backend credentials are in .local/backend.json and are not printed.", flush=True)
    app = create_app(args.database, token, ROOT / ".local/memory-audit.jsonl")
    if args.stop_file is None:
        uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, timeout_graceful_shutdown=5)
    else:
        async def serve():
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1, timeout_graceful_shutdown=5))
            async def watch():
                while not args.stop_file.exists():
                    await asyncio.sleep(0.25)
                server.should_exit = True
            watcher = asyncio.create_task(watch())
            try:
                await server.serve()
            finally:
                watcher.cancel()
        asyncio.run(serve())


if __name__ == "__main__":
    main()
