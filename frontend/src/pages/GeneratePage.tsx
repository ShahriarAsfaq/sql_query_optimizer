import { useState } from 'react'
import { api } from '../services/api'
import CodeEditor from '../components/CodeEditor'

export default function GeneratePage() {
  const [naturalLanguage, setNaturalLanguage] = useState('')
  const [schemaContext, setSchemaContext] = useState('')
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  // Clarifying questions state
  const [clarifyingQuestions, setClarifyingQuestions] = useState<Array<{
    field: string
    type: 'select' | 'text'
    question: string
    options: string[]
    current_value: string
    optional?: boolean
    validation?: string
  }>>([])
  const [answers, setAnswers] = useState<Record<string, any>>({})
  const [showClarifyingModal, setShowClarifyingModal] = useState(false)
  // Reserved for future use with clarifying questions flow
  const [_partialIntent, setPartialIntent] = useState<any>(null)

  const handleGenerate = async () => {
    if (!naturalLanguage.trim()) {
      setError('Please describe what you want to query')
      return
    }

    setLoading(true)
    setError(null)
    setResult(null)
    setCopied(false)
    setClarifyingQuestions([])
    setAnswers({})
    setPartialIntent(null)
    setShowClarifyingModal(false)

    try {
      const data = await api.generate(naturalLanguage, schemaContext || undefined)
      handleGenerateResponse(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed')
    } finally {
      setLoading(false)
    }
  }

  const handleGenerateResponse = async (data: any) => {
    if (data.status === 'needs_clarification') {
      // Show clarifying questions
      setClarifyingQuestions(data.questions || [])
      setPartialIntent(data.partial_intent)
      setShowClarifyingModal(true)
    } else {
      // Success
      setResult(data)
    }
  }

  const handleAnswerSubmit = async () => {
    // Validate required answers
    for (const q of clarifyingQuestions) {
      if (!q.optional && (!answers[q.field] || answers[q.field] === '')) {
        setError(`Please answer: ${q.question}`)
        return
      }
      // Validate number fields
      if (q.validation === 'number' && answers[q.field] && isNaN(Number(answers[q.field]))) {
        setError(`Please enter a valid number for: ${q.question}`)
        return
      }
    }

    setLoading(true)
    setError(null)

    try {
      const data = await api.generate(naturalLanguage, schemaContext || undefined, answers)
      handleGenerateResponse(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed')
    } finally {
      setLoading(false)
    }
  }

  const handleAnswerChange = (field: string, value: any) => {
    setAnswers(prev => ({ ...prev, [field]: value }))
  }

  const handleSelectAnswer = (field: string, value: string) => {
    setAnswers(prev => ({ ...prev, [field]: value }))
  }

  const handleCloseClarifying = () => {
    setShowClarifyingModal(false)
    setClarifyingQuestions([])
    setAnswers({})
    setPartialIntent(null)
  }

  const copyToClipboard = async (text: string) => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const formatSql = (sql: string) => {
    return sql
      .replace(/,/g, ',\n  ')
      .replace(/\b(FROM|JOIN|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|UNION|INTERSECT|EXCEPT)\b/gi, '\n$1')
      .replace(/\b(AND|OR)\b/gi, '\n  $1')
      .replace(/\b(ON)\b/gi, '\n    $1')
      .trim()
  }

  const confidenceColor = (confidence: number) => {
    if (confidence >= 0.8) return 'text-green-600'
    if (confidence >= 0.6) return 'text-yellow-600'
    return 'text-red-600'
  }

  const intentCategoryColors: Record<string, string> = {
    SELECT: 'badge-info',
    FILTER: 'badge-warning',
    AGGREGATE: 'badge-success',
    JOIN: 'badge-info',
    TOP_N: 'badge-warning',
    SORT: 'badge-info',
    GROUP_BY: 'badge-success',
    WINDOW: 'badge-info',
    DDL: 'badge-error',
    DML: 'badge-error',
    UNKNOWN: 'badge bg-gray-100 text-gray-600',
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Generate SQL</h1>
        <p className="text-gray-600 mt-1">Convert natural language to SQL using AI</p>
      </div>

      <div className="card">
        <div className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">What do you want to query?</label>
            <textarea
              value={naturalLanguage}
              onChange={(e) => setNaturalLanguage(e.target.value)}
              placeholder="e.g., Show me the top 10 highest paid employees with their department names"
              className="textarea"
              rows={3}
            />
          </div>

          <div>
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

          <button
            onClick={handleGenerate}
            disabled={loading || !naturalLanguage.trim()}
            className="btn-primary"
          >
            {loading ? (
              <span className="flex items-center space-x-2">
                <span className="spinner w-4 h-4"></span>
                <span>Generating SQL...</span>
              </span>
            ) : (
              'Generate SQL'
            )}
          </button>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg" role="alert">
              {error}
            </div>
          )}

          {/* Clarifying Questions Modal */}
          {showClarifyingModal && clarifyingQuestions.length > 0 && (
            <div className="fixed inset-0 z-50 overflow-y-auto" role="dialog" aria-modal="true" aria-labelledby="clarifying-title">
              <div className="flex min-h-full items-center justify-center p-4">
                <div className="fixed inset-0 bg-black/50 transition-opacity" onClick={handleCloseClarifying} />
                <div className="relative w-full max-w-md bg-white rounded-xl shadow-xl p-6">
                  <div className="flex items-center justify-between mb-4">
                    <h3 id="clarifying-title" className="text-lg font-semibold text-gray-900">
                      Need More Information
                    </h3>
                    <button
                      onClick={handleCloseClarifying}
                      className="text-gray-400 hover:text-gray-600"
                      aria-label="Close"
                    >
                      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                  <p className="text-sm text-gray-600 mb-4">
                    Please provide the missing details to generate the SQL:
                  </p>
                  <div className="space-y-4 max-h-80 overflow-y-auto">
                    {clarifyingQuestions.map((q, i) => (
                      <div key={i} className="space-y-2">
                        <label className="block text-sm font-medium text-gray-700">
                          {q.question} {q.optional && <span className="text-gray-400 ml-1">(optional)</span>}
                        </label>
                        {q.type === 'select' ? (
                          <select
                            value={answers[q.field] || q.current_value || ''}
                            onChange={(e) => handleSelectAnswer(q.field, e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                          >
                            <option value="">Select...</option>
                            {q.options.map((opt, oi) => (
                              <option key={oi} value={opt}>{opt}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type="text"
                            value={answers[q.field] || q.current_value || ''}
                            onChange={(e) => handleAnswerChange(q.field, e.target.value)}
                            placeholder="Enter value..."
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                          />
                        )}
                      </div>
                    ))}
                  </div>
                  <div className="mt-6 flex justify-end space-x-3">
                    <button
                      onClick={handleCloseClarifying}
                      className="btn-secondary"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleAnswerSubmit}
                      disabled={loading}
                      className="btn-primary"
                    >
                      {loading ? (
                        <span className="flex items-center space-x-2">
                          <span className="spinner w-4 h-4"></span>
                          <span>Generating...</span>
                        </span>
                      ) : (
                        'Generate SQL'
                      )}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {result && (
            <div className="space-y-6 border-t border-gray-200 pt-6">
              {/* Generated SQL */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-lg font-medium text-gray-900">Generated SQL</h3>
                  <button
                    onClick={() => copyToClipboard(result.sql)}
                    className="btn-secondary text-sm"
                  >
                    {copied ? (
                      <span className="flex items-center space-x-1 text-green-600">
                        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                        </svg>
                        <span>Copied!</span>
                      </span>
                    ) : (
                      'Copy SQL'
                    )}
                  </button>
                </div>
                <CodeEditor
                  value={formatSql(result.sql)}
                  onChange={() => {}}
                  readOnly
                  language="sql"
                  minHeight="200px"
                />
              </div>

              {/* Metadata */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="card p-4">
                  <p className="text-sm text-gray-500">Intent Category</p>
                  <div className="flex flex-wrap gap-2 mt-1">
                    {result.intent?.category ? (
                      <span key={0} className={intentCategoryColors[result.intent.category] || 'badge bg-gray-100 text-gray-600'}>
                        {result.intent.category.replace('_', ' ')}
                      </span>
                    ) : (
                      result.intent?.categories?.map((cat: string, i: number) => (
                        <span key={i} className={intentCategoryColors[cat] || 'badge bg-gray-100 text-gray-600'}>
                          {cat.replace('_', ' ')}
                        </span>
                      ))
                    )}
                  </div>
                </div>
                <div className="card p-4">
                  <p className="text-sm text-gray-500">Target Tables</p>
                  <p className="text-lg font-medium text-gray-900 mt-1">
                    {result.intent?.entity
                      ? result.intent.entity
                      : result.intent?.target_tables?.length > 0
                      ? result.intent.target_tables.join(', ')
                      : '—'}
                  </p>
                </div>
                <div className="card p-4">
                  <p className="text-sm text-gray-500">Confidence</p>
                  <div className="flex items-center space-x-2 mt-1">
                    <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${confidenceColor((result.confidence || 0) * 100).replace('text', 'bg')}`}
                        style={{ width: `${(result.confidence || 0) * 100}%` }}
                      />
                    </div>
                    <span className={`font-medium ${confidenceColor((result.confidence || 0) * 100)}`}>
                      {Math.round((result.confidence || 0) * 100)}%
                    </span>
                  </div>
                </div>
              </div>

              {/* Explanation */}
              <div className="card p-4 bg-blue-50 border-blue-200">
                <h3 className="text-sm font-medium text-blue-800 mb-2 flex items-center space-x-2">
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                  </svg>
                  <span>Explanation</span>
                </h3>
                <p className="text-sm text-blue-700 whitespace-pre-wrap">{result.explanation || 'No explanation available'}</p>
              </div>

              {/* Intent Details */}
              <details className="group">
                <summary className="cursor-pointer flex items-center space-x-2 text-sm font-medium text-gray-700 hover:text-gray-900">
                  <svg className="w-5 h-5 text-gray-400 group-open:rotate-90 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  <span>View Intent Details</span>
                </summary>
                <div className="mt-4 p-4 bg-gray-50 rounded-lg">
                  <pre className="text-sm text-gray-700 overflow-x-auto">
                    <code>{JSON.stringify(result.intent, null, 2)}</code>
                  </pre>
                </div>
              </details>

              {/* Warnings */}
              {result.warnings && result.warnings.length > 0 && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-yellow-800 mb-2">Warnings</h3>
                  <ul className="space-y-1 text-sm text-yellow-700">
                    {result.warnings.map((warning: string, i: number) => (
                      <li key={i} className="flex items-start space-x-2">
                        <svg className="w-4 h-4 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                          <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                        </svg>
                        <span>{warning}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Example Queries */}
              <div className="bg-gray-50 rounded-lg p-4">
                <h3 className="text-sm font-medium text-gray-700 mb-3">Try these examples:</h3>
                <div className="flex flex-wrap gap-2">
                  {[
                    'Show all employees in the Engineering department',
                    'Top 5 highest paid employees with their department names',
                    'Average salary by department',
                    'Employees hired after 2020 with their managers',
                    'Count of employees per department',
                  ].map((example, i) => (
                    <button
                      key={i}
                      onClick={() => setNaturalLanguage(example)}
                      className="text-xs text-blue-600 hover:text-blue-800 hover:underline px-3 py-1 bg-blue-50 rounded-full"
                    >
                      {example}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}