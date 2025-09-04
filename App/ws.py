import asyncio, websockets, json, logging
from App import config, state, services

logger = logging.getLogger("uvicorn.error")

async def run_ws_endpoint(endpoint: dict):
    if not state.STATE.user_config:
        raise RuntimeError("UserConfig missing; login not completed")

    session_id = state.STATE.user_config.get("SessionID")
    user_name = state.STATE.user_config.get("UserName") or config.PLATFORM_USERNAME
    passwd = state.STATE.user_config.get("Password") or config.PLATFORM_PASSWORD

    host = endpoint.get("WssDomainName") if not config.USE_HTTP_WS else endpoint.get("ServerIP")
    port = endpoint.get("WssOutputPort") if not config.USE_HTTP_WS else endpoint.get("WsOutputPort")
    scheme = "ws" if config.USE_HTTP_WS else "wss"

    url = f"{scheme}://{host}:{port}"
    cred = services.build_credential(session_id, user_name, passwd)

    while True:
        try:
            sslopt = False if scheme == "wss" and not config.VERIFY_SSL else None
            async with websockets.connect(
                    url,
                    ssl=sslopt,
                    ping_interval=None,  # disable internal keepalive
                    ping_timeout=None
            ) as ws:
                await ws.send(cred)
                logger.info(f"WS connected: {url}")

                async def heartbeat():
                    while True:
                        await asyncio.sleep(config.HEARTBEAT_SECONDS)
                        if ws.open:
                            await ws.send('{"SignalName":"99"}#')

                hb_task = asyncio.create_task(heartbeat())
                buffer = ""
                try:
                    async for message in ws:
                        buffer += message
                        while "#" in buffer:
                            chunk, buffer = buffer.split("#", 1)
                            if not chunk.strip():
                                continue
                            try:
                                data = json.loads(chunk)
                                alarm_type = str(data.get("AlarmType", "")).strip()
                                if alarm_type in config.ALLOWED_ALARMS:
                                    logger.info(f"WS ALARM [{host}:{port}]: {json.dumps(data)[:500]}")
                                    await services.push_to_soap(data)
                            except Exception as e:
                                logger.error(f"JSON/process error: {e} (chunk={chunk[:200]})")
                finally:
                    hb_task.cancel()
        except Exception as e:
            logger.error(f"WS error ({url}): {e}. Reconnect in 5s.")
            await asyncio.sleep(5)
