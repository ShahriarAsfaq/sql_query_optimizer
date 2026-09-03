import { useRef, useEffect, useState } from 'react'

interface CodeEditorProps {
  value: string
  onChange: (value: string) => void
  language?: string
  placeholder?: string
  readOnly?: boolean
  className?: string
  minHeight?: string
}

export default function CodeEditor({
  value,
  onChange,
  language = 'sql',
  placeholder = 'Enter SQL query...',
  readOnly = false,
  className = '',
  minHeight = '150px',
}: CodeEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [highlighted, setHighlighted] = useState('')

  useEffect(() => {
    // Simple syntax highlighting for SQL keywords
    const keywords = [
      'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN',
      'OUTER JOIN', 'ON', 'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT', 'OFFSET',
      'AND', 'OR', 'NOT', 'IN', 'NOT IN', 'EXISTS', 'NOT EXISTS', 'BETWEEN',
      'LIKE', 'ILIKE', 'IS NULL', 'IS NOT NULL', 'AS', 'ASC', 'DESC',
      'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'DISTINCT', 'CASE', 'WHEN', 'THEN',
      'ELSE', 'END', 'CAST', 'COALESCE', 'NULLIF', 'CREATE', 'TABLE', 'INDEX',
      'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP', 'TRUNCATE', 'WITH', 'UNION',
      'INTERSECT', 'EXCEPT', 'PRIMARY KEY', 'FOREIGN KEY', 'REFERENCES'
    ]

    let html = value
    // Escape HTML
    html = html.replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>')

    // Highlight keywords
    keywords.forEach(kw => {
      const regex = new RegExp(`\\b${kw}\\b`, 'gi')
      html = html.replace(regex, `<span class="sql-keyword">${kw.toUpperCase()}</span>`)
    })

    // Highlight strings
    html = html.replace(/'([^']*)'/g, '<span class="sql-string">\'$1\'</span>')
    // Highlight numbers
    html = html.replace(/\b(\d+)\b/g, '<span class="sql-number">$1</span>')
    // Highlight comments
    html = html.replace(/--(.*)$/gm, '<span class="sql-comment">--$1</span>')

    setHighlighted(html)
  }, [value])

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onChange(e.target.value)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Tab') {
      e.preventDefault()
      const start = e.currentTarget.selectionStart
      const end = e.currentTarget.selectionEnd
      const newValue = value.substring(0, start) + '  ' + value.substring(end)
      onChange(newValue)
      // Restore cursor position
      setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.selectionStart = textareaRef.current.selectionEnd = start + 2
        }
      }, 0)
    }
  }

  return (
    <div className={`relative ${className}`}>
      <div className="absolute top-2 right-2 text-xs text-gray-400 uppercase tracking-wide">
        {language.toUpperCase()}
      </div>
      <div className="relative">
        <pre className="absolute inset-0 p-3 pointer-events-none text-transparent bg-gray-900/50 rounded-lg overflow-hidden">
          <code className="text-sm leading-relaxed" dangerouslySetInnerHTML={{ __html: highlighted }} />
        </pre>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          readOnly={readOnly}
          placeholder={placeholder}
          className={`textarea bg-transparent relative z-10 ${readOnly ? 'bg-gray-50 cursor-not-allowed' : ''}`}
          style={{ minHeight }}
          spellCheck={false}
        />
      </div>
    </div>
  )
}