from typing import Any, Dict, List, Optional
import asyncio

class State:
    token: Optional[str] = None
    user_config: Optional[Dict[str, Any]] = None
    transfer_endpoints: List[Dict[str, Any]] = []
    ws_tasks: List[asyncio.Task] = []
    vehicle_data: List[Dict[str, Any]] = []
    alarm_types: List[Dict[str, Any]] = []

STATE = State()
