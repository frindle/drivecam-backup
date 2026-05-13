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
    id: str = Field(description="Unique ID (relative path hash)")
    filename: str
    relativePath: str
    folder: str = Field(description="Subfolder name (e.g. SentryClips, dashcam)")
    vehicle: VehicleType
    eventType: EventType
    cameraAngle: CameraAngle
    timestamp: Optional[datetime] = None
    duration: Optional[float] = Field(default=None, description="Duration in seconds")
    size: int = Field(description="File size in bytes")
    sizeString: str
    hasThumbnail: bool = False
    hasVideo: bool = True
    downloadUrl: Optional[str] = None
    thumbnailUrl: Optional[str] = None


class ClipListResponse(BaseModel):
    clips: List[Clip]
    total: int
    vehicles: List[str]
    folders: List[str]
    eventTypes: List[str]
    cachedAt: Optional[datetime] = None


class ScanResponse(BaseModel):
    status: str
    clipsFound: int
    cacheUpdated: bool


class HealthResponse(BaseModel):
    status: str
    sharePath: str
    clipsInCache: int
    ffmpegAvailable: bool