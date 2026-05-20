from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class VehicleType(str, Enum):
    TESLA = "tesla"
    RIVIAN = "rivian"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    DRIVING = "driving"
    GEAR_GUARD = "gear_guard"
    MANUALLY_SAVED = "manually_saved"
    UNKNOWN = "unknown"


class CameraAngle(str, Enum):
    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    CABIN = "cabin"
    UNKNOWN = "unknown"


class Clip(BaseModel):
    id: str = Field(description="Unique ID (relative path hash or composite for remote)")
    filename: str
    relativePath: str
    folder: str = Field(description="Subfolder name (e.g. SentryClips, dashcam)")
    vehicle: VehicleType
    eventType: EventType
    cameraAngle: CameraAngle
    timestamp: Optional[datetime] = None
    eventKey: Optional[str] = Field(default=None, description="Groups clips from same moment: {vehicle}:{YYYY-MM-DD_HH-MM-SS}")
    duration: Optional[float] = Field(default=None, description="Duration in seconds")
    size: int = Field(description="File size in bytes")
    sizeString: str
    hasThumbnail: bool = False
    hasVideo: bool = True
    downloadUrl: Optional[str] = None
    thumbnailUrl: Optional[str] = None
    source: Optional[str] = None
    share_id: Optional[int] = None


class ClipListResponse(BaseModel):
    clips: List[Clip]
    total: int
    vehicles: List[str]
    folders: List[str]
    eventTypes: List[str]
    cameras: List[str]
    cachedAt: Optional[datetime] = None
    hasMore: bool = False
    oldestTimestamp: Optional[str] = None


class EventSummary(BaseModel):
    eventKey: str
    timestamp: Optional[datetime] = None
    vehicle: VehicleType
    eventType: EventType
    folder: str
    cameraAngles: List[str]
    cameraCount: int
    totalSize: int
    sizeString: str
    thumbnailUrl: Optional[str] = None
    clips: Optional[List[Clip]] = None


class EventListResponse(BaseModel):
    events: List[EventSummary]
    total: int
    vehicles: List[str]
    folders: List[str]
    eventTypes: List[str]
    cameras: List[str]
    cachedAt: Optional[datetime] = None
    hasMore: bool = False
    oldestTimestamp: Optional[str] = None


class ScanResponse(BaseModel):
    status: str
    clipsFound: int
    cacheUpdated: bool


class HealthResponse(BaseModel):
    status: str
    sharePath: str
    clipsInCache: int
    ffmpegAvailable: bool


class RemoteShareBase(BaseModel):
    name: str
    protocol: str = Field(description="smb, ftp, nfs")
    host: str
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    path: str = Field(description="Path on the share (e.g., /dashcam or /media)")
    enabled: bool = True


class RemoteShareCreate(RemoteShareBase):
    pass


class RemoteShareUpdate(BaseModel):
    name: Optional[str] = None
    protocol: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    path: Optional[str] = None
    enabled: Optional[bool] = None


class RemoteShareResponse(RemoteShareBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RemoteShareTestResponse(BaseModel):
    success: bool
    message: str
    details: Optional[dict] = None