import React from 'react'

export function mdInline(text, keyPrefix = 'i') {
  const out = []
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(_[^_\n]+_)|(\[[^\]]+\]\([^)\s]+\))/g
  let last = 0
  let m
  let n = 0
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const key = `${keyPrefix}-${n++}`
    if (tok.startsWith('`')) {
      out.push(<code key={key} className="md-code">{tok.slice(1, -1)}</code>)
    } else if (tok.startsWith('**')) {
      out.push(<strong key={key}>{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('*') || tok.startsWith('_')) {
      out.push(<em key={key}>{tok.slice(1, -1)}</em>)
    } else {
      const close = tok.indexOf(']')
      const label = tok.slice(1, close)
      const href = tok.slice(close + 2, -1)
      const safe = /^https?:\/\//i.test(href)
      out.push(safe
        ? <a key={key} href={href} target="_blank" rel="noreferrer noopener">{label}</a>
        : label)
    }
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
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
