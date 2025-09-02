import asyncio, logging, httpx
from fastapi import FastAPI
from App import config, state, platform, ws, routes


logger = logging.getLogger("uvicorn.error")
app = FastAPI()
app.include_router(routes.router)

@app.on_event("startup")
async def on_startup():
    async with httpx.AsyncClient(verify=config.VERIFY_SSL, timeout=httpx.Timeout(30.0)) as client:
        await platform.sign_in(client)
        await platform.get_my_tracker(client)
    for ep in state.STATE.transfer_endpoints:
        task = asyncio.create_task(ws.run_ws_endpoint(ep))
        state.STATE.ws_tasks.append(task)

@app.on_event("shutdown")
async def on_shutdown():
    for t in state.STATE.ws_tasks:
        t.cancel()
    state.STATE.ws_tasks.clear()
