import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { getEvents, getClips, getHealth, triggerScan, getShares, createShare, updateShare, deleteShare, testShare, getScanStatus, getCloudProviders, createCloudProvider, updateCloudProvider, deleteCloudProvider, testCloudProvider, syncCloudProvider, getCloudOAuthUrl, uploadFiles, getStorageStats, getVehicleLabels, reassignVehicle, deleteClip, deleteEvent } from './api'

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

const PROTOCOL_LABELS = {
  smb: 'SMB',
  ftp: 'FTP',
  nfs: 'NFS',
}

const VEHICLE_PREFIXES = [
  { prefix: 'teslacam_', brand: 'Tesla' },
  { prefix: 'teslacam', brand: 'Tesla' },
  { prefix: 'rivian_dashcam_', brand: 'Rivian' },
  { prefix: 'rivian_dashcam', brand: 'Rivian' },
]

function formatVehicleFolder(folder) {
  const fl = folder.toLowerCase()
  for (const { prefix, brand } of VEHICLE_PREFIXES) {
    if (fl === prefix.replace(/_$/, '')) return brand
    if (fl.startsWith(prefix) && folder.length > prefix.length) {
      return `${brand}: ${folder.slice(prefix.length)}`
    }
  }
  return folder
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

function groupByDate(events) {
  const groups = {}
  for (const ev of events) {
    const key = ev.timestamp ? new Date(ev.timestamp).toDateString() : 'Unknown Date'
    if (!groups[key]) groups[key] = []
    groups[key].push(ev)
  }
  return groups
}

function byteCountFmt(bytes) {
  if (bytes === 0) return '0 B'
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (bytes < 1024) return `${bytes.toFixed(1)} ${unit}`
    bytes /= 1024
  }
  return `${bytes.toFixed(1)} PB`
}

function StorageCalculator({ stats, onImportClick }) {
  if (!stats) return null
  if (!stats.total_clips) {
    return (
      <div className="storage-calculator">
        <span className="storage-label">No footage yet.</span>
        <span className="storage-label">Import SD Card to get started.</span>
      </div>
    )
  }
  const { clips_per_hour, total_size_bytes, total_clips } = stats
  if (!clips_per_hour || clips_per_hour === 0) return null

  const GB = 1024 ** 3
  const sizes = [
    { label: '256 GB', gb: 256 },
    { label: '512 GB', gb: 512 },
    { label: '1 TB', gb: 1024 },
  ]

  return (
    <div className="storage-calculator">
      <span className="storage-label">SD card estimate:</span>
      {sizes.map(({ label, gb }) => {
        const bytesPerHour = clips_per_hour * (total_size_bytes / total_clips)
        const hoursLeft = (gb * GB) / bytesPerHour
        const daysLeft = hoursLeft / 24
        return (
          <span key={label} className="storage-estimate" title={`${Math.round(hoursLeft)}h of footage`}>
            {label}: <strong>{daysLeft < 1 ? `${Math.round(hoursLeft)}h` : `${Math.round(daysLeft)}d`}</strong>
          </span>
        )
      })}
    </div>
  )
}

async function getFilesFromDir(dirHandle) {
  const files = []
  async function walk(dir, path = '') {
    for await (const entry of dir.values()) {
      const entryPath = path ? `${path}/${entry.name}` : entry.name
      if (entry.kind === 'directory') {
        const subDir = await dir.getDirectoryHandle(entry.name)
        await walk(subDir, entryPath)
      } else if (entry.kind === 'file') {
        const ext = entry.name.split('.').pop().toLowerCase()
        if (['mp4', 'mov', 'ts', 'avi', 'mkv'].includes(ext)) {
          files.push({ path: entryPath, handle: entry })
        }
      }
    }
  }
  await walk(dirHandle)
  return files
}

const VEHICLE_DEFS = [
  {
    patterns: ['teslacam'],
    canonical: 'TeslaCam',
    label: 'Tesla',
    subfolders: ['recentclips', 'savedclips', 'sentryclips'],
  },
  {
    patterns: ['rivian_dashcam'],
    canonical: 'rivian_dashcam',
    label: 'Rivian',
    subfolders: ['gearguardvideo', 'incidentcam', 'roadcam'],
  },
]

function detectVehicle(files) {
  for (const { path } of files.slice(0, 10)) {
    const top = path.split('/')[0].toLowerCase()
    for (const def of VEHICLE_DEFS) {
      if (def.patterns.includes(top)) return { def, existingLabel: null, hasVehicleRoot: true }
      for (const p of def.patterns) {
        if (top.startsWith(p + '_')) {
          return { def, existingLabel: path.split('/')[0].slice(p.length + 1), hasVehicleRoot: true }
        }
      }
    }
    for (const def of VEHICLE_DEFS) {
      if (def.subfolders.includes(top)) return { def, existingLabel: null, hasVehicleRoot: false }
    }
  }
  return null
}

function applyVehicleLabel(files, vehicleInfo, label) {
  if (!vehicleInfo || !label.trim()) return files
  const root = `${vehicleInfo.def.canonical}_${label.trim()}`
  return files.map(({ path, file }) => ({
    path: vehicleInfo.hasVehicleRoot
      ? [root, ...path.split('/').slice(1)].join('/')
      : `${root}/${path}`,
    file,
  }))
}

function formatSpeed(bps) {
  if (bps >= 1024 * 1024) return `${(bps / (1024 * 1024)).toFixed(1)} MB/s`
  if (bps >= 1024) return `${(bps / 1024).toFixed(0)} KB/s`
  return `${Math.round(bps)} B/s`
}

function formatTimeRemaining(secs) {
  if (!secs || secs < 1) return ''
  if (secs < 60) return `~${Math.round(secs)}s left`
  if (secs < 3600) return `~${Math.round(secs / 60)}m left`
  return `~${(secs / 3600).toFixed(1)}h left`
}

function ImportPanel({ onClose, onImported }) {
  const fileInputRef = useRef(null)
  const [isDragging, setIsDragging] = useState(false)
  const [files, setFiles] = useState([])
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [vehicleLabel, setVehicleLabel] = useState('')
  const [vehicleSuggestions, setVehicleSuggestions] = useState([])
  const [uploadStats, setUploadStats] = useState(null)
  const [reassignOffer, setReassignOffer] = useState(null)
  const [reassigning, setReassigning] = useState(false)
  const [stopAfterCurrent, setStopAfterCurrent] = useState(false)
  const stopRef = useRef(false)

  const vehicleInfo = useMemo(() => detectVehicle(files), [files])

  useEffect(() => {
    getVehicleLabels().then(data => {
      const labels = new Set()
      for (const folder of data.folders || []) {
        const fl = folder.toLowerCase()
        for (const def of VEHICLE_DEFS) {
          for (const p of def.patterns) {
            if (fl.startsWith(p + '_') && folder.length > p.length + 1) {
              labels.add(folder.slice(p.length + 1))
            }
          }
        }
      }
      setVehicleSuggestions([...labels])
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (vehicleInfo?.existingLabel && !vehicleLabel) setVehicleLabel(vehicleInfo.existingLabel)
  }, [vehicleInfo])

  const scanDirHandle = async (dirHandle) => {
    const found = []
    async function walk(dir, path = '') {
      for await (const entry of dir.values()) {
        const entryPath = path ? `${path}/${entry.name}` : entry.name
        if (entry.kind === 'directory') {
          try {
            const subDir = await dir.getDirectoryHandle(entry.name)
            await walk(subDir, entryPath)
          } catch {}
        } else if (entry.kind === 'file') {
          const ext = entry.name.split('.').pop().toLowerCase()
          if (['mp4', 'mov', 'ts', 'avi', 'mkv'].includes(ext)) {
            const file = await entry.getFile()
            found.push({ path: entryPath, file })
          }
        }
      }
    }
    await walk(dirHandle)
    return found
  }

  const scanDropEntry = (entry, path = '') => {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file((file) => {
          const ext = file.name.split('.').pop().toLowerCase()
          if (['mp4', 'mov', 'ts', 'avi', 'mkv'].includes(ext)) {
            resolve([{ path: path ? `${path}/${file.name}` : file.name, file }])
          } else {
            resolve([])
          }
        })
      } else if (entry.isDirectory) {
        const reader = entry.createReader()
        const allFiles = []
        const readDir = () => {
          reader.readEntries(async (entries) => {
            if (entries.length === 0) {
              resolve(allFiles)
            } else {
              for (const e of entries) {
                const childFiles = await scanDropEntry(e, path ? `${path}/${entry.name}` : entry.name)
                allFiles.push(...childFiles)
              }
              readDir()
            }
          })
        }
        readDir()
      } else {
        resolve([])
      }
    })
  }

  const handleDrop = async (e) => {
    e.preventDefault()
    setIsDragging(false)
    try {
      setMessage('Scanning for video files…')
      setError('')
      const items = Array.from(e.dataTransfer.items)
      let found = []
      for (const item of items) {
        if (item.kind === 'file') {
          const entry = item.webkitGetAsEntry()
          if (entry) {
            const files = await scanDropEntry(entry)
            found = found.concat(files)
          }
        }
      }
      setFiles(found)
      setMessage(`${found.length} video file(s) found`)
    } catch (e) {
      setError('Failed to read dropped folder: ' + e.message)
    }
  }

  const hasDirectoryPicker = typeof window.showDirectoryPicker !== 'undefined'

  const selectDir = async () => {
    try {
      setMessage('Scanning for video files…')
      setError('')
      if (hasDirectoryPicker) {
        const handle = await window.showDirectoryPicker({ mode: 'read' })
        const found = await scanDirHandle(handle)
        setFiles(found)
        setMessage(`${found.length} video file(s) found`)
      } else {
        fileInputRef.current?.click()
      }
    } catch (e) {
      if (e.name !== 'AbortError') setError('Failed to open SD card: ' + e.message)
    }
  }

  const handleFirefoxFiles = (e) => {
    const entries = Array.from(e.target.files)
    const videoFiles = []
    for (const file of entries) {
      const ext = file.name.split('.').pop().toLowerCase()
      if (['mp4', 'mov', 'ts', 'avi', 'mkv'].includes(ext)) {
        const relPath = file.webkitRelativePath || file.name
        videoFiles.push({ path: relPath, file })
      }
    }
    setFiles(videoFiles)
    setMessage(`${videoFiles.length} video file(s) found`)
    e.target.value = ''
  }

  const handleUpload = async () => {
    const filesToUpload = applyVehicleLabel(files, vehicleInfo, vehicleLabel)
    if (!filesToUpload.length) return
    setUploading(true)
    setProgress(0)
    setError('')
    setUploadStats(null)
    setReassignOffer(null)
    setStopAfterCurrent(false)
    stopRef.current = false

    const safePaths = filesToUpload.map(({ path, file: fileObj }) => ({
      safe: path.replace(/^\//, ''),
      file: fileObj,
    }))

    const totalBytes = safePaths.reduce((sum, { file }) => sum + (file?.size || 0), 0)
    const startTime = Date.now()
    let bytesCompleted = 0
    let saved = 0, skipped = 0
    const errors = []

    const uploadOne = (safe, file, idx) =>
      new Promise((resolve) => {
        const formData = new FormData()
        formData.append('paths', JSON.stringify([safe]))
        formData.append('files', file, safe)
        const xhr = new XMLHttpRequest()
        xhr.upload.addEventListener('progress', (e) => {
          if (e.lengthComputable) {
            const totalDone = bytesCompleted + e.loaded
            const pct = totalBytes > 0
              ? Math.round((totalDone / totalBytes) * 100)
              : Math.round(((idx + e.loaded / e.total) / safePaths.length) * 100)
            setProgress(Math.min(pct, 99))
            const elapsed = (Date.now() - startTime) / 1000
            if (elapsed > 0.5 && totalDone > 0) {
              const speed = totalDone / elapsed
              const remaining = totalBytes > 0 ? (totalBytes - totalDone) / speed : null
              setUploadStats({ speed, remaining })
            }
          }
        })
        xhr.addEventListener('load', () => {
          bytesCompleted += file?.size || 0
          try {
            const data = JSON.parse(xhr.responseText)
            saved += data.saved ?? 0
            skipped += data.skipped ?? 0
            if (data.errors?.length) errors.push(...data.errors)
          } catch {
            errors.push(`${safe}: unexpected server response`)
          }
          resolve()
        })
        xhr.addEventListener('error', () => {
          bytesCompleted += file?.size || 0
          errors.push(`${safe}: network error`)
          resolve()
        })
        xhr.open('POST', '/api/upload')
        xhr.send(formData)
      })

    let stoppedAt = null
    for (let i = 0; i < safePaths.length; i++) {
      if (stopRef.current) {
        stoppedAt = i
        break
      }
      await uploadOne(safePaths[i].safe, safePaths[i].file, i)
    }

    setProgress(stoppedAt != null ? Math.round((stoppedAt / safePaths.length) * 100) : 100)
    setUploadStats(null)
    setStopAfterCurrent(false)
    stopRef.current = false
    let msg = `Imported ${saved} file(s)`
    if (skipped > 0) msg += `, ${skipped} already imported`
    if (errors.length) msg += `, ${errors.length} failed`
    if (stoppedAt != null) {
      const remaining = safePaths.length - stoppedAt
      msg += ` · Stopped — ${remaining} file(s) remaining. Re-import to resume (already saved files will be skipped).`
    }
    setMessage(msg)
    if (errors.length) setError(errors.slice(0, 5).join('\n'))
    if (saved > 0) {
      onImported()
      if (vehicleInfo && vehicleLabel.trim()) {
        try {
          const data = await getVehicleLabels()
          const unlabeled = vehicleInfo.def.canonical
          if ((data.folders || []).includes(unlabeled)) {
            setReassignOffer({ fromFolder: unlabeled, toFolder: `${unlabeled}_${vehicleLabel.trim()}` })
          }
        } catch {}
      }
    }
    setUploading(false)
  }

  const handleReassign = async () => {
    if (!reassignOffer) return
    setReassigning(true)
    try {
      const result = await reassignVehicle(reassignOffer.fromFolder, reassignOffer.toFolder)
      setMessage(prev => `${prev} · Moved ${result.moved} unlabeled clips to ${reassignOffer.toFolder}`)
      setReassignOffer(null)
    } catch (e) {
      setError('Move failed: ' + e.message)
    }
    setReassigning(false)
  }

  const grouped = {}
  for (const f of files) {
    const parts = f.path.split('/')
    const key = parts.slice(0, -1).join('/') || '/'
    if (!grouped[key]) grouped[key] = []
    grouped[key].push(f)
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="import-panel">
        <div className="modal-header">
          <h2>📥 Import from SD Card</h2>
          <button className="btn btn-icon" onClick={onClose}>✕</button>
        </div>

        <div className="import-body">
          <p className="import-desc">
            Select your SD card root folder. Tesla: select the TeslaCam folder.
            Rivian: select the SD card root (contains GearGuardVideo, IncidentCam, RoadCam).
          </p>

          <div
            className={`import-dropzone ${isDragging ? 'dragging' : ''}`}
            style={{ textAlign: 'center' }}
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true) }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
          >
            <div className="drop-icon">📂</div>
            {hasDirectoryPicker ? (
              <>
                <div className="drop-title">Drop your SD Card folder here</div>
                <div className="drop-hint">Or use the button below to browse</div>
              </>
            ) : (
              <div className="drop-title">Select your SD Card folder</div>
            )}
            <div className="drop-or">— or —</div>
            <button
              className="btn btn-primary"
              onClick={selectDir}
              disabled={uploading}
            >
              📂 Select SD Card Folder
            </button>
          </div>

          <input
            ref={el => { fileInputRef.current = el; if (el) el.webkitdirectory = true }}
            type="file"
            multiple
            onChange={handleFirefoxFiles}
            style={{ display: 'none' }}
          />

          {message && <div className="import-message">{message}</div>}
          {error && <div className="import-error">{error}</div>}

          {files.length > 0 && (
            <>
              {vehicleInfo && (
                <div className="vehicle-section">
                  <div className="vehicle-detected-badge">{vehicleInfo.def.label} detected</div>
                  <div className="vehicle-label-row">
                    <label className="vehicle-label-label">Vehicle name</label>
                    <input
                      type="text"
                      className="vehicle-label-input"
                      placeholder={`e.g. My ${vehicleInfo.def.label}`}
                      value={vehicleLabel}
                      onChange={e => setVehicleLabel(e.target.value)}
                      list="vehicle-suggestions"
                      disabled={uploading}
                    />
                    <datalist id="vehicle-suggestions">
                      {vehicleSuggestions.map(s => <option key={s} value={s} />)}
                    </datalist>
                  </div>
                  {vehicleLabel.trim() && (
                    <div className="vehicle-preview">
                      Saves to: <code>{vehicleInfo.def.canonical}_{vehicleLabel.trim()}/</code>
                    </div>
                  )}
                </div>
              )}

              <div className="import-summary">
                {Object.entries(grouped).map(([folder, fls]) => (
                  <div key={folder} className="import-folder">
                    <span className="import-folder-name">{folder}/</span>
                    <span className="import-folder-count">{fls.length} file(s)</span>
                  </div>
                ))}
              </div>

              {uploading ? (
                <div className="import-progress">
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${progress}%` }} />
                  </div>
                  <div className="progress-meta">
                    <span>{progress}%</span>
                    {uploadStats && (
                      <>
                        <span className="progress-speed">{formatSpeed(uploadStats.speed)}</span>
                        {formatTimeRemaining(uploadStats.remaining) && (
                          <span className="progress-remaining">{formatTimeRemaining(uploadStats.remaining)}</span>
                        )}
                      </>
                    )}
                    <button
                      className="btn btn-clear btn-stop"
                      onClick={() => { stopRef.current = true; setStopAfterCurrent(true) }}
                      disabled={stopAfterCurrent}
                      title="Finish the current file then stop"
                    >
                      {stopAfterCurrent ? 'Stopping…' : '⏹ Stop'}
                    </button>
                  </div>
                </div>
              ) : (
                <button className="btn btn-primary" onClick={handleUpload}>
                  ▶ Import {files.length} File{files.length !== 1 ? 's' : ''}
                </button>
              )}

              {reassignOffer && (
                <div className="reassign-offer">
                  <span>Unlabeled {vehicleInfo?.def.label} clips found. Move to <code>{reassignOffer.toFolder}</code>?</span>
                  <div className="reassign-actions">
                    <button className="btn btn-primary" onClick={handleReassign} disabled={reassigning}>
                      {reassigning ? 'Moving…' : 'Move'}
                    </button>
                    <button className="btn btn-clear" onClick={() => setReassignOffer(null)}>Skip</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default function App() {
  const [events, setEvents] = useState([])
  const [filterVehicle, setFilterVehicle] = useState('')
  const [filterVehicleFolder, setFilterVehicleFolder] = useState('')
  const [filterEvent, setFilterEvent] = useState('')
  const [filterFolder, setFilterFolder] = useState('')
  const [filterCamera, setFilterCamera] = useState('')
  const [filterDateFrom, setFilterDateFrom] = useState('')
  const [filterDateTo, setFilterDateTo] = useState('')
  const [vehicles, setVehicles] = useState([])
  const [vehicleFolders, setVehicleFolders] = useState([])
  const [folders, setFolders] = useState([])
  const [eventTypes, setEventTypes] = useState([])
  const [cameras, setCameras] = useState([])
  const [activeEvent, setActiveEvent] = useState(null)
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [colorTheme, setColorTheme] = useState('auto')
  const [darkMode, setDarkMode] = useState('auto')
  const [currentColorTheme, setCurrentColorTheme] = useState('default')
  const [isDark, setIsDark] = useState(true)
  const [showShares, setShowShares] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [storageStats, setStorageStats] = useState(null)
  const [hasMore, setHasMore] = useState(false)
  const [oldestTimestamp, setOldestTimestamp] = useState(null)
  const loadMoreRef = useRef(null)

  const loadEvents = useCallback(async () => {
    setLoading(true)
    setEvents([])
    setHasMore(false)
    setOldestTimestamp(null)
    try {
      const data = await getEvents({
        vehicle: filterVehicle || undefined,
        vehicle_folder: filterVehicleFolder || undefined,
        event_type: filterEvent || undefined,
        folder: filterFolder || undefined,
        camera: filterCamera || undefined,
        date_from: filterDateFrom || undefined,
        date_to: filterDateTo || undefined,
      })
      setEvents(data.events || [])
      setHasMore(data.hasMore)
      setOldestTimestamp(data.oldestTimestamp)
      setVehicles(data.vehicles)
      setVehicleFolders(data.vehicleFolders || [])
      setFolders(data.folders)
      setEventTypes(data.eventTypes)
      setCameras(data.cameras || [])
    } catch (e) {
      console.error('Failed to load events:', e)
    } finally {
      setLoading(false)
    }
  }, [filterVehicle, filterVehicleFolder, filterEvent, filterFolder, filterCamera, filterDateFrom, filterDateTo])

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore || !oldestTimestamp) return
    setLoadingMore(true)
    try {
      const data = await getEvents({
        vehicle: filterVehicle || undefined,
        vehicle_folder: filterVehicleFolder || undefined,
        event_type: filterEvent || undefined,
        folder: filterFolder || undefined,
        camera: filterCamera || undefined,
        date_from: filterDateFrom || undefined,
        date_to: filterDateTo || undefined,
        before_timestamp: oldestTimestamp,
      })
      setEvents(prev => [...prev, ...data.events])
      setHasMore(data.hasMore)
      setOldestTimestamp(data.oldestTimestamp)
    } catch (e) {
      console.error('Failed to load more events:', e)
    } finally {
      setLoadingMore(false)
    }
  }, [loadingMore, hasMore, oldestTimestamp, filterVehicle, filterVehicleFolder, filterEvent, filterFolder, filterCamera, filterDateFrom, filterDateTo])

  const loadHealth = useCallback(async () => {
    try {
      setHealth(await getHealth())
    } catch (e) {
      console.error('Health check failed:', e)
    }
  }, [])

  useEffect(() => { loadHealth() }, [loadHealth])
  useEffect(() => { loadEvents() }, [loadEvents])

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loadingMore) {
          loadMore()
        }
      },
      { rootMargin: '400px' }
    )
    if (loadMoreRef.current) {
      observer.observe(loadMoreRef.current)
    }
    return () => observer.disconnect()
  }, [hasMore, loadingMore, loadMore])

  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') {
        setActiveEvent(null)
        setShowShares(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  useEffect(() => {
    if (colorTheme === 'auto') {
      if (filterVehicle === 'rivian') setCurrentColorTheme('rivian')
      else if (filterVehicle === 'tesla') setCurrentColorTheme('tesla')
      else setCurrentColorTheme('default')
    } else {
      setCurrentColorTheme(colorTheme)
    }
  }, [filterVehicle, colorTheme])

  useEffect(() => {
    if (darkMode === 'auto') {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      setIsDark(prefersDark)
    } else {
      setIsDark(darkMode === 'dark')
    }
  }, [darkMode])

  useEffect(() => {
    const root = document.documentElement
    if (currentColorTheme === 'default') {
      root.removeAttribute('data-theme')
    } else {
      root.setAttribute('data-theme', currentColorTheme)
    }
    if (isDark) {
      root.setAttribute('data-dark', '')
    } else {
      root.removeAttribute('data-dark')
    }
  }, [currentColorTheme, isDark])

  useEffect(() => {
    getStorageStats().then(setStorageStats).catch(() => setStorageStats(null))
  }, [])

  const handleRescan = async () => {
    setScanning(true)
    try {
      await triggerScan()
      while (true) {
        await new Promise(r => setTimeout(r, 500))
        const { scanning } = await getScanStatus()
        if (!scanning) break
      }
      loadEvents()
    } catch {
      loadEvents()
    } finally {
      setScanning(false)
    }
  }

  const grouped = groupByDate(events)
  const eventCount = events.length
  const gearGuardCount = events.filter(e => e.eventType === 'gear_guard').length

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="logo">🚗</span>
          <h1 className="title">DriveCam Viewer</h1>
          {health && <span className={`status-dot ${health.status}`} title={`${eventCount} events cached`} />}
        </div>
        <div className="header-right">
          <button
            className="btn btn-icon"
            onClick={() => setDarkMode(darkMode === 'dark' ? 'light' : 'dark')}
            title={isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          >
            {isDark ? '☀️' : '🌙'}
          </button>
          <select
            value={colorTheme}
            onChange={(e) => setColorTheme(e.target.value)}
            className="theme-select"
            title="Color Theme"
          >
            <option value="auto">Auto</option>
            <option value="rivian">Rivian</option>
            <option value="tesla">Tesla</option>
          </select>
          <button className="btn btn-icon" onClick={() => setShowShares(true)} title="Manage Shares">
            📁
          </button>
          <button className="btn btn-icon" onClick={() => setShowImport(true)} title="Import from SD Card">
            📤
          </button>
          <span className="clip-count">{eventCount} events{hasMore ? '+' : ''}</span>
          {gearGuardCount > 0 && (
            <span className="badge gear-guard">{gearGuardCount} Gear Guard</span>
          )}
          <button className={`btn btn-rescan ${scanning ? 'scanning' : ''}`} onClick={handleRescan} disabled={scanning}>
            {scanning ? '⏳ Scanning…' : '🔄 Rescan'}
          </button>
        </div>
      </header>

      <StorageCalculator stats={storageStats} />

      <div className="filter-bar">
        <div className="filter-row">
          <select value={filterVehicleFolder} onChange={e => setFilterVehicleFolder(e.target.value)} className="filter-select">
            <option value="">All Vehicles</option>
            {vehicleFolders.map(f => {
              const label = formatVehicleFolder(f)
              return <option key={f} value={f}>{label}</option>
            })}
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
            {cameras.map(c => <option key={c} value={c}>{CAMERA_LABELS[c] || c}</option>)}
          </select>

          <input type="date" value={filterDateFrom} onChange={e => setFilterDateFrom(e.target.value)} className="filter-input" />
          <span className="date-sep">→</span>
          <input type="date" value={filterDateTo} onChange={e => setFilterDateTo(e.target.value)} className="filter-input" />

          {(filterVehicleFolder || filterEvent || filterFolder || filterCamera || filterDateFrom || filterDateTo) && (
            <button className="btn btn-clear" onClick={() => {
              setFilterVehicleFolder(''); setFilterEvent(''); setFilterFolder('')
              setFilterCamera(''); setFilterDateFrom(''); setFilterDateTo('')
            }}>✕ Clear</button>
          )}
        </div>
      </div>

      <main className="main">
        {loading ? (
          <div className="loading">
            <div className="spinner" />
            <p>Loading events…</p>
          </div>
        ) : events.length === 0 ? (
          <div className="empty">
            <p>No events found.</p>
            <p className="sub">Mount your NAS share at <code>/share</code> and click Rescan.</p>
            <button className="btn btn-primary" onClick={handleRescan}>🔄 Rescan</button>
          </div>
        ) : (
          Object.entries(grouped).map(([date, dateEvents]) => (
            <section key={date} className="day-group">
              <h2 className="day-header">
                {formatDate(new Date(date))}
                <span className="day-count">{dateEvents.length} events</span>
              </h2>
              <div className="clip-grid">
                {dateEvents.map(ev => (
                  <EventCard
                    key={ev.eventKey}
                    event={ev}
                    onClick={() => setActiveEvent(ev)}
                  />
                ))}
              </div>
            </section>
          ))
        )}
        {loadingMore && (
          <div className="loading-more">
            <div className="spinner small" />
            <span>Loading more…</span>
          </div>
        )}
        <div ref={loadMoreRef} style={{ height: '1px' }} />
      </main>

      {activeEvent && (
        <EventDetail
          event={activeEvent}
          allEvents={events}
          onClose={() => setActiveEvent(null)}
          onNavigate={setActiveEvent}
          onDeleted={() => {
            setActiveEvent(null)
            loadEvents()
          }}
        />
      )}

      {showShares && (
        <SharesPanel
          onClose={() => setShowShares(false)}
          onSaved={loadEvents}
        />
      )}

      {showImport && (
        <ImportPanel
          onClose={() => setShowImport(false)}
          onImported={() => {
            setShowImport(false)
            loadEvents()
            getStorageStats().then(setStorageStats).catch(() => {})
          }}
        />
      )}
    </div>
  )
}

function EventCard({ event, onClick }) {
  const color = EVENT_COLORS[event.eventType] || EVENT_COLORS.unknown
  const label = EVENT_LABELS[event.eventType] || 'Unknown'
  const thumb = event.thumbnailUrl

  return (
    <div
      className={`clip-card ${event.eventType === 'gear_guard' ? 'gear-guard' : ''}`}
      onClick={onClick}
      style={{ '--event-color': color }}
    >
      <div className="clip-thumb">
        {thumb ? (
          <img src={thumb} alt={event.eventKey} loading="lazy" />
        ) : (
          <div className="clip-thumb-placeholder">🎥</div>
        )}
        <div className="clip-play-overlay">▶</div>
        <div className="clip-event-badge" style={{ background: color }} title={label}>{label}</div>
        <div className="clip-camera-count" title={`${event.cameraCount} camera${event.cameraCount > 1 ? 's' : ''}`}>
          {event.cameraCount} 📷
        </div>
      </div>
      <div className="clip-info">
        <div className="clip-time">{formatTime(event.timestamp)}</div>
        <div className="clip-meta">
          <span className="clip-camera">{event.cameraCount} cam{event.cameraCount > 1 ? 's' : ''}</span>
          <span className="clip-folder">{event.folder}</span>
        </div>
        <div className="clip-size">{event.sizeString}</div>
      </div>
    </div>
  )
}

function EventDetail({ event, allEvents, onClose, onNavigate, onDeleted }) {
  const videosRef = useRef({})
  const [multiCam, setMultiCam] = useState(false)
  const [activeCamera, setActiveCamera] = useState(event.clips?.[0]?.cameraAngle || 'front')
  const [playing, setPlaying] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [selectedClips, setSelectedClips] = useState(new Set())
  const [selectMode, setSelectMode] = useState(false)
  const currentIdx = allEvents.findIndex(e => e.eventKey === event.eventKey)
  const hasPrev = currentIdx > 0
  const hasNext = currentIdx < allEvents.length - 1
  const color = EVENT_COLORS[event.eventType] || EVENT_COLORS.unknown

  const handleDeleteSelected = async () => {
    if (selectedClips.size === 0) return
    if (!confirm(`Delete ${selectedClips.size} clip(s)?`)) return
    setDeleting(true)
    try {
      for (const clipId of selectedClips) {
        await deleteClip(clipId)
      }
      onDeleted()
    } catch (e) {
      alert('Delete failed: ' + e.message)
    } finally {
      setDeleting(false)
    }
  }

  const handleDeleteEvent = async () => {
    if (!confirm(`Delete all ${clips.length} clip(s) in this event?`)) return
    setDeleting(true)
    try {
      await deleteEvent(event.eventKey)
      onDeleted()
    } catch (e) {
      alert('Delete failed: ' + e.message)
    } finally {
      setDeleting(false)
    }
  }

  const clips = event.clips || []
  const sortedClips = [...clips].sort((a, b) => {
    const order = ['front', 'back', 'left', 'right', 'cabin']
    return order.indexOf(a.cameraAngle) - order.indexOf(b.cameraAngle)
  })
  const activeClip = clips.find(c => c.cameraAngle === activeCamera) || clips[0]

  useEffect(() => {
    setPlaying(false)
    if (!multiCam) {
      const v = videosRef.current[activeClip?.id]
      if (v) {
        v.currentTime = 0
        v.load()
      }
    }
  }, [event, activeCamera, multiCam])

  function syncAll(time) {
    for (const ref of Object.values(videosRef.current)) {
      if (ref && Math.abs(ref.currentTime - time) > 0.5) {
        ref.currentTime = time
      }
    }
  }

  function handleMainPlay() {
    if (multiCam) {
      const first = Object.values(videosRef.current)[0]
      if (first) {
        first.play()
        setPlaying(true)
      }
    } else {
      const v = videosRef.current[activeClip?.id]
      if (v) { v.play(); setPlaying(true) }
    }
  }

  function handleMainPause() {
    if (multiCam) {
      for (const v of Object.values(videosRef.current)) {
        if (v) v.pause()
      }
    } else {
      const v = videosRef.current[activeClip?.id]
      if (v) v.pause()
    }
    setPlaying(false)
  }

  function handleTimeUpdate() {
    if (multiCam) {
      const first = Object.values(videosRef.current)[0]
      if (first) syncAll(first.currentTime)
    }
  }

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-content event-detail">
        <div className="modal-header">
          <div className="modal-title">
            <span className="modal-event" style={{ background: color }}>{EVENT_LABELS[event.eventType]}</span>
            <span className="modal-filename">{formatDate(event.timestamp)} {formatTime(event.timestamp)}</span>
          </div>
          <div className="modal-nav">
            <button className="btn btn-nav" onClick={() => onNavigate(allEvents[currentIdx - 1])} disabled={!hasPrev}>◀</button>
            <button className="btn btn-nav" onClick={() => onNavigate(allEvents[currentIdx + 1])} disabled={!hasNext}>▶</button>
            <button className="btn btn-rescan" onClick={() => setMultiCam(m => !m)} title={multiCam ? 'Single cam view' : 'Multi-cam view'}>
              {multiCam ? '📺 Single' : '🎬 Multi'}
            </button>
            {selectMode ? (
              <>
                <button
                  className="btn btn-danger"
                  onClick={handleDeleteSelected}
                  disabled={deleting || selectedClips.size === 0}
                  title="Delete selected clips"
                >
                  {deleting ? '⏳' : '🗑️'} {selectedClips.size > 0 ? `Delete (${selectedClips.size})` : 'Select clips'}
                </button>
                <button className="btn btn-clear" onClick={() => { setSelectMode(false); setSelectedClips(new Set()) }}>Cancel</button>
              </>
            ) : (
              <>
                <button className="btn btn-share-action" onClick={() => setSelectMode(true)} title="Select clips to delete">
                  ☑️ Select
                </button>
                <button
                  className="btn btn-danger"
                  onClick={handleDeleteEvent}
                  disabled={deleting}
                  title="Delete all clips in this event"
                >
                  {deleting ? '⏳' : '🗑️'} Delete All
                </button>
              </>
            )}
            <button className="btn btn-nav btn-close" onClick={onClose}>✕</button>
          </div>
        </div>

        {multiCam ? (
          <div className="multicam-grid">
            {sortedClips.map(clip => (
              <div key={clip.id} className="multicam-pane">
                <video
                  ref={el => { if (el) videosRef.current[clip.id] = el }}
                  src={clip.downloadUrl}
                  controls={false}
                  muted
                  onTimeUpdate={handleTimeUpdate}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                  onClick={e => {
                    if (e.target.paused) e.target.play()
                    else e.target.pause()
                  }}
                />
                <div className="multicam-label">{CAMERA_LABELS[clip.cameraAngle] || clip.cameraAngle}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="modal-video-wrap">
            <video
              ref={el => { if (el && activeClip) videosRef.current[activeClip.id] = el }}
              key={activeClip?.id}
              src={activeClip?.downloadUrl}
              controls
              autoPlay
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onClick={() => playing ? handleMainPause() : handleMainPlay()}
            />
          </div>
        )}

        <div className="modal-meta">
          <div className="camera-tabs">
            {sortedClips.map(clip => (
              <button
                key={clip.id}
                className={`camera-tab ${activeCamera === clip.cameraAngle && !selectMode ? 'active' : ''} ${selectMode && selectedClips.has(clip.id) ? 'selected' : ''}`}
                onClick={() => {
                  if (selectMode) {
                    setSelectedClips(prev => {
                      const next = new Set(prev)
                      next.has(clip.id) ? next.delete(clip.id) : next.add(clip.id)
                      return next
                    })
                  } else {
                    setActiveCamera(clip.cameraAngle)
                  }
                }}
              >
                {selectMode && selectedClips.has(clip.id) ? '✓ ' : ''}{CAMERA_LABELS[clip.cameraAngle] || clip.cameraAngle}
              </button>
            ))}
          </div>
          <div className="modal-meta-item"><span className="meta-label">Time</span><span className="meta-value">{formatDate(event.timestamp)} {formatTime(event.timestamp)}</span></div>
          <div className="modal-meta-item"><span className="meta-label">Cameras</span><span className="meta-value">{event.cameraCount}</span></div>
          <div className="modal-meta-item"><span className="meta-label">Folder</span><span className="meta-value">{event.folder}</span></div>
          <div className="modal-meta-item"><span className="meta-label">Size</span><span className="meta-value">{event.sizeString}</span></div>
        </div>
      </div>
    </div>
  )
}

function SharesPanel({ onClose, onSaved }) {
  const [shares, setShares] = useState([])
  const [loading, setLoading] = useState(true)
  const [editingShare, setEditingShare] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', protocol: 'smb', host: '', port: '', path: '', username: '', password: '', enabled: true })
  const [testing, setTesting] = useState(null)
  const [testResult, setTestResult] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('shares')

  const loadShares = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getShares()
      setShares(Array.isArray(data) ? data : [])
    } catch (e) {
      console.error('Failed to load shares:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadShares() }, [loadShares])

  const resetForm = () => {
    setForm({ name: '', protocol: 'smb', host: '', port: '', path: '', username: '', password: '', enabled: true })
    setEditingShare(null)
    setShowForm(false)
    setTestResult(null)
    setError('')
  }

  const handleEdit = (share) => {
    setEditingShare(share.id)
    setForm({
      name: share.name,
      protocol: share.protocol,
      host: share.host,
      port: share.port || '',
      path: share.path,
      username: share.username || '',
      password: share.password || '',
      enabled: share.enabled,
    })
    setShowForm(true)
    setTestResult(null)
    setError('')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      const payload = {
        name: form.name,
        protocol: form.protocol,
        host: form.host,
        path: form.path,
        port: form.port ? parseInt(form.port) : undefined,
        username: form.username || undefined,
        password: form.password || undefined,
        enabled: form.enabled,
      }
      if (editingShare) {
        await updateShare(editingShare, payload)
      } else {
        await createShare(payload)
      }
      resetForm()
      loadShares()
      onSaved()
    } catch (err) {
      setError(err.message || 'Failed to save share')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (shareId) => {
    if (!confirm('Delete this share?')) return
    try {
      await deleteShare(shareId)
      loadShares()
      onSaved()
    } catch (err) {
      setError(err.message || 'Failed to delete share')
    }
  }

  const handleToggle = async (share) => {
    try {
      await updateShare(share.id, { ...share, enabled: !share.enabled })
      loadShares()
      onSaved()
    } catch (err) {
      setError(err.message || 'Failed to toggle share')
    }
  }

  const handleTest = async (shareId) => {
    setTesting(shareId)
    setTestResult(null)
    try {
      const result = await testShare(shareId)
      setTestResult({ id: shareId, ...result })
    } catch (err) {
      setTestResult({ id: shareId, success: false, message: err.message })
    } finally {
      setTesting(null)
    }
  }

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-content shares-modal">
        <div className="modal-header">
          <div className="modal-title">
            <span>📁</span>
            <span>Settings</span>
          </div>
          <div className="modal-nav">
            <button className="btn btn-nav btn-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="settings-tabs">
          <button className={`settings-tab ${activeTab === 'shares' ? 'active' : ''}`} onClick={() => setActiveTab('shares')}>Shares</button>
          <button className={`settings-tab ${activeTab === 'cloud' ? 'active' : ''}`} onClick={() => setActiveTab('cloud')}>Cloud</button>
        </div>

        {activeTab === 'shares' && (
          <SharesTab
            shares={shares} loading={loading} showForm={showForm} editingShare={editingShare} form={form}
            testing={testing} testResult={testResult} saving={saving} error={error}
            onEdit={handleEdit} onDelete={handleDelete} onToggle={handleToggle} onTest={handleTest}
            onSubmit={handleSubmit} onReset={resetForm} onShowForm={setShowForm} onFormChange={setForm}
          />
        )}

        {activeTab === 'cloud' && (
          <CloudPanel onClose={onClose} onSaved={onSaved} />
        )}
      </div>
    </div>
  )
}

function SharesTab({ shares, loading, showForm, editingShare, form, testing, testResult, saving, error, onEdit, onDelete, onToggle, onTest, onSubmit, onReset, onShowForm, onFormChange }) {
  return (
    <div className="shares-body">
      {error && <div className="shares-error">{error} <button onClick={() => {}}>✕</button></div>}

      {!showForm && (
        <div className="shares-toolbar">
          <button className="btn btn-primary" onClick={() => { onShowForm(true); onFormChange({ name: '', protocol: 'smb', host: '', port: '', path: '', username: '', password: '', enabled: true }) }}>
            + Add Share
          </button>
        </div>
      )}

      {showForm && (
        <form className="share-form" onSubmit={onSubmit}>
          <h3>{editingShare ? 'Edit Share' : 'New Share'}</h3>
          <div className="form-row">
            <label>
              Name
              <input type="text" value={form.name} onChange={e => onFormChange(f => ({ ...f, name: e.target.value }))} placeholder="My NAS" required />
            </label>
            <label>
              Protocol
              <select value={form.protocol} onChange={e => onFormChange(f => ({ ...f, protocol: e.target.value }))}>
                <option value="smb">SMB</option>
                <option value="ftp">FTP</option>
                <option value="nfs">NFS</option>
              </select>
            </label>
          </div>
          <div className="form-row">
            <label>
              Host
              <input type="text" value={form.host} onChange={e => onFormChange(f => ({ ...f, host: e.target.value }))} placeholder="192.168.1.100" required />
            </label>
            <label>
              Port
              <input type="number" value={form.port} onChange={e => onFormChange(f => ({ ...f, port: e.target.value }))} placeholder={form.protocol === 'smb' ? '445' : form.protocol === 'ftp' ? '21' : '2049'} />
            </label>
          </div>
          <label className="form-full">
            Path
            <input type="text" value={form.path} onChange={e => onFormChange(f => ({ ...f, path: e.target.value }))} placeholder="/dashcam or /TeslaCam" required />
          </label>
          <div className="form-row">
            <label>
              Username
              <input type="text" value={form.username} onChange={e => onFormChange(f => ({ ...f, username: e.target.value }))} placeholder="guest" />
            </label>
            <label>
              Password
              <input type="password" value={form.password} onChange={e => onFormChange(f => ({ ...f, password: e.target.value }))} placeholder="••••••" />
            </label>
          </div>
          <label className="form-check">
            <input type="checkbox" checked={form.enabled} onChange={e => onFormChange(f => ({ ...f, enabled: e.target.checked }))} />
            Enabled
          </label>
          <div className="form-actions">
            <button type="button" className="btn btn-clear" onClick={onReset}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="shares-loading">Loading shares…</div>
      ) : (
        <div className="shares-list">
          {shares.length === 0 && !showForm && (
            <div className="shares-empty">No shares configured. Add one to scan remote footage.</div>
          )}
          {shares.map(share => (
            <div key={share.id} className={`share-item ${!share.enabled ? 'disabled' : ''}`}>
              <div className="share-info">
                <div className="share-name">
                  <span className={`protocol-badge ${share.protocol}`}>{PROTOCOL_LABELS[share.protocol] || share.protocol}</span>
                  {share.name}
                </div>
                <div className="share-host">{share.host}:{share.port || (share.protocol === 'smb' ? 445 : share.protocol === 'ftp' ? 21 : 2049)}{share.path}</div>
              </div>
              <div className="share-actions">
                <button
                  className={`btn btn-toggle ${share.enabled ? 'on' : ''}`}
                  onClick={() => onToggle(share)}
                  title={share.enabled ? 'Disable' : 'Enable'}
                >
                  {share.enabled ? '●' : '○'}
                </button>
                <button
                  className="btn btn-share-action"
                  onClick={() => onTest(share.id)}
                  disabled={testing === share.id}
                  title="Test connection"
                >
                  {testing === share.id ? '⏳' : '🔗'}
                </button>
                <button className="btn btn-share-action" onClick={() => onEdit(share)} title="Edit">✏️</button>
                <button className="btn btn-share-action btn-danger" onClick={() => onDelete(share.id)} title="Delete">🗑️</button>
              </div>
              {testResult && testResult.id === share.id && (
                <div className={`test-result ${testResult.success ? 'success' : 'fail'}`}>
                  {testResult.success ? '✅' : '❌'} {testResult.message}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const CLOUD_PROVIDERS = [
  { value: 'icloud', label: 'iCloud', icon: '☁️' },
  { value: 'gdrive', label: 'Google Drive', icon: '📹' },
  { value: 'onedrive', label: 'OneDrive', icon: '☁️' },
  { value: 'dropbox', label: 'Dropbox', icon: '📦' },
]

function CloudPanel({ onClose, onSaved }) {
  const [providers, setProviders] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [form, setForm] = useState({ name: '', provider: 'icloud', folder_path: '/Drivecam', credentials: {} })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(null)
  const [syncing, setSyncing] = useState(null)
  const [testResults, setTestResults] = useState({})
  const [syncResults, setSyncResults] = useState({})

  const loadProviders = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getCloudProviders()
      setProviders(Array.isArray(data) ? data : [])
    } catch (e) {
      console.error('Failed to load cloud providers:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadProviders() }, [loadProviders])

  const resetForm = () => {
    setForm({ name: '', provider: 'icloud', folder_path: '/Drivecam', credentials: {} })
    setEditingId(null)
    setShowForm(false)
    setError('')
  }

  const handleEdit = (p) => {
    setEditingId(p.id)
    setForm({ name: p.name, provider: p.provider, folder_path: p.folder_path, credentials: p.credentials || {} })
    setShowForm(true)
    setError('')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      const payload = { name: form.name, provider: form.provider, folder_path: form.folder_path, credentials: form.credentials }
      if (editingId) {
        await updateCloudProvider(editingId, payload)
      } else {
        await createCloudProvider(payload)
      }
      resetForm()
      loadProviders()
      onSaved()
    } catch (err) {
      setError(err.message || 'Failed to save provider')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id) => {
    if (!confirm('Delete this cloud provider?')) return
    try {
      await deleteCloudProvider(id)
      loadProviders()
    } catch (err) {
      setError(err.message || 'Failed to delete provider')
    }
  }

  const handleTest = async (id) => {
    setTesting(id)
    setTestResults(r => { const n = {...r}; delete n[id]; return n })
    try {
      const result = await testCloudProvider(id)
      setTestResults(r => ({ ...r, [id]: result }))
    } catch (err) {
      setTestResults(r => ({ ...r, [id]: { success: false, message: err.message } }))
    } finally {
      setTesting(null)
    }
  }

  const handleSync = async (id) => {
    setSyncing(id)
    try {
      const result = await syncCloudProvider(id)
      setSyncResults(r => ({ ...r, [id]: result }))
      loadProviders()
      onSaved()
    } catch (err) {
      setSyncResults(r => ({ ...r, [id]: { status: 'error', message: err.message } }))
    } finally {
      setSyncing(null)
    }
  }

  return (
    <div className="shares-body">
      {error && <div className="shares-error">{error} <button onClick={() => setError('')}>✕</button></div>}

      {!showForm && (
        <div className="shares-toolbar">
          <button className="btn btn-primary" onClick={() => { setShowForm(true); setEditingId(null); setForm({ name: '', provider: 'icloud', folder_path: '/Drivecam', credentials: {} }) }}>
            + Add Cloud Provider
          </button>
        </div>
      )}

      {showForm && (
        <form className="share-form cloud-form" onSubmit={handleSubmit}>
          <h3>{editingId ? 'Edit Provider' : 'New Cloud Provider'}</h3>
          <div className="form-row">
            <label>
              Name
              <input type="text" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="My iCloud Drive" required />
            </label>
            <label>
              Provider
              <select value={form.provider} onChange={e => setForm(f => ({ ...f, provider: e.target.value, credentials: {} }))}>
                {CLOUD_PROVIDERS.map(p => <option key={p.value} value={p.value}>{p.icon} {p.label}</option>)}
              </select>
            </label>
          </div>
          <label className="form-full">
            Folder Path
            <input type="text" value={form.folder_path} onChange={e => setForm(f => ({ ...f, folder_path: e.target.value }))} placeholder="/Drivecam or /Media" required />
          </label>

          {form.provider === 'icloud' && (
            <div className="form-row">
              <label>
                Apple ID
                <input type="email" value={form.credentials.username || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, username: e.target.value } }))} placeholder="me@icloud.com" />
              </label>
              <label>
                Password
                <input type="password" value={form.credentials.password || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, password: e.target.value } }))} placeholder="••••••" />
              </label>
            </div>
          )}

          {form.provider === 'gdrive' && (
            <div className="form-row">
              <label>
                Client ID
                <input type="text" value={form.credentials.client_id || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, client_id: e.target.value } }))} placeholder="apps.googleusercontent.com" />
              </label>
              <label>
                Client Secret
                <input type="password" value={form.credentials.client_secret || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, client_secret: e.target.value } }))} placeholder="••••••" />
              </label>
            </div>
          )}

          {form.provider === 'onedrive' && (
            <>
              <div className="form-row">
                <label>
                  Client ID
                  <input type="text" value={form.credentials.client_id || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, client_id: e.target.value } }))} placeholder="Application ID" />
                </label>
                <label>
                  Client Secret
                  <input type="password" value={form.credentials.client_secret || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, client_secret: e.target.value } }))} placeholder="••••••" />
                </label>
              </div>
              <div className="form-row">
                <label>
                  Tenant ID
                  <input type="text" value={form.credentials.tenant_id || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, tenant_id: e.target.value } }))} placeholder="common" />
                </label>
                <label>
                  Access Token
                  <input type="password" value={form.credentials.access_token || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, access_token: e.target.value } }))} placeholder="••••••" />
                </label>
              </div>
              <label className="form-full">
                Refresh Token
                <input type="password" value={form.credentials.refresh_token || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, refresh_token: e.target.value } }))} placeholder="••••••" />
              </label>
            </>
          )}

          {form.provider === 'dropbox' && (
            <div className="form-row">
              <label>
                App Key
                <input type="text" value={form.credentials.app_key || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, app_key: e.target.value } }))} placeholder="xxxxxxxxxxxxxx" />
              </label>
              <label>
                App Secret
                <input type="password" value={form.credentials.app_secret || ''} onChange={e => setForm(f => ({ ...f, credentials: { ...f.credentials, app_secret: e.target.value } }))} placeholder="••••••" />
              </label>
            </div>
          )}

          <div className="form-actions">
            <button type="button" className="btn btn-clear" onClick={resetForm}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="shares-loading">Loading cloud providers…</div>
      ) : (
        <div className="shares-list">
          {providers.length === 0 && !showForm && (
            <div className="shares-empty">No cloud providers configured. Add one to sync footage from the cloud.</div>
          )}
          {providers.map(p => {
            const meta = CLOUD_PROVIDERS.find(c => c.value === p.provider) || {}
            return (
              <div key={p.id} className={`share-item ${!p.enabled ? 'disabled' : ''}`}>
                <div className="share-info">
                  <div className="share-name">
                    <span className={`protocol-badge ${p.provider}`}>{meta.icon} {meta.label}</span>
                    {p.name}
                  </div>
                  <div className="share-host">{p.folder_path} &bull; {p.clip_count} clips{p.last_sync ? ` &bull; synced ${new Date(p.last_sync).toLocaleDateString()}` : ''}</div>
                </div>
                <div className="share-actions">
                  <button className="btn btn-share-action" onClick={() => handleSync(p.id)} disabled={syncing === p.id} title="Sync clips">
                    {syncing === p.id ? '⏳' : '🔄'}
                  </button>
                  <button className="btn btn-share-action" onClick={() => handleTest(p.id)} disabled={testing === p.id} title="Test connection">
                    {testing === p.id ? '⏳' : '🔗'}
                  </button>
                  <button className="btn btn-share-action" onClick={() => handleEdit(p)} title="Edit">✏️</button>
                  <button className="btn btn-share-action btn-danger" onClick={() => handleDelete(p.id)} title="Delete">🗑️</button>
                </div>
                {testResults[p.id] && (
                  <div className={`test-result ${testResults[p.id].success ? 'success' : 'fail'}`}>
                    {testResults[p.id].success ? '✅' : '❌'} {testResults[p.id].message}
                  </div>
                )}
                {syncResults[p.id] && (
                  <div className={`test-result ${syncResults[p.id].status === 'ok' ? 'success' : 'fail'}`}>
                    {syncResults[p.id].status === 'ok' ? '✅' : '❌'} Found {syncResults[p.id].clips_found} clips
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}