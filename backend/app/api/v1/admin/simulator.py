"""
Admin API: OCPP Station Simulator — interactive virtual station control.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
import logging

from app.db.session import get_db
from .operators import get_current_admin
from app.api.v1.schemas.admin.simulator import SimulatorStatusResponse, SimulatorLogResponse
from app.api.v1.schemas.common import ADMIN_RESPONSES

router = APIRouter(prefix="/simulator")
logger = logging.getLogger(__name__)


class SimulatorStartRequest(BaseModel):
    station_id: str = Field(..., min_length=1)
    connectors: int = Field(2, ge=1, le=4)


class ConnectorActionRequest(BaseModel):
    connector_id: int = Field(..., ge=1)


class StartChargingRequest(BaseModel):
    connector_id: int = Field(..., ge=1)
    id_tag: str = Field("SIM-USER-001")


@router.post("/start", summary="Start OCPP simulator", description="Starts an interactive OCPP station simulator for testing.", responses=ADMIN_RESPONSES)
async def start_simulator(
    request: Request,
    body: SimulatorStartRequest,
    db: Session = Depends(get_db),
):
    """Boot a virtual station and connect to the OCPP server."""
    admin = get_current_admin(request, db)

    from app.services.station_simulator import get_simulator, StationSimulator

    existing = get_simulator()
    if existing and existing._connected:
        raise HTTPException(status_code=409, detail="Simulator already running. Stop it first.")

    sim = StationSimulator(station_id=body.station_id, num_connectors=body.connectors)
    try:
        result = await sim.connect()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "success": True,
        "station_id": body.station_id,
        "connectors": body.connectors,
    }


@router.get("/status", summary="Get simulator status", description="Returns current simulator state.", response_model=SimulatorStatusResponse, responses=ADMIN_RESPONSES)
async def get_simulator_status(
    request: Request,
    db: Session = Depends(get_db),
):
    """Current state of the simulator (connectors, energy, etc.)."""
    admin = get_current_admin(request, db)

    from app.services.station_simulator import get_simulator

    sim = get_simulator()
    if not sim or not sim._connected:
        return {"success": True, "active": False}

    status = sim.get_status()
    return {"success": True, **status}


@router.post("/plug-in", summary="Simulate plug-in", description="Simulates plugging in a cable to a connector.", responses=ADMIN_RESPONSES)
async def plug_in(
    request: Request,
    body: ConnectorActionRequest,
    db: Session = Depends(get_db),
):
    """Send StatusNotification(Preparing) for a connector."""
    admin = get_current_admin(request, db)

    from app.services.station_simulator import get_simulator

    sim = get_simulator()
    if not sim or not sim._connected:
        raise HTTPException(status_code=400, detail="Simulator not running")

    try:
        result = await sim.plug_in(body.connector_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, **result}


@router.post("/start-charging", summary="Simulate start charging", description="Simulates starting a charging transaction.", responses=ADMIN_RESPONSES)
async def start_charging(
    request: Request,
    body: StartChargingRequest,
    db: Session = Depends(get_db),
):
    """Authorize + StartTransaction + begin meter values loop."""
    admin = get_current_admin(request, db)

    from app.services.station_simulator import get_simulator

    sim = get_simulator()
    if not sim or not sim._connected:
        raise HTTPException(status_code=400, detail="Simulator not running")

    try:
        result = await sim.start_charging(body.connector_id, body.id_tag)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, **result}


@router.post("/stop-charging", summary="Simulate stop charging", description="Simulates stopping a charging transaction.", responses=ADMIN_RESPONSES)
async def stop_charging(
    request: Request,
    body: ConnectorActionRequest,
    db: Session = Depends(get_db),
):
    """StopTransaction + stop meter values loop."""
    admin = get_current_admin(request, db)

    from app.services.station_simulator import get_simulator

    sim = get_simulator()
    if not sim or not sim._connected:
        raise HTTPException(status_code=400, detail="Simulator not running")

    try:
        result = await sim.stop_charging(body.connector_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, **result}


@router.post("/stop", summary="Stop simulator", description="Stops the OCPP simulator and disconnects.", responses=ADMIN_RESPONSES)
async def stop_simulator(
    request: Request,
    db: Session = Depends(get_db),
):
    """Disconnect the simulator and clean up."""
    admin = get_current_admin(request, db)

    from app.services.station_simulator import get_simulator

    sim = get_simulator()
    if not sim:
        return {"success": True, "message": "No simulator running"}

    await sim.disconnect()
    return {"success": True, "message": "Simulator stopped"}


@router.get("/log", summary="Get simulator log", description="Returns last 200 OCPP messages from the simulator.", response_model=SimulatorLogResponse, responses=ADMIN_RESPONSES)
async def get_simulator_log(
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the last 200 OCPP messages."""
    admin = get_current_admin(request, db)

    from app.services.station_simulator import get_simulator

    sim = get_simulator()
    if not sim:
        return {"success": True, "events": []}

    return {"success": True, "events": sim.get_log()}
