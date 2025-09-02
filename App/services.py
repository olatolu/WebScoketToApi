import asyncio, json, logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

import httpx
from App import config, state, soap, platform

logger = logging.getLogger("uvicorn.error")

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def to_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in {"1", "true", "on", "yes"}

def to_decimal(val: Any) -> Optional[Decimal]:
    try:
        if val is None or val == "":
            return None
        return Decimal(str(val))
    except Exception:
        return None

def to_int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(str(val))
    except Exception:
        return None

def to_xsd_datetime(val: str) -> Optional[datetime]:
    if not val:
        return None
    try:
        if "T" in val:
            return datetime.fromisoformat(val.replace("Z", ""))
        return datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def build_credential(session_id: str, username: str, password: str) -> str:
    cred = {
        "ClientID": session_id,
        "SignalName": "00",
        "LoginType": "0",
        "UserID": username,
        "Password": password,
        "ClientType": "4",
        "DataIP": "",
        "DataTypeReq": [],
    }
    return json.dumps(cred) + "#"

# -------------------------------------------------
# SOAP push
# -------------------------------------------------
async def push_to_soap(payload_in: Dict[str, Any]) -> None:
    alarm_type = str(payload_in.get("AlarmType", "")).strip()
    if alarm_type not in config.ALLOWED_ALARMS:
        return

    payload = {
        "System_No": payload_in.get("SystemNo"),
        "Date_x0026_Time": to_xsd_datetime(payload_in.get("DateTime")),
        "Latitude": to_decimal(payload_in.get("Longitude")),
        "Longitude": to_decimal(payload_in.get("Latitude")),
        "Velocity": to_decimal(payload_in.get("Velocity")),
        "Angle": to_decimal(payload_in.get("Angle")),
        "Altitude": to_decimal(payload_in.get("Altitude")),
        "Acc": to_bool(payload_in.get("Acc")),
        "Digit_Status": payload_in.get("DigitStatus"),
        "Temperature": to_decimal(payload_in.get("Temperature")),
        "Mileage": to_decimal(payload_in.get("Mileage")),
        "Alarm_Type": to_int(alarm_type),
        "Is_Original_Alarm": to_bool(payload_in.get("IsOriginalAlarm")),
        "Arguments": json.dumps({
            "Arguments": payload_in.get("Arguments"),
            "Longitude": payload_in.get("Longitude"),
            "Latitude": payload_in.get("Latitude"),
            "OtherValues": payload_in.get("OtherValues"),
        }, ensure_ascii=False),
    }

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: soap.SOAP_CLIENT.Create(WB_Tracking_API=payload))
        logger.info(f"SOAP Create OK: {result}")
    except Exception as e:
        logger.error(f"SOAP Create failed: {e}")
        logger.error(f"Payload: {payload}")

# -------------------------------------------------
# Reverse geocoding
# -------------------------------------------------
async def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    params = {"lat": lat, "lon": lon, "format": "json"}
    headers = {"User-Agent": "MyApp/1.0 (your-email@example.com)"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(config.NOMINATIM_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("display_name")
    except Exception as e:
        logger.error(f"Reverse geocoding failed: {e}")
        return None

# -------------------------------------------------
# Vehicle lookup
# -------------------------------------------------
async def get_vehicle_by_system_no(system_no: str) -> Optional[Dict[str, Any]]:
    for vehicle in state.STATE.vehicle_data:
        if str(vehicle.get("SystemNo")) == str(system_no):
            return vehicle

    async with httpx.AsyncClient(verify=config.VERIFY_SSL, timeout=httpx.Timeout(30.0)) as client:
        await platform.get_my_tracker(client)

    for vehicle in state.STATE.vehicle_data:
        if str(vehicle.get("SystemNo")) == str(system_no):
            return vehicle
    return None

# -------------------------------------------------
# Alarm type lookup
# -------------------------------------------------
async def get_alarm_type_by_id(alarm_type_id: str) -> Optional[str]:
    for alarm in state.STATE.alarm_types:
        if str(alarm.get("AlarmTypeID")) == str(alarm_type_id):
            name = alarm.get("Content", "")
            return "Deviation Alarm" if name == "Yaw Alarm" else name

    async with httpx.AsyncClient(verify=config.VERIFY_SSL, timeout=httpx.Timeout(30.0)) as client:
        await platform.get_alarm_types(client)

    for alarm in state.STATE.alarm_types:
        if str(alarm.get("AlarmTypeID")) == str(alarm_type_id):
            name = alarm.get("Content", "")
            return "Deviation Alarm" if name == "Yaw Alarm" else name

    return None
