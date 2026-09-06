import { useState } from 'react'
import { api } from '../services/api'
import CodeEditor from '../components/CodeEditor'

export default function AnalyzePage() {
  const [sql, setSql] = useState('')
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [explain, setExplain] = useState(false)
  const [schemaContext, setSchemaContext] = useState('')
  const [activeTab, setActiveTab] = useState<'ast' | 'tables' | 'joins' | 'explain'>('ast')

  const handleAnalyze = async () => {
    if (!sql.trim()) {
      setError('Please enter a SQL query')
      return
    }

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const data = await api.analyze(sql, explain, schemaContext || undefined)
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Analysis failed')
    } finally {
      setLoading(false)
    }
  }

  const formatJson = (obj: any) => {
    return JSON.stringify(obj, null, 2)
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
        <h1 className="text-3xl font-bold text-gray-900">Analyze Query</h1>
        <p className="text-gray-600 mt-1">Parse and analyze SQL query structure, complexity, and execution plan</p>
      </div>

      <div className="card">
        <div className="flex flex-col sm:flex-row sm:items-end gap-4 mb-6">
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 mb-1">SQL Query</label>
            <CodeEditor
              value={sql}
              onChange={setSql}
              placeholder="SELECT * FROM employees WHERE department = 'Engineering' ORDER BY salary DESC LIMIT 10"
              minHeight="150px"
            />
          </div>
          <div className="w-full sm:w-64">
            <label className="block text-sm font-medium text-gray-700 mb-1">Schema Context (Optional)</label>
            <textarea
              value={schemaContext}
              onChange={(e) => setSchemaContext(e.target.value)}
              placeholder="e.g., tables: employees(id, name, salary, dept_id), departments(id, name)"
              className="textarea font-mono text-sm"
              rows={2}
            />
            <p className="text-xs text-gray-500 mt-1">Provide table/column info for better accuracy</p>
          </div>
          <div className="flex items-center space-x-4 sm:ml-4">
            <label className="flex items-center space-x-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={explain}
                onChange={(e) => setExplain(e.target.checked)}
                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span>Include EXPLAIN plan</span>
            </label>
            <button
              onClick={handleAnalyze}
              disabled={loading || !sql.trim()}
              className="btn-primary whitespace-nowrap"
            >
              {loading ? (
                <span className="flex items-center space-x-2">
                  <span className="spinner w-4 h-4"></span>
                  <span>Analyzing...</span>
                </span>
              ) : (
                'Analyze'
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
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="card p-4">
                <p className="text-sm text-gray-500">Query Type</p>
                <p className="text-2xl font-bold text-gray-900 capitalize">{result.parsed_query?.operation_type?.replace('_', ' ') || 'UNKNOWN'}</p>
              </div>
              <div className="card p-4">
                <p className="text-sm text-gray-500">Tables Referenced</p>
                <p className="text-2xl font-bold text-gray-900">{result.parsed_query?.tables?.length || 0}</p>
              </div>
              <div className="card p-4">
                <p className="text-sm text-gray-500">Columns Referenced</p>
                <p className="text-2xl font-bold text-gray-900">{result.parsed_query?.columns?.length || 0}</p>
              </div>
              <div className="card p-4">
                <p className="text-sm text-gray-500">Complexity Score</p>
                <p className={`text-2xl font-bold ${complexityColor(result.complexity_score || 0)}`}>{result.complexity_score || 0}/100</p>
              </div>
            </div>

            {/* Tabs */}
            <div className="border-b border-gray-200">
              <nav className="flex space-x-8" aria-label="Analysis tabs">
                <button
                  onClick={() => setActiveTab('ast')}
                  className={`py-3 px-1 border-b-2 font-medium text-sm transition-colors ${
                    activeTab === 'ast'
                      ? 'border-blue-500 text-blue-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                >
                  AST
                </button>
                <button
                  onClick={() => setActiveTab('tables')}
                  className={`py-3 px-1 border-b-2 font-medium text-sm transition-colors ${
                    activeTab === 'tables'
                      ? 'border-blue-500 text-blue-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                >
                  Tables & Columns
                </button>
                <button
                  onClick={() => setActiveTab('joins')}
                  className={`py-3 px-1 border-b-2 font-medium text-sm transition-colors ${
                    activeTab === 'joins'
                      ? 'border-blue-500 text-blue-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                >
                  Joins ({result.parsed_query?.joins?.length || 0})
                </button>
                {explain && result.explain_plan && (
                  <button
                    onClick={() => setActiveTab('explain')}
                    className={`py-3 px-1 border-b-2 font-medium text-sm transition-colors ${
                      activeTab === 'explain'
                        ? 'border-blue-500 text-blue-600'
                        : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                    }`}
                  >
                    EXPLAIN Plan
                  </button>
                )}
              </nav>
            </div>

            {/* Tab Content */}
            <div className="mt-4">
              {activeTab === 'ast' && (
                <div className="table-container">
                  <pre className="bg-gray-900 text-gray-100 p-4 rounded-lg overflow-x-auto text-sm">
                    <code>{formatJson(result.parsed_query)}</code>
                  </pre>
                </div>
              )}

              {activeTab === 'tables' && (
                <div className="space-y-4">
                  <div>
                    <h3 className="text-lg font-medium text-gray-900 mb-2">Tables</h3>
                    {result.parsed_query?.tables?.length > 0 ? (
                      <ul className="space-y-1">
                        {result.parsed_query.tables.map((table: any, i: number) => (
                          <li key={i} className="flex items-center space-x-2">
                            <span className="w-2 h-2 bg-blue-500 rounded-full"></span>
                            <code className="text-gray-900 bg-gray-100 px-2 py-1 rounded">{table.name}{table.alias ? ` AS ${table.alias}` : ''}</code>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-gray-500">No tables found</p>
                    )}
                  </div>
                  <div>
                    <h3 className="text-lg font-medium text-gray-900 mb-2">Columns</h3>
                    {result.parsed_query?.columns?.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {result.parsed_query.columns.map((col: any, i: number) => (
                          <span key={i} className="badge bg-blue-50 text-blue-700">{col.table ? `${col.table}.` : ''}{col.name}{col.alias ? ` AS ${col.alias}` : ''}</span>
                        ))}
                      </div>
                    ) : (
                      <p className="text-gray-500">No columns found</p>
                    )}
                  </div>
                </div>
              )}

              {activeTab === 'joins' && (
                <div>
                  {result.parsed_query?.joins && result.parsed_query.joins.length > 0 ? (
                    <div className="space-y-4">
                      {result.parsed_query.joins.map((join: any, i: number) => (
                        <div key={i} className="card p-4">
                          <div className="flex items-center justify-between mb-2">
                            <span className="font-medium text-gray-900">{join.type || 'INNER'} JOIN</span>
                            <span className="badge badge-info">{join.table.name}</span>
                          </div>
                          <div className="text-sm text-gray-600">
                            <p><strong>Condition:</strong> <code className="bg-gray-100 px-1 rounded">{join.condition || 'N/A'}</code></p>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500 text-center py-8">No joins found in this query</p>
                  )}
                </div>
              )}

              {activeTab === 'explain' && result.explain_plan && (
                <div className="table-container">
                  <pre className="bg-gray-900 text-green-300 p-4 rounded-lg overflow-x-auto text-sm font-mono whitespace-pre-wrap">
                    <code>{result.explain_plan}</code>
                  </pre>
                </div>
              )}

              {/* Explanation */}
              {result.explanation && (
                <div className="mt-6">
                  <h3 className="text-lg font-medium text-gray-900 mb-3">Explanation</h3>
                  <div className="prose prose-sm max-w-none bg-gray-50 p-4 rounded-lg">
                    {result.explanation.split('\n').map((line: string, i: number) => (
                      <p key={i} className="whitespace-pre-wrap">{line}</p>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Warnings */}
            {result.validation?.issues && result.validation.issues.length > 0 && (
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <h3 className="text-sm font-medium text-yellow-800 mb-2">Warnings</h3>
                <ul className="space-y-1 text-sm text-yellow-700">
                  {result.validation.issues
                    .filter((issue: any) => issue.severity === 'warning')
                    .map((issue: any, i: number) => (
                    <li key={i} className="flex items-start space-x-2">
                      <svg className="w-4 h-4 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                      </svg>
                      <span>{issue.message}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}