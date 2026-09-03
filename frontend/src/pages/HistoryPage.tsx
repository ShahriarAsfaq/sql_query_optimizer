import { useState, useEffect } from 'react'
import { api } from '../services/api'
import CodeEditor from '../components/CodeEditor'

interface HistoryItem {
  id: number
  query_type: string
  original_sql: string
  optimized_sql: string
  natural_language: string
  created_at: string
  execution_time_ms: number
  intent?: any
  explanation?: string
}

export default function HistoryPage() {
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const [pageSize] = useState(20)
  const [selectedItem, setSelectedItem] = useState<HistoryItem | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  const fetchHistory = async (pageNum = 1) => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getHistory(pageNum, pageSize)
      setHistory(data.results)
      setTotalCount(data.count)
      setPage(pageNum)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load history')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchHistory(1)
  }, [])

  const handleViewDetail = async (item: HistoryItem) => {
    setSelectedItem(item)
    setDetailLoading(true)
    setDetailError(null)
    try {
      const data = await api.getHistoryDetail(item.id)
      setSelectedItem(data)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : 'Failed to load details')
    } finally {
      setDetailLoading(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!window.confirm('Are you sure you want to delete this history item?')) return

    try {
      await api.deleteHistory(id)
      fetchHistory(page)
      if (selectedItem?.id === id) {
        setSelectedItem(null)
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete')
    }
  }

  const handleClearAll = async () => {
    if (!window.confirm('Are you sure you want to clear all history? This cannot be undone.')) return

    try {
      const data = await api.clearHistory()
      alert(`Cleared ${data.deleted_count} history items`)
      fetchHistory(1)
      setSelectedItem(null)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to clear history')
    }
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleString()
  }

  const formatSql = (sql: string) => {
    return sql
      .replace(/,/g, ',\n  ')
      .replace(/\b(FROM|JOIN|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|UNION|INTERSECT|EXCEPT)\b/gi, '\n$1')
      .replace(/\b(AND|OR)\b/gi, '\n  $1')
      .replace(/\b(ON)\b/gi, '\n    $1')
      .trim()
  }

  const queryTypeColors: Record<string, string> = {
    analyze: 'badge-info',
    optimize: 'badge-success',
    generate: 'badge-warning',
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Query History</h1>
          <p className="text-gray-600 mt-1">View and manage your query history</p>
        </div>
        {totalCount > 0 && (
          <button onClick={handleClearAll} className="btn-danger">
            Clear All History
          </button>
        )}
      </div>

      <div className="card">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-4" role="alert">
            {error}
          </div>
        )}

        {loading && !selectedItem && (
          <div className="flex justify-center py-12">
            <div className="spinner w-8 h-8 border-4"></div>
          </div>
        )}

        {!loading && history.length === 0 && !selectedItem && (
          <div className="text-center py-12">
            <svg className="w-16 h-16 text-gray-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <h3 className="text-lg font-medium text-gray-900 mb-1">No history yet</h3>
            <p className="text-gray-500">Your query history will appear here</p>
          </div>
        )}

        {!loading && history.length > 0 && !selectedItem && (
          <>
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th className="w-24">Type</th>
                    <th>Query</th>
                    <th className="w-48">Created</th>
                    <th className="w-32">Duration</th>
                    <th className="w-24">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((item) => (
                    <tr key={item.id} className="cursor-pointer hover:bg-blue-50" onClick={() => handleViewDetail(item)}>
                      <td>
                        <span className={queryTypeColors[item.query_type] || 'badge bg-gray-100 text-gray-600'}>
                          {item.query_type}
                        </span>
                      </td>
                      <td className="max-w-2xl">
                        <code className="text-sm text-gray-900 truncate block">
                          {item.query_type === 'generate' ? item.natural_language : item.original_sql}
                        </code>
                      </td>
                      <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(item.created_at)}</td>
                      <td className="text-sm text-gray-500 whitespace-nowrap">{item.execution_time_ms} ms</td>
                      <td>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleDelete(item.id) }}
                          className="text-red-600 hover:text-red-800 text-sm font-medium"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalCount > pageSize && (
              <div className="flex items-center justify-between mt-4 pt-4 border-t border-gray-200">
                <p className="text-sm text-gray-500">
                  Showing {((page - 1) * pageSize) + 1} to {Math.min(page * pageSize, totalCount)} of {totalCount} results
                </p>
                <div className="flex space-x-2">
                  <button
                    onClick={() => fetchHistory(page - 1)}
                    disabled={page === 1}
                    className="btn-secondary text-sm"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => fetchHistory(page + 1)}
                    disabled={page * pageSize >= totalCount}
                    className="btn-secondary text-sm"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}

        {/* Detail View */}
        {selectedItem && (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-bold text-gray-900">Query Details</h2>
              <button
                onClick={() => setSelectedItem(null)}
                className="text-gray-500 hover:text-gray-700"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {detailLoading && (
              <div className="flex justify-center py-8">
                <div className="spinner w-8 h-8 border-4"></div>
              </div>
            )}

            {detailError && (
              <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg" role="alert">
                {detailError}
              </div>
            )}

            {!detailLoading && !detailError && (
              <div className="space-y-6">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div className="card p-4">
                    <p className="text-sm text-gray-500">Query Type</p>
                    <p className="text-lg font-bold text-gray-900 capitalize">{selectedItem.query_type}</p>
                  </div>
                  <div className="card p-4">
                    <p className="text-sm text-gray-500">Created</p>
                    <p className="text-lg font-medium text-gray-900">{formatDate(selectedItem.created_at)}</p>
                  </div>
                  <div className="card p-4">
                    <p className="text-sm text-gray-500">Execution Time</p>
                    <p className="text-lg font-medium text-gray-900">{selectedItem.execution_time_ms} ms</p>
                  </div>
                </div>

                {selectedItem.natural_language && (
                  <div>
                    <h3 className="text-lg font-medium text-gray-900 mb-2">Natural Language</h3>
                    <p className="text-gray-700 bg-gray-50 p-4 rounded-lg">{selectedItem.natural_language}</p>
                  </div>
                )}

                {selectedItem.original_sql && (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <h3 className="text-lg font-medium text-gray-900">Original SQL</h3>
                      <button
                        onClick={() => navigator.clipboard.writeText(selectedItem.original_sql!)}
                        className="text-xs text-blue-600 hover:text-blue-800"
                      >
                        Copy
                      </button>
                    </div>
                    <CodeEditor
                      value={formatSql(selectedItem.original_sql)}
                      onChange={() => {}}
                      readOnly
                      language="sql"
                      minHeight="200px"
                    />
                  </div>
                )}

                {selectedItem.optimized_sql && (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <h3 className="text-lg font-medium text-gray-900">Optimized SQL</h3>
                      <button
                        onClick={() => navigator.clipboard.writeText(selectedItem.optimized_sql!)}
                        className="text-xs text-blue-600 hover:text-blue-800"
                      >
                        Copy
                      </button>
                    </div>
                    <CodeEditor
                      value={formatSql(selectedItem.optimized_sql)}
                      onChange={() => {}}
                      readOnly
                      language="sql"
                      minHeight="200px"
                    />
                  </div>
                )}

                {selectedItem.intent && (
                  <details className="group">
                    <summary className="cursor-pointer flex items-center space-x-2 text-sm font-medium text-gray-700 hover:text-gray-900">
                      <svg className="w-5 h-5 text-gray-400 group-open:rotate-90 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                      <span>View Intent Details</span>
                    </summary>
                    <div className="mt-4 p-4 bg-gray-50 rounded-lg">
                      <pre className="text-sm text-gray-700 overflow-x-auto">
                        <code>{JSON.stringify(selectedItem.intent, null, 2)}</code>
                      </pre>
                    </div>
                  </details>
                )}

                {selectedItem.explanation && (
                  <div className="card p-4 bg-blue-50 border-blue-200">
                    <h3 className="text-sm font-medium text-blue-800 mb-2 flex items-center space-x-2">
                      <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                      </svg>
                      <span>Explanation</span>
                    </h3>
                    <p className="text-sm text-blue-700 whitespace-pre-wrap">{selectedItem.explanation}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}