"""Assets CRUD router."""
from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import AssetCreate, AssetOut
from storage.database import get_db
from storage.models import Asset

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=List[AssetOut])
async def list_assets(session: AsyncSession = Depends(get_db)):
    stmt = select(Asset).where(Asset.active == True).order_by(Asset.name)  # noqa: E712
    result = await session.execute(stmt)
    assets = result.scalars().all()
    return [_to_out(a) for a in assets]


@router.post("", response_model=AssetOut, status_code=201)
async def create_asset(body: AssetCreate, session: AsyncSession = Depends(get_db)):
    asset = Asset(
        name=body.name,
        asset_type=body.asset_type,
        description=body.description,
        location_name=body.location_name,
        coordinates=WKTElement(f"POINT({body.longitude} {body.latitude})", srid=4326),
        alert_radius_km=body.alert_radius_km,
        extra_meta=body.extra_meta or {},
        active=True,
    )
    session.add(asset)
    await session.flush()
    return _to_out(asset)


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: UUID, session: AsyncSession = Depends(get_db)):
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return _to_out(asset)


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: UUID,
    body: AssetCreate,
    session: AsyncSession = Depends(get_db),
):
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset.name = body.name
    asset.asset_type = body.asset_type
    asset.description = body.description
    asset.location_name = body.location_name
    asset.coordinates = WKTElement(f"POINT({body.longitude} {body.latitude})", srid=4326)
    asset.alert_radius_km = body.alert_radius_km
    if body.extra_meta:
        asset.extra_meta = body.extra_meta
    await session.flush()
    return _to_out(asset)


@router.delete("/{asset_id}", status_code=204)
async def deactivate_asset(asset_id: UUID, session: AsyncSession = Depends(get_db)):
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset.active = False


def _to_out(asset: Asset) -> AssetOut:
    lat, lon = None, None
    try:
        if asset.coordinates:
            from geoalchemy2.shape import to_shape
            pt = to_shape(asset.coordinates)
            lat, lon = pt.y, pt.x
    except Exception:
        pass
    return AssetOut(
        id=asset.id,
        name=asset.name,
        asset_type=asset.asset_type,
        description=asset.description,
        location_name=asset.location_name,
        latitude=lat,
        longitude=lon,
        alert_radius_km=asset.alert_radius_km,
        active=asset.active,
        created_at=asset.created_at,
    )
