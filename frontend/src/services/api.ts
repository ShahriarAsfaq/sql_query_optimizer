const API_BASE = import.meta.env.VITE_API_BASE || '/api'

interface RequestOptions extends RequestInit {
  params?: Record<string, string>
}

async function request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { params, headers, ...fetchOptions } = options

  const url = new URL(`${API_BASE}${endpoint}`, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      url.searchParams.append(key, value)
    })
  }

  const defaultHeaders: HeadersInit = {
    'Content-Type': 'application/json',
    ...headers,
  }

  // Add API key if available
  const apiKey = localStorage.getItem('apiKey')
  if (apiKey) {
    (defaultHeaders as Record<string, string>)['Authorization'] = `Api-Key ${apiKey}`
  }

  const response = await fetch(url.toString(), {
    ...fetchOptions,
    headers: defaultHeaders,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'An error occurred' }))
    throw new Error(error.detail || `HTTP error ${response.status}`)
  }

  return response.json()
}

export const api = {
  // Health check
  health: () => request<{ status: string; timestamp: string }>('/health/'),

  // Analyze query
  analyze: (sql: string, explain = false) =>
    request<{
      parsed_query: {
        operation_type: string
        tables: Array<{ name: string; alias?: string; schema?: string }>
        columns: Array<{ name: string; table?: string; alias?: string }>
        joins: Array<{ type: string; table: { name: string; alias?: string }; condition?: string }>
        where_conditions: any[]
        group_by: string[]
        having_conditions: string[]
        order_by: any[]
        limit: number | null
        window_functions: string[]
        aggregations: string[]
        subqueries: string[]
        cte_names: string[]
        is_valid: boolean
        errors: any[]
        raw_sql: string
        dialect: string
      }
      validation: {
        is_valid: boolean
        issues: any[]
        warnings: string[]
      }
      intent_match: any
      explanation: string
      history_id: number
      complexity_score: number
      explain_plan?: any
    }>('/queries/analyze/', {
      method: 'POST',
      body: JSON.stringify({ sql, explain }),
    }),

  // Optimize query
  optimize: (sql: string, dialect = 'postgresql') =>
    request<{
      original_sql: string
      original_cost: number | null
      candidates: Array<{
        sql: string
        description: string
        cost: number | null
        complexity_score: number | null
        validation_passed: boolean
        validation_errors: string[]
      }>
      best_candidate: {
        sql: string
        description: string
        cost: number | null
        complexity_score: number | null
        validation_passed: boolean
        validation_errors: string[]
      } | null
    }>('/queries/optimize/', {
      method: 'POST',
      body: JSON.stringify({ sql, dialect }),
    }),

  // Generate SQL from natural language
  generate: (natural_language: string, schema_context?: string, answers?: Record<string, any>) =>
    request<{
      status?: 'success' | 'needs_clarification'
      sql: string
      intent: any
      explanation: string
      confidence: number
      warnings: string[]
      // For needs_clarification response
      questions?: Array<{
        field: string
        type: 'select' | 'text'
        question: string
        options: string[]
        current_value: string
        optional?: boolean
        validation?: string
      }>
      partial_intent?: any
    }>('/queries/generate/', {
      method: 'POST',
      body: JSON.stringify({ intent_text: natural_language, schema: schema_context, answers }),
    }),

  // Query history
  getHistory: (page = 1, pageSize = 20) =>
    request<{
      results: Array<{
        id: number
        query_type: string
        original_sql: string
        optimized_sql: string
        natural_language: string
        created_at: string
        execution_time_ms: number
      }>
      count: number
      next: string | null
      previous: string | null
    }>(`/queries/history/`, {
      params: { page: page.toString(), page_size: pageSize.toString() },
    }),

  getHistoryDetail: (id: number) =>
    request<{
      id: number
      query_type: string
      original_sql: string
      optimized_sql: string
      natural_language: string
      intent: any
      explanation: string
      created_at: string
      execution_time_ms: number
    }>(`/queries/history/${id}/`),

  deleteHistory: (id: number) =>
    request<void>(`/queries/history/${id}/`, {
      method: 'DELETE',
    }),

  clearHistory: () =>
    request<{ deleted_count: number }>('/queries/history/clear/', {
      method: 'POST',
    }),
}

// API key management
export function setApiKey(key: string) {
  localStorage.setItem('apiKey', key)
}

export function getApiKey(): string | null {
  return localStorage.getItem('apiKey')
}

export function clearApiKey() {
  localStorage.removeItem('apiKey')
}

export function hasApiKey(): boolean {
  return !!localStorage.getItem('apiKey')
}