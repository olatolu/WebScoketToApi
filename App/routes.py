from fastapi import APIRouter, Query
from App import state, services, config

router = APIRouter()

@router.get("/")
async def health():
    return {
        "status": "running",
        "ws_tasks": len(state.STATE.ws_tasks),
        "use_http_ws": config.USE_HTTP_WS,
        "platform_signed_in": bool(state.STATE.token),
    }

@router.get("/vehicle/{system_no}")
async def vehicle_lookup(system_no: str):
    vehicle = await services.get_vehicle_by_system_no(system_no)
    if not vehicle:
        return {"error": "SystemNo not found"}
    return vehicle

@router.get("/alarm/{alarm_type_id}")
async def alarm_lookup(alarm_type_id: str):
    name = await services.get_alarm_type_by_id(alarm_type_id)
    if not name:
        return {"error": "AlarmTypeID not found"}
    return {"id": alarm_type_id, "name": name}

@router.get("/geocode/{lat}/{lon}")
async def geocode(lat: float, lon: float):
    address = await services.reverse_geocode(lat, lon)
    if not address:
        return {"error": "Address not found"}
    return {"lat": lat, "lon": lon, "address": address}

@router.get("/geofence-name")
async def geofence_name(zone_id: str = Query(..., description="Geofence ZoneID")):
    """
    Get the geofence (SafeZone) name by ZoneID.
    """
    name = await services.get_geofence_name(zone_id)
    return {"ZoneID": zone_id, "ZoneName": name}

@router.get("/route-name")
async def route_name(route_id: str = Query(..., description="RouteID")):
    """
    Get the route name by RouteID.
    """
    name = await services.get_route_name(route_id)
    return {"RouteID": route_id, "RouteName": name}
