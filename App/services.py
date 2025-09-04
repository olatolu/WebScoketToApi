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

    # Enrichments (parallelised for speed)
    system_no = payload_in.get("SystemNo")
    lat = payload_in.get("Latitude")
    lon = payload_in.get("Longitude")

    vehicle_task = get_vehicle_by_system_no(system_no)
    location_task = reverse_geocode(lat, lon) if lat and lon else None
    alarm_name_task = get_alarm_type_by_id(alarm_type)

    # Handle Route/SafeZone specifically
    related_table = str(payload_in.get("RelatedTable") or "").strip()
    related_id = payload_in.get("RelatedID")

    geofence_name_task: Optional[asyncio.Future] = None
    if alarm_type == "17" and related_table == "Route" and related_id:
        geofence_name_task = get_route_name(related_id)
    elif related_table == "SafeZone" and related_id:
        geofence_name_task = get_geofence_name(related_id)

    results = await asyncio.gather(
        vehicle_task,
        location_task if location_task else asyncio.sleep(0, result=None),
        alarm_name_task,
        geofence_name_task if geofence_name_task else asyncio.sleep(0, result=None),
    )

    vehicle = results[0] or {}
    current_location = results[1]
    alarm_name = results[2]
    geofence_name = results[3]

    payload = {
        "System_No": system_no,
        "Date_x0026_Time": payload_in.get("DateTime"),
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
            "Longitude": payload_in.get("Longitude"),
            "Latitude": payload_in.get("Latitude"),
        }, ensure_ascii=False),
        "Vehicle_No": vehicle.get("Name"),
        "Current_Location": current_location,
        "Geo_fence_Name": geofence_name,
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
            if name == "Yaw Alarm":
                return "Deviation Alarm"
            if name == "Engine Start Alarm":
                return "Movement Alarm"
            return name

    return None

# -------------------------------------------------
# Geofence lookup
# -------------------------------------------------
async def get_geofence_name(zone_id: str) -> Optional[str]:
    """
    Utility: return ZoneName for a given ZoneID.
    Refresh cache if not found.
    """
    zone_id = str(zone_id).lower()

    for zone in state.STATE.geofences:
        if str(zone.get("ZoneID")).lower() == zone_id:
            return zone.get("ZoneName")

    # Not found → refresh SafeZones
    async with httpx.AsyncClient(verify=config.VERIFY_SSL, timeout=httpx.Timeout(30.0)) as client:
        await platform.get_geofences(client)

    for zone in state.STATE.geofences:
        if str(zone.get("ZoneID")).lower() == zone_id:
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

async def get_route_name(route_id: str) -> Optional[str]:
    """
    Utility: return RouteName for a given RouteID.
    Refresh cache if not found.
    """
    route_id = str(route_id).lower()

    for route in state.STATE.routes:
        if str(route.get("RouteID")) == str(route_id):
            return route.get("RouteName")

    # Not found → refresh routes
    async with httpx.AsyncClient(verify=config.VERIFY_SSL, timeout=httpx.Timeout(30.0)) as client:
        await platform.get_routes(client)

    for route in state.STATE.routes:
        if str(route.get("RouteID")) == str(route_id):
            return route.get("RouteName")

    return None
