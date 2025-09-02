import httpx, json, logging
from App import config, state

logger = logging.getLogger("uvicorn.error")

async def platform_submit(client: httpx.AsyncClient, information_type: str, operation_type: str, arguments: dict):
    files = {
        "Token": (None, state.STATE.token or ""),
        "OperationType": (None, operation_type),
        "InformationType": (None, information_type),
        "LanguageType": (None, config.LANGUAGE_TYPE),
        "Arguments": (None, json.dumps(arguments)),
    }
    headers = {"Origin": "https://overseetracking.com"}
    resp = await client.post(config.PLATFORM_API_URL, files=files, headers=headers)
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Non-JSON response: {resp.text[:500]}")

async def sign_in(client: httpx.AsyncClient):
    data = await platform_submit(client, "User", "SignIn", {"UserName": config.PLATFORM_USERNAME, "Password": config.PLATFORM_PASSWORD})
    if str(data.get("State")) != "0":
        raise RuntimeError(f"SignIn failed: {data}")
    state.STATE.token = data.get("Token")
    state.STATE.user_config = data.get("Data")

async def get_my_tracker(client: httpx.AsyncClient):
    data = await platform_submit(client, "Product", "GetMyTracker", {"TrackerType": "0"})
    if str(data.get("State")) != "0":
        raise RuntimeError(f"GetMyTracker failed: {data}")
    public_data = data.get("Data") or {}
    state.STATE.transfer_endpoints = public_data.get("Transfer", []) or []
    state.STATE.vehicle_data = public_data.get("Tracker", []) or []

async def get_alarm_types(client: httpx.AsyncClient):
    data = await platform_submit(client, "AlarmType", "Query", {})
    if str(data.get("State")) != "0":
        raise RuntimeError(f"Get AlarmType failed: {data}")
    state.STATE.alarm_types = data.get("Data") or []
