import React from 'react'

// Extensions worth linking — common output files from kiro-cli sessions
const FILE_EXTS = /\.(md|txt|json|yaml|yml|py|js|jsx|ts|tsx|sh|toml|csv|html|xml|log|pdf|docx|xlsx)$/i

// Paths that look like URLs/routes, not filesystem paths — never linkify these
// as file chips even though they start with /
const API_PATH_RE = /^\/(api|app|auth|static|assets|public|login|logout|health)\//i

// A path segment that suggests a real filesystem location
const FS_SEGMENT_RE = /^(Users|home|var|etc|tmp|usr|opt|Applications|Library|Documents|Desktop|Downloads|Projects|src|backend|frontend|build|dist)\b/i

function isFilesystemPath(path) {
  if (!path) return false
  // Exclude URL-like patterns (even without scheme) and API routes
  if (API_PATH_RE.test(path)) return false
  if (/^\/\w+:/.test(path)) return false // Windows-style /C:/...
  const depth = (path.match(/\//g) || []).length
  // Needs a known file extension OR enough depth + a filesystem-looking segment
  if (FILE_EXTS.test(path)) return true
  if (depth < 2) return false
  // Must have a segment that looks like a real fs location
  const segments = path.split('/').filter(Boolean)
  return segments.some(s => FS_SEGMENT_RE.test(s)) || path.startsWith('~/')
}

function revealFile(path) {
  fetch('/api/files/reveal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  }).catch(() => {})
}

function FileChip({ path, label }) {
  const name = label || path.split('/').pop()
  return (
    <button
      className="md-file-chip"
      title={`Reveal in Finder: ${path}`}
      onClick={() => revealFile(path)}
    >
      📄 {name}
    </button>
  )
}

// Split a plain text string on absolute file paths.
// Returns null when no paths found (caller renders the string as-is).
function splitFilePaths(text) {
  const segments = []
  let last = 0
  let m
  // Match paths starting with / or ~ up to a whitespace boundary or quote.
  // Allow spaces inside paths (e.g. "Obsidian Vault") but stop at obvious
  // sentence-ending punctuation when followed by whitespace or end-of-string.
  const re = /((~\/|\/)[^\n"')\]`,*<>|]+\/[^\n"')\]`,*<>|]+)/g
  while ((m = re.exec(text)) !== null) {
    let path = m[1].replace(/[.,;:!?]+$/, '') // strip trailing punctuation
    if (!isFilesystemPath(path)) continue
    if (m.index > last) segments.push({ type: 'text', value: text.slice(last, m.index) })
    segments.push({ type: 'file', value: path })
    last = m.index + path.length
  }
  if (last < text.length) segments.push({ type: 'text', value: text.slice(last) })
  // Only return segmented array when at least one file was found
  return segments.some(s => s.type === 'file') ? segments : null
}

function renderWithFilePaths(text, baseKey) {
  const parts = splitFilePaths(text)
  if (!parts) return text
  return parts.map((seg, i) =>
    seg.type === 'file'
      ? <FileChip key={`${baseKey}-fp${i}`} path={seg.value} />
      : seg.value || null
  )
}

export function mdInline(text, keyPrefix = 'i') {
  const out = []
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(_[^_\n]+_)|(\[[^\]]+\]\([^)\s]+\))/g
  let last = 0
  let m
  let n = 0
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) {
      out.push(renderWithFilePaths(text.slice(last, m.index), `${keyPrefix}-${n}-pre`))
    }
    const tok = m[0]
    const key = `${keyPrefix}-${n++}`
    if (tok.startsWith('`')) {
      const inner = tok.slice(1, -1)
      // Only render as file chip when it genuinely looks like a filesystem path
      if (isFilesystemPath(inner)) {
        out.push(<FileChip key={key} path={inner} />)
      } else {
        out.push(<code key={key} className="md-code">{inner}</code>)
      }
    } else if (tok.startsWith('**')) {
      out.push(<strong key={key}>{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('*') || tok.startsWith('_')) {
      out.push(<em key={key}>{tok.slice(1, -1)}</em>)
    } else {
      // [label](href)
      const close = tok.indexOf(']')
      const label = tok.slice(1, close)
      const href = tok.slice(close + 2, -1)
      const isHttp = /^https?:\/\//i.test(href)
      const isRealFile = isFilesystemPath(href)
      if (isHttp) {
        out.push(<a key={key} href={href} target="_blank" rel="noreferrer noopener">{label}</a>)
      } else if (isRealFile) {
        // Markdown link to a local file — show chip, use label as display name
        out.push(<FileChip key={key} path={href} label={label} />)
      } else {
        // Not a real file path (e.g. /api/..., relative path, anchor) — show label as text
        out.push(<span key={key}>{label}</span>)
      }
    }
    last = m.index + tok.length
  }
  if (last < text.length) {
    out.push(renderWithFilePaths(text.slice(last), `${keyPrefix}-tail`))
  }
  return out
}


export default function Markdown({ text }) {
  if (!text) return null
  const lines = String(text).split('\n')
  const blocks = []
  let i = 0
  let key = 0
  while (i < lines.length) {
    const line = lines[i]
    const fence = line.match(/^\s*```(\w*)\s*$/)
    if (fence) {
      const body = []
      i++
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { body.push(lines[i]); i++ }
      i++
      blocks.push(<pre key={key++} className="md-pre"><code>{body.join('\n')}</code></pre>)
      continue
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      const level = heading[1].length
      blocks.push(
        <div key={key++} className={`md-h md-h${level}`}>{mdInline(heading[2], `h${key}`)}</div>
      )
      i++
      continue
    }
    if (/^\s*(?:[-*+]|\d+[.)])\s+/.test(line)) {
      const items = []
      const ordered = /^\s*\d+[.)]\s+/.test(line)
      while (i < lines.length && /^\s*(?:[-*+]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, ''))
        i++
      }
      const List = ordered ? 'ol' : 'ul'
      blocks.push(
        <List key={key++} className="md-list">
          {items.map((it, n) => <li key={n}>{mdInline(it, `l${key}-${n}`)}</li>)}
        </List>
      )
      continue
    }
    if (/^\s*>\s?/.test(line)) {
      const body = []
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, '')); i++
      }
      blocks.push(<blockquote key={key++} className="md-quote">{mdInline(body.join(' '), `q${key}`)}</blockquote>)
      continue
    }
    if (!line.trim()) { i++; continue }
    // Table: line starts with | and next line is a separator |---|
    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|[\s|:-]+\|/.test(lines[i + 1])) {
      const parseRow = (r) => r.replace(/^\s*\||\|\s*$/g, '').split('|').map(c => c.trim())
      const headers = parseRow(line)
      i += 2 // skip header + separator
      const rows = []
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        rows.push(parseRow(lines[i])); i++
      }
      blocks.push(
        <div key={key++} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>{headers.map((h, n) => <th key={n}>{mdInline(h, `th${key}-${n}`)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, rn) => (
                <tr key={rn}>{row.map((cell, cn) => <td key={cn}>{mdInline(cell, `td${key}-${rn}-${cn}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      continue
    }
    const para = []
    while (i < lines.length && lines[i].trim() &&
           !/^\s*```/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i]) &&
           !/^\s*(?:[-*+]|\d+[.)])\s+/.test(lines[i]) && !/^\s*>/.test(lines[i])) {
      para.push(lines[i]); i++
    }
    blocks.push(<p key={key++} className="md-p">{mdInline(para.join(' '), `p${key}`)}</p>)
  }
  return <div className="md">{blocks}</div>
}
