from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
import websockets
from fastapi import FastAPI
from zeep import Client, Settings
from zeep.transports import Transport
import requests
from requests.auth import HTTPBasicAuth
from requests_ntlm import HttpNtlmAuth

# -------------------------------------------------
# App & logging
# -------------------------------------------------
app = FastAPI()
logger = logging.getLogger("uvicorn.error")

# -------------------------------------------------
# Env / Config
# -------------------------------------------------
# Platform API (preprocessing like in webSocket.html)
PLATFORM_API_URL = os.getenv("PLATFORM_API_URL", "https://api.overseetracking.com:9090/WebProcessorApi.ashx")
SOAP_ENDPOINT = os.getenv("SOAP_ENDPOINT", "http://aig-navdb18-064.g.group:8447/ANRML-Live/WS/ANRML/Page/WB_Tracking_API?wsdl")
PLATFORM_USERNAME = os.getenv("PLATFORM_USERNAME", 'ANRMLOG1')
PLATFORM_PASSWORD = os.getenv("PLATFORM_PASSWORD", '456789')
LANGUAGE_TYPE = os.getenv("PLATFORM_LANGUAGE_TYPE", "2B72ABC6-19D7-4653-AAEE-0BE542026D46")
USE_HTTP_WS = os.getenv("USE_HTTP_WS", "false").lower() == "true"  # if true -> ws://IP:WsOutputPort, else wss://Domain:WssOutputPort
VERIFY_SSL = os.getenv("VERIFY_SSL", "true").lower() == "true"

# SOAP
# Use absolute path to WSDL file in project root
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SOAP_WSDL = f"file://{os.path.join(PROJECT_ROOT, 'WB_Tracking_API.xml')}"

SOAP_BASIC_USER = os.getenv("SOAP_BASIC_USER", "")
SOAP_BASIC_PASS = os.getenv("SOAP_BASIC_PASS", "")

# Alarm filter
ALLOWED_ALARMS = set(os.getenv("ALLOWED_ALARMS", "17,3,8").split(","))

# Heartbeat
HEARTBEAT_SECONDS = int(os.getenv("WS_HEARTBEAT_SECONDS", "20"))

# -------------------------------------------------
# Global state
# -------------------------------------------------
class State:
    token: Optional[str] = None
    user_config: Optional[Dict[str, Any]] = None  # includes SessionID, UserName, Password
    transfer_endpoints: List[Dict[str, Any]] = []
    ws_tasks: List[asyncio.Task] = []

STATE = State()

# -------------------------------------------------
# SOAP client (zeep)
# -------------------------------------------------
def build_soap_client():
    session = requests.Session()
    if SOAP_BASIC_USER and SOAP_BASIC_PASS:
        # session.auth = HTTPBasicAuth(SOAP_BASIC_USER, SOAP_BASIC_PASS)
        session.auth = HttpNtlmAuth(SOAP_BASIC_USER, SOAP_BASIC_PASS)

    transport = Transport(session=session)
    settings = Settings(strict=False, xml_huge_tree=True)

    base_client = Client(wsdl=SOAP_WSDL, transport=transport, settings=settings)

    # Debug: print available bindings
    logger.info(f"Available bindings: {list(base_client.wsdl.bindings.keys())}")

    return base_client.create_service(
        "{urn:microsoft-dynamics-schemas/page/wb_tracking_api}WB_Tracking_API_Binding",
        SOAP_ENDPOINT,
    )

SOAP_CLIENT = build_soap_client()

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def _to_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in {"1", "true", "on", "yes"}

def _to_decimal(val: Any) -> Optional[Decimal]:
    try:
        if val is None or val == "":
            return None
        return Decimal(str(val))
    except Exception:
        return None

def _to_int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(str(val))
    except Exception:
        return None

def _to_xsd_datetime(val: str) -> Optional[datetime]:
    """
    Incoming example: '2025-08-20 14:58:28'
    WSDL expects xsd:dateTime. We'll pass a Python datetime.
    """
    if not val:
        return None
    try:
        if "T" in val:
            # already ISO-like
            return datetime.fromisoformat(val.replace("Z", ""))
        # convert 'YYYY-MM-DD HH:MM:SS' -> datetime
        return datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def _build_credential(session_id: str, username: str, password: str) -> str:
    """
    As per webSocket.html:
    {
        'ClientID': SessionID,
        'SignalName': '00',
        'LoginType': '0',
        'UserID': UserName,
        'Password': Password,
        'ClientType': '4',
        'DataIP': '',
        'DataTypeReq': []
    }#
    """
    cred = {
        "ClientID": session_id,
        "SignalName": "00",
        "LoginType": "0",
        "UserID": username,
        "Password": password,
        "ClientType": "4",
        "DataIP": "",
        "DataTypeReq": []
    }
    return json.dumps(cred) + "#"

# -------------------------------------------------
# Platform API (mimicking SubmitData in the HTML)
# -------------------------------------------------
async def platform_submit(client: httpx.AsyncClient, information_type: str, operation_type: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    The browser used FormData with fields:
      - Token (can be empty for SignIn)
      - OperationType
      - InformationType
      - LanguageType
      - Arguments (stringified)
    We'll send multipart/form-data too.
    """
    token = STATE.token or ""
    files = {
        "Token": (None, token),
        "OperationType": (None, operation_type),
        "InformationType": (None, information_type),
        "LanguageType": (None, LANGUAGE_TYPE),
        "Arguments": (None, json.dumps(arguments)),  # stringify like in JS
    }
    headers = {
        "Origin": "https://overseetracking.com"
    }
    resp = await client.post(PLATFORM_API_URL, files=files, headers=headers)
    logger.info(
        f"Platform API response ({information_type}/{operation_type}) "
        f"status={resp.status_code}, text='{resp.text[:200]}'"
    )
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Non-JSON response: {resp.text[:500]}")

async def platform_sign_in(client: httpx.AsyncClient) -> None:
    data = await platform_submit(
        client,
        information_type="User",
        operation_type="SignIn",
        arguments={"UserName": PLATFORM_USERNAME, "Password": PLATFORM_PASSWORD},
    )
    if str(data.get("State")) != "0":
        raise RuntimeError(f"SignIn failed: {data}")
    STATE.token = data.get("Token")
    STATE.user_config = data.get("Data")  # contains SessionID, UserName, Password, etc.

async def platform_get_my_tracker(client: httpx.AsyncClient) -> None:
    data = await platform_submit(
        client,
        information_type="Product",
        operation_type="GetMyTracker",
        arguments={"TrackerType": "0"},
    )
    if str(data.get("State")) != "0":
        raise RuntimeError(f"GetMyTracker failed: {data}")

    public_data = data.get("Data") or {}
    # Per the HTML, Transfer contains endpoints (IP/ports or WSS domains/ports)
    STATE.transfer_endpoints = public_data.get("Transfer", []) or []

# -------------------------------------------------
# SOAP push (Create)
# -------------------------------------------------
async def push_to_soap(payload_in: Dict[str, Any]) -> None:
    """
    Only forward AlarmType in ALLOWED_ALARMS.
    Map incoming fields to WB_Tracking_API.Create payload from WSDL.
    """
    alarm_type = str(payload_in.get("AlarmType", "")).strip()
    if alarm_type not in ALLOWED_ALARMS:
        return

    # Build payload matching the WSDL schema.
    # NOTE on "Longitude_Latitude": The WSDL exposes a *single* decimal field.
    # We'll store **Longitude** in it and append Latitude into Arguments for traceability.
    # Adjust here if your target expects a different encoding.
    payload = {
        "System_No": payload_in.get("SystemNo"),
        "Date_x0026_Time": _to_xsd_datetime(payload_in.get("DateTime")),
        "Latitude": _to_decimal(payload_in.get("Longitude")),  # single decimal field in WSDL
        "Longitude": _to_decimal(payload_in.get("Latitude")),  # single decimal field in WSDL
        "Velocity": _to_decimal(payload_in.get("Velocity")),
        "Angle": _to_decimal(payload_in.get("Angle")),
        "Altitude": _to_decimal(payload_in.get("Altitude")),
        "Acc": _to_bool(payload_in.get("Acc")),
        "Digit_Status": payload_in.get("DigitStatus"),
        "Temperature": _to_decimal(payload_in.get("Temperature")),
        "Mileage": _to_decimal(payload_in.get("Mileage")),
        "Alarm_Type": _to_int(alarm_type),
        "Is_Original_Alarm": _to_bool(payload_in.get("IsOriginalAlarm")),
        # Include original Arguments plus Latitude so nothing is lost
        "Arguments": json.dumps({
            "Arguments": payload_in.get("Arguments"),
            "Longitude": payload_in.get("Longitude"),
            "Latitude": payload_in.get("Latitude"),
            "OtherValues": payload_in.get("OtherValues")
        }, ensure_ascii=False),
    }

    # Offload blocking SOAP I/O to a thread so we don't block the event loop.
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: SOAP_CLIENT.Create(WB_Tracking_API=payload))
        logger.info(f"SOAP Create OK: {result}")
        logger.info(f"SOAP Create OK: {payload}")
    except Exception as e:
        logger.error(f"SOAP Create failed: {e}")
        logger.error(f"SOAP Create failed: {payload}")

# -------------------------------------------------
# WebSocket handling (multiple endpoints from Transfer[])
# -------------------------------------------------
async def run_ws_endpoint(endpoint: Dict[str, Any]) -> None:
    """
    Mirrors MyWsClient in the HTML:
      - choose ws://IP:WsOutputPort (if USE_HTTP_WS) OR wss://WssDomainName:WssOutputPort
      - send credential with SessionID, username, password
      - keepalive every 20s with {"SignalName":"99"}#
      - parse '#' delimited messages
    """
    if not STATE.user_config:
        raise RuntimeError("UserConfig missing; login not completed")

    session_id = STATE.user_config.get("SessionID")
    user_name = STATE.user_config.get("UserName") or PLATFORM_USERNAME
    passwd = STATE.user_config.get("Password") or PLATFORM_PASSWORD

    if USE_HTTP_WS:
        host = endpoint.get("ServerIP")
        port = endpoint.get("WsOutputPort")
        scheme = "ws"
    else:
        host = endpoint.get("WssDomainName")
        port = endpoint.get("WssOutputPort")
        scheme = "wss"

    if not host or not port:
        logger.warning(f"Skipping endpoint with missing host/port: {endpoint}")
        return

    url = f"{scheme}://{host}:{port}"
    cred = _build_credential(session_id, user_name, passwd)

    while True:
        try:
            sslopt = None
            if scheme == "wss" and not VERIFY_SSL:
                # websockets.connect uses ssl.SSLContext; passing ssl=False disables verification
                sslopt = False  # disable TLS verify if needed

            async with websockets.connect(url, ssl=sslopt) as ws:
                await ws.send(cred)
                logger.info(f"WS connected: {url}")

                async def heartbeat():
                    while True:
                        await asyncio.sleep(HEARTBEAT_SECONDS)
                        if ws.open:
                            await ws.send('{"SignalName":"99"}#')

                hb_task = asyncio.create_task(heartbeat())

                buffer = ""
                try:
                    async for message in ws:
                        buffer += message
                        # Process complete frames delimited by '#'
                        while "#" in buffer:
                            chunk, buffer = buffer.split("#", 1)
                            if not chunk.strip():
                                continue
                            try:
                                data = json.loads(chunk)
                                alarm_type = str(data.get("AlarmType", "")).strip()

                                if alarm_type in ALLOWED_ALARMS:
                                    # ✅ Only log allowed alarms
                                    logger.info(
                                        f"WS ALARM [{host}:{port}]: {json.dumps(data)[:500]}"
                                    )
                                    # ✅ Only queue allowed alarms
                                    await push_to_soap(data)
                            except Exception as e:
                                logger.error(f"JSON/process error: {e} (chunk={chunk[:200]})")
                finally:
                    hb_task.cancel()

        except Exception as e:
            logger.error(f"WS error ({url}): {e}. Reconnect in 5s.")
            await asyncio.sleep(5)

# -------------------------------------------------
# Bootstrap: SignIn -> GetMyTracker -> spawn WS tasks
# -------------------------------------------------
async def bootstrap():
    async with httpx.AsyncClient(verify=VERIFY_SSL, timeout=httpx.Timeout(30.0)) as client:
        await platform_sign_in(client)
        await platform_get_my_tracker(client)

    # Spawn a WS task per Transfer endpoint
    for ep in STATE.transfer_endpoints:
        task = asyncio.create_task(run_ws_endpoint(ep))
        STATE.ws_tasks.append(task)

# -------------------------------------------------
# FastAPI lifecycle
# -------------------------------------------------
@app.on_event("startup")
async def on_startup():
    # Basic sanity checks
    for var in ("PLATFORM_USERNAME", "PLATFORM_PASSWORD"):
        if not os.getenv(var):
            logger.warning(f"ENV {var} is not set")

    asyncio.create_task(bootstrap())

@app.on_event("shutdown")
async def on_shutdown():
    for t in STATE.ws_tasks:
        t.cancel()
    STATE.ws_tasks.clear()

@app.get("/")
async def health():
    return {
        "status": "running",
        "ws_tasks": len(STATE.ws_tasks),
        "use_http_ws": USE_HTTP_WS,
        "platform_signed_in": bool(STATE.token),
    }
