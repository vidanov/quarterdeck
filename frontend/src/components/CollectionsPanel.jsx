/**
 * CollectionsPanel — unified view for collections, archive, and projects.
 *
 * Replaces the inline collections UI in App.jsx. Exposes:
 *  - Collections tab: create, rename, delete, add/remove members, reorder, start
 *  - Archive tab: favourites + all sessions (legacy, kept for search)
 *  - Projects tab: project grouping (slow scan, cached)
 */
import React, { useState, useEffect, useCallback, useRef } from 'react'
import * as collectionsApi from '../api/collections'
import { useToast } from '../state/ToastContext'
import { useConfirm } from '../state/ConfirmContext'
import { timeAgo, showPath } from '../utils'

// ---------------------------------------------------------------------------
// Availability badge
// ---------------------------------------------------------------------------

const AVAIL_LABELS = {
  active:  { icon: '🟢', label: 'active' },
  done:    { icon: '⚪', label: 'done' },
  missing: { icon: '❌', label: 'missing' },
  recipe:  { icon: '📋', label: 'recipe' },
}

function AvailBadge({ availability }) {
  const { icon, label } = AVAIL_LABELS[availability] || { icon: '?', label: availability }
  return <span className="coll-avail-badge" title={label}>{icon}</span>
}

// ---------------------------------------------------------------------------
// Add-to-collection button — self-contained, fetches collections on first open
// ---------------------------------------------------------------------------

function AddToCollectionBtn({ session }) {
  const notify = useToast()
  const [open, setOpen] = useState(false)
  const [cols, setCols] = useState(null)  // null = not loaded yet
  const [adding, setAdding] = useState(false)
  const ref = useRef(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const handleOpen = (e) => {
    e.stopPropagation()
    if (!open && cols === null) {
      collectionsApi.listCollectionsEnriched()
        .then(d => setCols((d.collections || []).filter(c => c.source === 'manual')))
        .catch(() => setCols([]))
    }
    setOpen(v => !v)
  }

  const handleAdd = async (e, collId) => {
    e.stopPropagation()
    setAdding(true)
    try {
      await collectionsApi.addMember(collId, { session_id: session.id, cwd: session.cwd })
      notify('Added to collection')
      setOpen(false)
    } catch {
      notify('Failed to add to collection', 'error')
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="add-to-coll-wrap" ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button
        className="add-to-coll-btn"
        title="Add to collection"
        onClick={handleOpen}
      >📁+</button>
      {open && (
        <div className="add-to-coll-picker" onClick={e => e.stopPropagation()}>
          {cols === null && <div className="add-to-coll-loading">Loading…</div>}
          {cols !== null && cols.length === 0 && (
            <div className="add-to-coll-empty">No collections yet.<br/>Create one in the Collections tab.</div>
          )}
          {cols !== null && cols.map(c => (
            <button
              key={c.id}
              className="add-to-coll-item"
              disabled={adding}
              onClick={(e) => handleAdd(e, c.id)}
            >
              {c.name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Collections sub-tab
// ---------------------------------------------------------------------------

function CollectionMember({ member, onRemove }) {
  const avail = member.availability || 'done'
  const isMissing = avail === 'missing'
  return (
    <div className={`coll-member ${isMissing ? 'coll-member-missing' : ''}`}>
      <AvailBadge availability={avail} />
      <span className="coll-member-title">{member.title || member.cwd || member.session_id || 'Unnamed'}</span>
      {member.cwd && <span className="coll-member-cwd">{member.cwd_display || member.cwd}</span>}
      {isMissing && <span className="coll-member-missing-hint">session deleted</span>}
      <button className="coll-member-remove" onClick={() => onRemove(member.session_id)} title="Remove">×</button>
    </div>
  )
}

function CollectionCard({ collection, sessions, onRename, onDelete, onStart, onRemoveMember, expanded, onToggle }) {
  const memberCount = collection.members.length
  const isSnapshot = collection.source === 'snapshot'
  const isFavourites = collection.source === 'favourites'
  const sourceIcon = isSnapshot ? '⊙' : isFavourites ? '★' : '📁'

  return (
    <div className="coll-card">
      <div className="coll-card-header" onClick={onToggle}>
        <span className="coll-expand">{expanded ? '▼' : '▶'}</span>
        <span className="coll-source-icon" title={collection.source}>{sourceIcon}</span>
        <span className="coll-name">{collection.name}</span>
        <span className="coll-count">{memberCount} {memberCount === 1 ? 'session' : 'sessions'}</span>
        {collection.meta?.date && <span className="coll-meta-date">{collection.meta.date}</span>}
        <div className="coll-actions" onClick={e => e.stopPropagation()}>
          {memberCount > 0 && (
            <button className="coll-start-btn" onClick={onStart} title="Start all members">▶ Start</button>
          )}
          <button className="coll-rename-btn" onClick={onRename} title="Rename">✎</button>
          <button className="coll-delete-btn" onClick={onDelete} title="Delete collection">×</button>
        </div>
      </div>
      {expanded && (
        <div className="coll-members">
          {collection.members.length === 0 && (
            <div className="coll-empty-members">
              No members.
              {isFavourites
                ? ' Add sessions via the ★ button.'
                : ' Use the 📁+ button on sessions in the Projects or Archive tab.'}
            </div>
          )}
          {collection.members.map((m, i) => (
            <CollectionMember
              key={m.session_id || i}
              member={m}
              onRemove={(sid) => onRemoveMember(collection.id, sid)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function CollectionsSubTab({ sessions, onResumeSession }) {
  const notify = useToast()
  const askConfirm = useConfirm()
  const [collections, setCollections] = useState([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(new Set())
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    collectionsApi.listCollectionsEnriched()
      .then(d => setCollections(d.collections || []))
      .catch(() => notify('Failed to load collections', 'error'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const toggleExpand = (id) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleCreate = async () => {
    const name = newName.trim()
    if (!name) return
    try {
      const d = await collectionsApi.createCollection(name)
      setCollections(prev => [...prev, d.collection])
      setExpanded(prev => new Set([...prev, d.collection.id]))
      setNewName('')
      setCreating(false)
      notify(`Collection "${name}" created`)
    } catch {
      notify('Failed to create collection', 'error')
    }
  }

  const handleRename = async (collection) => {
    const name = window.prompt('New name:', collection.name)
    if (!name || name === collection.name) return
    const d = await collectionsApi.renameCollection(collection.id, name)
    if (d.ok) {
      setCollections(prev => prev.map(c => c.id === collection.id ? d.collection : c))
    }
  }

  const handleDelete = async (collection) => {
    const ok = await askConfirm(`Delete collection "${collection.name}"? Sessions are not deleted.`)
    if (!ok) return
    await collectionsApi.deleteCollection(collection.id)
    setCollections(prev => prev.filter(c => c.id !== collection.id))
    notify(`Collection "${collection.name}" deleted`)
  }

  const handleStart = async (collection) => {
    try {
      const d = await collectionsApi.startCollection(collection.id)
      const n = d.spawned?.length || 0
      notify(`Started ${n} session${n === 1 ? '' : 's'}`)
    } catch {
      notify('Failed to start collection', 'error')
    }
  }

  const handleRemoveMember = async (collectionId, sessionId) => {
    if (!sessionId) return
    const d = await collectionsApi.removeCollectionMember(collectionId, sessionId)
    if (d.ok) {
      // Re-fetch enriched version of this collection
      collectionsApi.getCollectionEnriched(collectionId)
        .then(r => {
          if (r.collection) {
            setCollections(prev => prev.map(c => c.id === collectionId ? r.collection : c))
          }
        })
        .catch(() => load())
    }
  }

  if (loading) return <div className="loading">Loading collections…</div>

  return (
    <div className="collections-sub">
      <div className="coll-toolbar">
        {creating ? (
          <div className="coll-create-row">
            <input
              className="coll-name-input"
              autoFocus
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') setCreating(false) }}
              placeholder="Collection name…"
            />
            <button className="coll-create-confirm" onClick={handleCreate}>Create</button>
            <button className="coll-create-cancel" onClick={() => setCreating(false)}>Cancel</button>
          </div>
        ) : (
          <button className="coll-new-btn" onClick={() => setCreating(true)}>+ New collection</button>
        )}
      </div>
      {collections.length === 0 && !creating && (
        <div className="empty">
          No collections yet. Create one with <strong>+ New collection</strong>,
          or favourite sessions to auto-populate Favourites.
        </div>
      )}
      {collections.map(c => (
        <CollectionCard
          key={c.id}
          collection={c}
          sessions={sessions}
          expanded={expanded.has(c.id)}
          onToggle={() => toggleExpand(c.id)}
          onRename={() => handleRename(c)}
          onDelete={() => handleDelete(c)}
          onStart={() => handleStart(c)}
          onRemoveMember={handleRemoveMember}
        />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main CollectionsPanel
// ---------------------------------------------------------------------------

export default function CollectionsPanel({
  // Archive props (passed from App)
  archiveQuery,
  archiveResults,
  archiveTotal,
  archiveSelected,
  onArchiveSearch,
  onToggleArchiveSelect,
  onBatchDelete,
  onDeleteArchive,
  onRenameArchive,
  // Favourites props
  favourites,
  onToggleFavourite,
  onLaunchFavourite,
  // Sessions (for collections tab)
  sessions,
  // Snapshot props (legacy)
  snapshots,
  onRestoreAll,
  onDeleteSnapshot,
  restoring,
  // Projects props
  projectsData,
  projectsLoading,
  projectsError,
  onLoadProjects,
  expandedProjects,
  onToggleProject,
  onDeleteProject,
  // Selected session
  selected,
  onSelectSession,
  // Source sub-tab state
  source,
  onChangeSource,
}) {
  const sources = [
    ['collections', `Collections`],
    ['archive', `Archive (${archiveTotal})`],
    ['snapshots', `Snapshots (${snapshots.length})`],
    ['projects', 'Projects'],
  ]

  return (
    <div className="collections-panel">
      <div className="collection-source-bar">
        {sources.map(([key, label]) => (
          <button
            key={key}
            className={`control-filter-btn ${source === key ? 'active' : ''}`}
            onClick={() => {
              onChangeSource(key)
              if (key === 'projects' && !projectsData) onLoadProjects()
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {source === 'collections' && (
        <CollectionsSubTab sessions={sessions} onResumeSession={onLaunchFavourite} />
      )}

      {source === 'snapshots' && (
        <>
          {snapshots.length > 0 ? (
            <div className="snapshot-list">
              {snapshots.map(snap => (
                <div key={snap.id} className="snapshot-entry">
                  <div className="snapshot-header">
                    <span className="snapshot-time">{snap.date} {snap.time}</span>
                    <span className="snapshot-count">{snap.sessions.length} sessions</span>
                    <div className="snapshot-actions">
                      <button className="restore-all-btn" onClick={() => onRestoreAll(snap)} disabled={restoring}>
                        {restoring ? '⟳' : '▶ Restore'}
                      </button>
                      <button className="snapshot-delete" onClick={() => onDeleteSnapshot(snap.id)}>×</button>
                    </div>
                  </div>
                  <div className="snapshot-sessions">
                    {snap.sessions.map(s => (
                      <div key={s.id} className="snapshot-session-row">
                        <span className="snapshot-session-name">{s.name}</span>
                        <span className="snapshot-session-title">{s.title}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty">No snapshots yet. Press ⊙ Snapshot to capture.</div>
          )}
        </>
      )}

      {source === 'archive' && (
        <div className="archive-panel">
          {favourites.length > 0 && (
            <div className="archive-section">
              <h3 className="archive-title">★ Favourites
                {(() => {
                  const stale = favourites.filter(f =>
                    f.cwd && (f.cwd.startsWith('/private/var/folders') || f.cwd.startsWith('/tmp'))
                  )
                  if (!stale.length) return null
                  return (
                    <button className="stats-delete-all small" style={{marginLeft:8}}
                            title={`${stale.length} favourite(s) point to temp paths that no longer exist`}
                            onClick={() => collectionsApi.purgeStalesFavourites()
                              .then(() => stale.forEach(f => onToggleFavourite(f)))}>
                      Remove {stale.length} stale
                    </button>
                  )
                })()}
              </h3>
              <div className="archive-list">
                {favourites.map(f => (
                  <div
                    key={f.id}
                    className="archive-row clickable"
                    onClick={() => onSelectSession({ id: f.id, title: f.title, cwd: f.cwd, cwd_display: f.cwd_display, status: 'done' })}
                  >
                    <button className="archive-fav active" onClick={e => { e.stopPropagation(); onToggleFavourite(f) }} title="Remove from favourites">★</button>
                    <div className="archive-info">
                      <span className="archive-row-title">{f.title}</span>
                      <span className="archive-row-cwd" title={f.cwd}>{showPath(f)}</span>
                    </div>
                    <button className="archive-launch" onClick={e => { e.stopPropagation(); onLaunchFavourite(f) }} title="Resume in new tab">▶</button>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="archive-section">
            <h3 className="archive-title">
              All Sessions ({archiveTotal})
              {archiveSelected.size > 0 && (
                <button className="stats-delete-all" onClick={onBatchDelete}>
                  Delete {archiveSelected.size} selected
                </button>
              )}
            </h3>
            <input
              className="archive-search"
              type="text"
              placeholder="Search sessions by title or path..."
              value={archiveQuery}
              onChange={e => onArchiveSearch(e.target.value)}
              onFocus={() => { if (!archiveResults.length) onArchiveSearch('') }}
            />
            <div className="archive-list">
              {archiveResults.map(s => (
                <div
                  key={s.id}
                  className={`archive-row clickable ${selected?.id === s.id ? 'selected' : ''} ${archiveSelected.has(s.id) ? 'checked' : ''}`}
                  onClick={() => onSelectSession({ id: s.id, title: s.title, cwd: s.cwd, cwd_display: s.cwd_display, status: 'done' })}
                >
                  <input type="checkbox" className="archive-check" checked={archiveSelected.has(s.id)} onChange={() => onToggleArchiveSelect(s.id)} onClick={e => e.stopPropagation()} />
                  <button className={`archive-fav ${s.is_favourite ? 'active' : ''}`} onClick={e => { e.stopPropagation(); onToggleFavourite(s) }} title={s.is_favourite ? 'Remove favourite' : 'Add favourite'}>
                    {s.is_favourite ? '★' : '☆'}
                  </button>
                  <div className="archive-info">
                    <span className="archive-row-title">{s.title}</span>
                    <span className="archive-row-cwd" title={s.cwd}>{showPath(s)}</span>
                  </div>
                  <span className="archive-row-date">
                    {s.updated_at ? new Date(s.updated_at).toLocaleDateString([], { month: 'short', day: 'numeric' }) : ''}
                  </span>
                  <button className="archive-rename" onClick={e => { e.stopPropagation(); onRenameArchive(s) }} title="Rename">✎</button>
                  <AddToCollectionBtn session={s} />
                  <button className="archive-launch" onClick={e => { e.stopPropagation(); onLaunchFavourite(s) }} title="Resume in new tab">▶</button>
                  <button className="archive-delete" onClick={e => { e.stopPropagation(); onDeleteArchive(s.id) }} title="Delete session">×</button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {source === 'projects' && (
        <div className="projects-panel">
          <div className="projects-toolbar">
            <button className="scan-btn" onClick={() => onLoadProjects(true)} disabled={projectsLoading}>
              {projectsLoading ? '⟳ Scanning…' : '⟳ Refresh'}
            </button>
            {projectsData && !projectsLoading && (
              <span className="projects-age">
                {projectsData.cached
                  ? `cached ${Math.round(projectsData.age_seconds)}s ago`
                  : 'just scanned'}
              </span>
            )}
          </div>
          {projectsLoading && !projectsData && <div className="loading">Scanning sessions… this takes a moment.</div>}
          {projectsError && <div className="error-banner">Error: {projectsError} <button onClick={() => onLoadProjects(true)}>Retry</button></div>}
          {!projectsLoading && !projectsData && !projectsError && (
            <div className="loading">No data. <button onClick={() => onLoadProjects()}>Load Projects</button></div>
          )}
          {projectsData && (
            <>
              {projectsData.hot.length > 0 && (
                <div className="projects-section">
                  <h3 className="projects-title">🔥 Hot Projects</h3>
                  <div className="hot-projects">
                    {projectsData.hot.map(p => (
                      <div key={p.name} className="hot-project-card" onClick={() => onToggleProject(p.name)}>
                        <button className="project-delete-btn" onClick={e => onDeleteProject(p.cwd, p.name, p.session_count, e)} title="Delete all sessions">×</button>
                        <div className="hot-project-name">{p.name}</div>
                        <div className="hot-project-stats">
                          <span>{p.session_count} sessions</span>
                          <span>{p.total_turns} turns</span>
                          {p.active_count > 0 && <span className="hot-active">{p.active_count} active</span>}
                        </div>
                        <div className="hot-project-time">{p.last_activity ? timeAgo(p.last_activity) : ''}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {projectsData.abandoned.length > 0 && (
                <div className="projects-section">
                  <h3 className="projects-title">💤 Abandoned Threads <span className="projects-count">({projectsData.abandoned.length})</span></h3>
                  <div className="abandoned-list">
                    {projectsData.abandoned.slice(0, 10).map(a => (
                      <div key={a.id} className="abandoned-row" onClick={() => onSelectSession({ id: a.id, title: a.title, cwd: a.cwd, cwd_display: a.cwd_display, status: 'done' })}>
                        <span className="abandoned-days">{a.days_inactive}d</span>
                        <span className="abandoned-project">{a.project}</span>
                        <span className="abandoned-title">{a.title}</span>
                        <span className="abandoned-turns">{a.turns} turns</span>
                        <button className="archive-launch" onClick={e => { e.stopPropagation(); onLaunchFavourite(a) }} title="Resume">▶</button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="projects-section">
                <h3 className="projects-title">
                  📁 All Projects <span className="projects-count">({projectsData.projects.length})</span>
                  <button className="projects-refresh" onClick={onLoadProjects} disabled={projectsLoading}>↻ Refresh</button>
                </h3>
                <div className="projects-list">
                  {projectsData.projects.map(p => (
                    <div key={p.name} className="project-group">
                      <div className={`project-header ${expandedProjects.has(p.name) ? 'expanded' : ''}`} onClick={() => onToggleProject(p.name)}>
                        <span className="project-expand">{expandedProjects.has(p.name) ? '▼' : '▶'}</span>
                        <span className="project-name">{p.name}</span>
                        <span className="project-stats">
                          {p.active_count > 0 && <span className="project-active">{p.active_count} active</span>}
                          <span>{p.session_count} sessions</span>
                          <span>{p.total_messages} msgs</span>
                        </span>
                        <span className="project-time">{p.last_activity ? timeAgo(p.last_activity) : ''}</span>
                      </div>
                      {expandedProjects.has(p.name) && (
                        <div className="project-sessions">
                          {p.sessions.map(s => (
                            <div
                              key={s.id}
                              className={`project-session ${s.is_active ? 'active' : ''}`}
                              onClick={() => onSelectSession({ id: s.id, title: s.title, cwd: s.cwd, cwd_display: s.cwd_display, status: s.status })}
                            >
                              <span className={`session-status-dot ${s.status}`}></span>
                              <span className="session-title">{s.title}</span>
                              <span className="session-meta">{s.turns} turns</span>
                              <span className="session-time">{s.updated_at ? timeAgo(s.updated_at) : ''}</span>
                              <AddToCollectionBtn session={s} />
                              <button className="archive-launch" onClick={e => { e.stopPropagation(); onLaunchFavourite(s) }} title="Resume">▶</button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
