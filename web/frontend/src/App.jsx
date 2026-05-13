import { useState, useEffect, useCallback, useRef } from 'react'
import { getClips, getHealth, triggerScan } from './api'

const EVENT_COLORS = {
  driving: '#3b82f6',
  gear_guard: '#f59e0b',
  manually_saved: '#22c55e',
  unknown: '#6b7280',
}

const EVENT_LABELS = {
  driving: 'Driving',
  gear_guard: 'Gear Guard',
  manually_saved: 'Saved',
  unknown: 'Unknown',
}

const CAMERA_LABELS = {
  front: 'Front',
  back: 'Rear',
  left: 'Left',
  right: 'Right',
  cabin: 'Cabin',
  unknown: 'Unknown',
}

function formatDate(d) {
  if (!d) return ''
  return new Date(d).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
}

function formatTime(d) {
  if (!d) return ''
  return new Date(d).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDuration(seconds) {
  if (!seconds) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function groupByDate(clips) {
  const groups = {}
  for (const clip of clips) {
    const key = clip.timestamp ? new Date(clip.timestamp).toDateString() : 'Unknown Date'
    if (!groups[key]) groups[key] = []
    groups[key].push(clip)
  }
  return groups
}

export default function App() {
  const [clips, setClips] = useState([])
  const [filterVehicle, setFilterVehicle] = useState('')
  const [filterEvent, setFilterEvent] = useState('')
  const [filterFolder, setFilterFolder] = useState('')
  const [filterCamera, setFilterCamera] = useState('')
  const [filterDateFrom, setFilterDateFrom] = useState('')
  const [filterDateTo, setFilterDateTo] = useState('')
  const [vehicles, setVehicles] = useState([])
  const [folders, setFolders] = useState([])
  const [eventTypes, setEventTypes] = useState([])
  const [playingClip, setPlayingClip] = useState(null)
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [thumbnails, setThumbnails] = useState({})
  const [thumbnailLoading, setThumbnailLoading] = useState({})

  const loadClips = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getClips({
        vehicle: filterVehicle || undefined,
        event_type: filterEvent || undefined,
        folder: filterFolder || undefined,
        camera: filterCamera || undefined,
        date_from: filterDateFrom || undefined,
        date_to: filterDateTo || undefined,
      })
      setClips(data.clips)
      setVehicles(data.vehicles)
      setFolders(data.folders)
      setEventTypes(data.eventTypes)
    } catch (e) {
      console.error('Failed to load clips:', e)
    } finally {
      setLoading(false)
    }
  }, [filterVehicle, filterEvent, filterFolder, filterCamera, filterDateFrom, filterDateTo])

  const loadHealth = useCallback(async () => {
    try {
      setHealth(await getHealth())
    } catch (e) {
      console.error('Health check failed:', e)
    }
  }, [])

  useEffect(() => { loadHealth() }, [loadHealth])
  useEffect(() => { loadClips() }, [loadClips])

  useEffect(() => {
    const visible = clips.slice(0, 48)
    for (const clip of visible) {
      if (!thumbnails[clip.id] && !thumbnailLoading[clip.id]) {
        setThumbnailLoading(prev => ({ ...prev, [clip.id]: true }))
        const img = new Image()
        img.onload = () => setThumbnails(prev => ({ ...prev, [clip.id]: img.src }))
        img.onerror = () => setThumbnailLoading(prev => ({ ...prev, [clip.id]: false }))
        img.src = clip.thumbnailUrl
      }
    }
  }, [clips, thumbnails, thumbnailLoading])

  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') setPlayingClip(null)
      if (e.key === 'ArrowRight' && playingClip) {
        const idx = clips.findIndex(c => c.id === playingClip.id)
        if (idx < clips.length - 1) setPlayingClip(clips[idx + 1])
      }
      if (e.key === 'ArrowLeft' && playingClip) {
        const idx = clips.findIndex(c => c.id === playingClip.id)
        if (idx > 0) setPlayingClip(clips[idx - 1])
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [playingClip, clips])

  const handleRescan = async () => {
    setScanning(true)
    try {
      await triggerScan()
      setTimeout(() => { setScanning(false); loadClips() }, 5000)
    } catch {
      setScanning(false)
    }
  }

  const grouped = groupByDate(clips)
  const clipCount = clips.length
  const gearGuardCount = clips.filter(c => c.eventType === 'gear_guard').length

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="logo">🚗</span>
          <h1 className="title">DriveCam Viewer</h1>
          {health && <span className={`status-dot ${health.status}`} title={`${clipCount} clips cached`} />}
        </div>
        <div className="header-right">
          <span className="clip-count">{clipCount} clips</span>
          {gearGuardCount > 0 && (
            <span className="badge gear-guard">{gearGuardCount} Gear Guard</span>
          )}
          <button className={`btn btn-rescan ${scanning ? 'scanning' : ''}`} onClick={handleRescan} disabled={scanning}>
            {scanning ? '⏳ Scanning…' : '🔄 Rescan'}
          </button>
        </div>
      </header>

      <div className="filter-bar">
        <div className="filter-row">
          <select value={filterVehicle} onChange={e => setFilterVehicle(e.target.value)} className="filter-select">
            <option value="">All Vehicles</option>
            {vehicles.map(v => <option key={v} value={v}>{v.charAt(0).toUpperCase() + v.slice(1)}</option>)}
          </select>

          <select value={filterEvent} onChange={e => setFilterEvent(e.target.value)} className="filter-select">
            <option value="">All Events</option>
            {eventTypes.map(t => <option key={t} value={t}>{EVENT_LABELS[t] || t}</option>)}
          </select>

          <select value={filterFolder} onChange={e => setFilterFolder(e.target.value)} className="filter-select">
            <option value="">All Folders</option>
            {folders.map(f => <option key={f} value={f}>{f}</option>)}
          </select>

          <select value={filterCamera} onChange={e => setFilterCamera(e.target.value)} className="filter-select">
            <option value="">All Cameras</option>
            <option value="front">Front</option>
            <option value="back">Rear</option>
            <option value="left">Left</option>
            <option value="right">Right</option>
            <option value="cabin">Cabin</option>
          </select>

          <input type="date" value={filterDateFrom} onChange={e => setFilterDateFrom(e.target.value)} className="filter-input" />
          <span className="date-sep">→</span>
          <input type="date" value={filterDateTo} onChange={e => setFilterDateTo(e.target.value)} className="filter-input" />

          {(filterVehicle || filterEvent || filterFolder || filterCamera || filterDateFrom || filterDateTo) && (
            <button className="btn btn-clear" onClick={() => {
              setFilterVehicle(''); setFilterEvent(''); setFilterFolder('')
              setFilterCamera(''); setFilterDateFrom(''); setFilterDateTo('')
            }}>✕ Clear</button>
          )}
        </div>
      </div>

      <main className="main">
        {loading ? (
          <div className="loading">
            <div className="spinner" />
            <p>Loading clips…</p>
          </div>
        ) : clips.length === 0 ? (
          <div className="empty">
            <p>No clips found.</p>
            <p className="sub">Mount your NAS share at <code>/share</code> and click Rescan.</p>
            <button className="btn btn-primary" onClick={handleRescan}>🔄 Rescan</button>
          </div>
        ) : (
          Object.entries(grouped).map(([date, dateClips]) => (
            <section key={date} className="day-group">
              <h2 className="day-header">
                {formatDate(dateClips[0]?.timestamp)}
                <span className="day-count">{dateClips.length} clips</span>
              </h2>
              <div className="clip-grid">
                {dateClips.map(clip => (
                  <ClipCard
                    key={clip.id}
                    clip={clip}
                    thumbnail={thumbnails[clip.id]}
                    onPlay={() => setPlayingClip(clip)}
                  />
                ))}
              </div>
            </section>
          ))
        )}
      </main>

      {playingClip && (
        <VideoModal
          clip={playingClip}
          clips={clips}
          onClose={() => setPlayingClip(null)}
          setPlayingClip={setPlayingClip}
        />
      )}
    </div>
  )
}

function ClipCard({ clip, thumbnail, onPlay }) {
  const color = EVENT_COLORS[clip.eventType] || EVENT_COLORS.unknown
  const label = EVENT_LABELS[clip.eventType] || 'Unknown'

  return (
    <div
      className={`clip-card ${clip.eventType === 'gear_guard' ? 'gear-guard' : ''}`}
      onClick={onPlay}
      style={{ '--event-color': color }}
    >
      <div className="clip-thumb">
        {thumbnail ? (
          <img src={thumbnail} alt={clip.filename} loading="lazy" />
        ) : (
          <div className="clip-thumb-placeholder">🎥</div>
        )}
        <div className="clip-duration">{formatDuration(clip.duration)}</div>
        <div className="clip-play-overlay">▶</div>
        <div className="clip-event-badge" style={{ background: color }} title={label}>{label}</div>
      </div>
      <div className="clip-info">
        <div className="clip-time">{formatTime(clip.timestamp)}</div>
        <div className="clip-meta">
          <span className="clip-camera">{CAMERA_LABELS[clip.cameraAngle] || clip.cameraAngle}</span>
          <span className="clip-folder">{clip.folder}</span>
        </div>
        <div className="clip-size">{clip.sizeString}</div>
      </div>
    </div>
  )
}

function VideoModal({ clip, clips, onClose, setPlayingClip }) {
  const videoRef = useRef(null)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    setPlaying(false)
    const video = videoRef.current
    if (!video) return
    video.pause()
    video.currentTime = 0
    video.load()
  }, [clip])

  const color = EVENT_COLORS[clip.eventType] || EVENT_COLORS.unknown
  const currentIdx = clips.findIndex(c => c.id === clip.id)
  const hasPrev = currentIdx > 0
  const hasNext = currentIdx < clips.length - 1

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">
            <span className="modal-event" style={{ background: color }}>{EVENT_LABELS[clip.eventType]}</span>
            <span className="modal-filename">{clip.filename}</span>
          </div>
          <div className="modal-nav">
            <button className="btn btn-nav" onClick={() => setPlayingClip(clips[currentIdx - 1])} disabled={!hasPrev}>◀</button>
            <button className="btn btn-nav" onClick={() => setPlayingClip(clips[currentIdx + 1])} disabled={!hasNext}>▶</button>
            <button className="btn btn-nav btn-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="modal-video-wrap">
          <video
            ref={videoRef}
            key={clip.id}
            src={clip.downloadUrl}
            controls
            autoPlay
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onClick={() => playing ? videoRef.current?.pause() : videoRef.current?.play()}
          />
        </div>

        <div className="modal-meta">
          <div className="modal-meta-item"><span className="meta-label">Time</span><span className="meta-value">{formatDate(clip.timestamp)} {formatTime(clip.timestamp)}</span></div>
          <div className="modal-meta-item"><span className="meta-label">Camera</span><span className="meta-value">{CAMERA_LABELS[clip.cameraAngle] || clip.cameraAngle}</span></div>
          <div className="modal-meta-item"><span className="meta-label">Folder</span><span className="meta-value">{clip.folder}</span></div>
          <div className="modal-meta-item"><span className="meta-label">Size</span><span className="meta-value">{clip.sizeString}</span></div>
        </div>
      </div>
    </div>
  )
}