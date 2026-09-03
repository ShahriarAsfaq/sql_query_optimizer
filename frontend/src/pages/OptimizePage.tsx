import { useState } from 'react'
import { api } from '../services/api'
import CodeEditor from '../components/CodeEditor'

export default function OptimizePage() {
  const [sql, setSql] = useState('')
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dialect, setDialect] = useState('postgresql')
  const [showDiff, setShowDiff] = useState(true)

  const handleOptimize = async () => {
    if (!sql.trim()) {
      setError('Please enter a SQL query')
      return
    }

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const data = await api.optimize(sql, dialect)
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Optimization failed')
    } finally {
      setLoading(false)
    }
  }

  const copyToClipboard = async (text: string) => {
    await navigator.clipboard.writeText(text)
  }

  const formatSql = (sql: string) => {
    // Basic SQL formatting for display
    return sql
      .replace(/,/g, ',\n  ')
      .replace(/\b(FROM|JOIN|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|UNION|INTERSECT|EXCEPT)\b/gi, '\n$1')
      .replace(/\b(AND|OR)\b/gi, '\n  $1')
      .replace(/\b(ON)\b/gi, '\n    $1')
      .trim()
  }

  const complexityColor = (score: number) => {
    if (score < 30) return 'text-green-600'
    if (score < 60) return 'text-yellow-600'
    if (score < 80) return 'text-orange-600'
    return 'text-red-600'
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Optimize Query</h1>
        <p className="text-gray-600 mt-1">Get optimization suggestions and improved SQL queries</p>
      </div>

      <div className="card">
        <div className="flex flex-col sm:flex-row sm:items-end gap-4 mb-6">
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 mb-1">SQL Query</label>
            <CodeEditor
              value={sql}
              onChange={setSql}
              placeholder="SELECT * FROM employees e JOIN departments d ON e.dept_id = d.id WHERE e.salary > 50000 ORDER BY e.hire_date"
              minHeight="150px"
            />
          </div>
          <div className="flex flex-col sm:flex-row items-start sm:items-end space-y-2 sm:space-y-0 sm:space-x-4 sm:ml-4 w-full sm:w-auto">
            <div className="w-full sm:w-48">
              <label className="block text-sm font-medium text-gray-700 mb-1">Dialect</label>
              <select
                value={dialect}
                onChange={(e) => setDialect(e.target.value)}
                className="input"
              >
                <option value="postgresql">PostgreSQL</option>
                <option value="mysql">MySQL</option>
                <option value="sqlite">SQLite</option>
              </select>
            </div>
            <button
              onClick={handleOptimize}
              disabled={loading || !sql.trim()}
              className="btn-primary whitespace-nowrap w-full sm:w-auto"
            >
              {loading ? (
                <span className="flex items-center space-x-2 justify-center">
                  <span className="spinner w-4 h-4"></span>
                  <span>Optimizing...</span>
                </span>
              ) : (
                'Optimize'
              )}
            </button>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-4" role="alert">
            {error}
          </div>
        )}

        {result && (
          <div className="space-y-6">
            {/* Summary Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="card p-4">
                <p className="text-sm text-gray-500">Optimization Candidates</p>
                <p className="text-2xl font-bold text-gray-900">{result.candidates?.length || 0}</p>
              </div>
              <div className="card p-4">
                <p className="text-sm text-gray-500">Original Cost</p>
                <p className="text-2xl font-bold text-gray-900">{result.original_cost != null ? result.original_cost.toFixed(2) : 'N/A'}</p>
              </div>
              <div className="card p-4">
                <p className="text-sm text-gray-500">Best Candidate Complexity</p>
                <p className={`text-2xl font-bold ${complexityColor(result.best_candidate?.complexity_score || 0)}`}>{result.best_candidate?.complexity_score || 0}/100</p>
              </div>
            </div>

            {/* Optimizations List */}
            {result.candidates && result.candidates.length > 0 && (
              <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                <h3 className="text-sm font-medium text-green-800 mb-3 flex items-center space-x-2">
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                  <span>Optimization Candidates ({result.candidates.length})</span>
                </h3>
                <ul className="space-y-2 text-sm text-green-700">
                  {result.candidates.map((candidate: any, i: number) => (
                    <li key={i} className="flex items-start space-x-2">
                      <svg className="w-4 h-4 mt-0.5 flex-shrink-0 text-green-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                      <span>{candidate.description}</span>
                      {i === 0 && <span className="text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded">Best</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* SQL Comparison */}
            <div>
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-medium text-gray-900">Query Comparison</h3>
                <label className="flex items-center space-x-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={showDiff}
                    onChange={(e) => setShowDiff(e.target.checked)}
                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span>Side by side</span>
                </label>
              </div>

              {showDiff ? (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-gray-700">Original Query</span>
                      <button
                        onClick={() => copyToClipboard(result.original_sql)}
                        className="text-xs text-blue-600 hover:text-blue-800"
                        title="Copy to clipboard"
                      >
                        Copy
                      </button>
                    </div>
                    <CodeEditor
                      value={formatSql(result.original_sql)}
                      onChange={() => {}}
                      readOnly
                      language="sql"
                      minHeight="250px"
                    />
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-gray-700">Best Optimized Query</span>
                      <button
                        onClick={() => copyToClipboard(result.best_candidate?.sql || '')}
                        className="text-xs text-blue-600 hover:text-blue-800"
                        title="Copy to clipboard"
                      >
                        Copy
                      </button>
                    </div>
                    <CodeEditor
                      value={formatSql(result.best_candidate?.sql || '')}
                      onChange={() => {}}
                      readOnly
                      language="sql"
                      minHeight="250px"
                    />
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-gray-700">Original Query</span>
                      <button
                        onClick={() => copyToClipboard(result.original_sql)}
                        className="text-xs text-blue-600 hover:text-blue-800"
                        title="Copy to clipboard"
                      >
                        Copy
                      </button>
                    </div>
                    <CodeEditor
                      value={formatSql(result.original_sql)}
                      onChange={() => {}}
                      readOnly
                      language="sql"
                      minHeight="200px"
                    />
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-gray-700">Best Optimized Query</span>
                      <button
                        onClick={() => copyToClipboard(result.best_candidate?.sql || '')}
                        className="text-xs text-blue-600 hover:text-blue-800"
                        title="Copy to clipboard"
                      >
                        Copy
                      </button>
                    </div>
                    <CodeEditor
                      value={formatSql(result.best_candidate?.sql || '')}
                      onChange={() => {}}
                      readOnly
                      language="sql"
                      minHeight="200px"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Warnings */}
            {result.candidates && result.candidates.some((c: any) => c.validation_errors && c.validation_errors.length > 0) && (
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <h3 className="text-sm font-medium text-yellow-800 mb-2">Validation Warnings</h3>
                <ul className="space-y-1 text-sm text-yellow-700">
                  {result.candidates.flatMap((c: any, i: number) =>
                    (c.validation_errors || []).map((err: string, j: number) => (
                      <li key={`${i}-${j}`} className="flex items-start space-x-2">
                        <svg className="w-4 h-4 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                          <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                        </svg>
                        <span>{c.description}: {err}</span>
                      </li>
                    ))
                  )}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}