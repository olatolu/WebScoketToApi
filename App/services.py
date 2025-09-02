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

    alarm_type = str(payload_in.get("AlarmType", "")).strip()
    system_no = payload_in.get("SystemNo")
    lat, lon = payload_in.get("Latitude"), payload_in.get("Longitude")

    # Enriched fields from utility functions
    vehicle_no = await get_vehicle_no(system_no) if system_no else None
    current_location = await get_current_location(lat, lon)
    alarm_name = await get_alarm_name(alarm_type)
    geo_fence_name = await get_geofence_name(payload_in.get("GeoFenceID"))

    payload = {
        "System_No": system_no,
        "Date_x0026_Time": to_xsd_datetime(payload_in.get("DateTime")),
        "Latitude": to_decimal(payload_in.get("Latitude")),
        "Longitude": to_decimal(payload_in.get("Longitude")),
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
        # Enriched from utility function
        "Vehicle_No": vehicle_no,
        "Current_Location": current_location,
        "Geo_fence_Name": geo_fence_name,
        "Alarm_Name": alarm_name,
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

# -------------------------------------------------
# Geofence lookup
# -------------------------------------------------
async def get_geofence_name(zone_id: str) -> Optional[str]:
    """
    Utility: return ZoneName for a given ZoneID.
    Refresh cache if not found.
    """
    for zone in state.STATE.geofences:
        if str(zone.get("ZoneID")) == str(zone_id):
            return zone.get("ZoneName")

    # Not found → refresh SafeZones
    async with httpx.AsyncClient(verify=config.VERIFY_SSL, timeout=httpx.Timeout(30.0)) as client:
        await platform.get_geofences(client)

    for zone in state.STATE.geofences:
        if str(zone.get("ZoneID")) == str(zone_id):
            return zone.get("ZoneName")

    return None

async def get_vehicle_no(system_no: str) -> Optional[str]:
    """
    Utility: return Vehicle_No (the Name attribute) for a given SystemNo.
    Refresh vehicle data if not cached.
    """
    vehicle = await get_vehicle_by_system_no(system_no)
    if vehicle:
        return vehicle.get("Name")
    return None

async def get_current_location(lat: Any, lon: Any) -> Optional[str]:
    """
    Utility: reverse geocode latitude/longitude into a human-readable address.
    Returns None if invalid or reverse geocode fails.
    """
    if not lat or not lon:
        return None
    try:
        lat_f, lon_f = float(lat), float(lon)
    except Exception:
        return None
    return await reverse_geocode(lat_f, lon_f)

async def get_alarm_name(alarm_type_id: str) -> Optional[str]:
    """
    Utility: return Alarm_Name by looking up AlarmTypeID.
    Refresh cache if not found.
    """
    if not alarm_type_id:
        return None
    return await get_alarm_type_by_id(alarm_type_id)
