# SQL Query Optimizer

A full-stack SQL query optimizer with natural language interface. Analyze, optimize, and generate SQL queries using a Django REST API backend and React/TypeScript frontend.

## Features

- **Query Analysis**: Parse SQL into structured AST, validate against schema, check intent match
- **Query Optimization**: Generate optimized candidates (expand SELECT *, rewrite non-sargable predicates, add LIMIT, etc.)
- **Natural Language Generation**: Convert plain English to SQL using intent extraction (rule-based + LLM support)
- **Cost Scoring**: PostgreSQL EXPLAIN-based cost estimation with structural fallback
- **JOIN Detection**: Automatic JOIN clause generation from schema relationships (including self-joins)

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Frontend      │────▶│   Backend       │────▶│   Seed DB       │
│   (React/TS)    │     │   (Django/DRF)  │     │   (PostgreSQL)  │
│   Port 5173     │     │   Port 8000     │     │   Port 5432     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │   LLM (Optional)│
                        │   Anthropic API │
                        └─────────────────┘
```

### Backend Services

| Service | Purpose |
|---------|---------|
| `SQLParserService` | Parse SQL → AST using sqlglot |
| `ValidationService` | Validate tables/columns against schema |
| `IntentService` | NL → StructuredIntent (rule-based + LLM) |
| `OptimizerService` | Generate & rank candidate queries |
| `ExplanationService` | Human-readable query explanations |

### Seed Database Schema

| Table | Key Columns | Relationships |
|-------|-------------|---------------|
| `employees` | id, first_name, last_name, salary, department_id, manager_id | department_id → departments.id, manager_id → employees.id (self-ref) |
| `departments` | id, name, location, budget | — |
| `customers` | id, first_name, last_name, city, state | — |
| `purchases` | id, customer_id, product_name, category, total_amount | customer_id → customers.id |
| `products` | id, name, category, price, cost | — |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 15+ (optional - for EXPLAIN cost scoring)

### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings (ANTHROPIC_API_KEY for LLM features)

# Run migrations
python manage.py migrate

# (Optional) Set up seed database for EXPLAIN cost scoring
# Requires PostgreSQL running locally
python seed_db_setup.py

# Start server
python manage.py runserver 8000
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev

# Build for production
npm run build
```

### Using Docker (Alternative)

```bash
# From project root
docker-compose up -d
```

## API Endpoints

### POST `/api/queries/analyze/`

Analyze a SQL query with optional intent matching.

**Request:**
```json
{
  "sql": "SELECT department, AVG(salary) FROM employees GROUP BY department ORDER BY avg_salary DESC LIMIT 5",
  "schema": { ... },
  "intent_text": "Show top 5 departments by average salary"
}
```

**Response:**
```json
{
  "parsed_query": { "operation_type": "SELECT", "tables": [...], "joins": [...], ... },
  "validation": { "is_valid": true, "issues": [] },
  "intent_match": { "match_score": 1.0, "mismatches": [] },
  "explanation": "This query retrieves data from employees..."
}
```

### POST `/api/queries/optimize/`

Generate optimized query candidates.

**Request:**
```json
{
  "sql": "SELECT * FROM employees WHERE LOWER(first_name) = 'john'",
  "schema": { ... }
}
```

**Response:**
```json
{
  "original_sql": "...",
  "original_cost": 18.0,
  "candidates": [
    {
      "sql": "SELECT id, first_name, ... FROM employees WHERE LOWER(first_name) = 'john'",
      "description": "Expanded SELECT * to explicit columns",
      "cost": 13.0,
      "complexity_score": 1.0,
      "validation_passed": true
    }
  ],
  "best_candidate": { ... }
}
```

### POST `/api/queries/generate/`

Generate SQL from natural language intent.

**Request:**
```json
{
  "intent_text": "Show employees with their departments",
  "schema": { ... }
}
```

**Response:**
```json
{
  "intent": {
    "category": "JOIN",
    "operation": "SELECT",
    "entity": "employees",
    "join_tables": ["departments"]
  },
  "candidates": [
    {
      "sql": "SELECT * FROM employees JOIN departments ON employees.department_id = departments.id",
      "description": "Primary query matching intent",
      "cost": 61.0
    }
  ]
}
```

## Supported Intent Patterns

| Category | Example | Generated SQL |
|----------|---------|---------------|
| **TOP_N** | "Show top 5 departments by average salary" | `SELECT department, AVG(salary) ... GROUP BY department ORDER BY avg_salary DESC LIMIT 5` |
| **TOP_N** | "Show me the top 10 highest paid employees" | `SELECT salary FROM employees ORDER BY salary DESC LIMIT 10` |
| **JOIN** | "Show employees with their departments" | `SELECT * FROM employees JOIN departments ON employees.department_id = departments.id` |
| **JOIN** | "Find employees with their managers" | `SELECT * FROM employees JOIN employees mgr ON employees.manager_id = mgr.id` |
| **FILTER** | "List all customers from New York" | `SELECT * FROM customers WHERE city = 'New York'` |
| **AGGREGATE** | "Show total sales by product category" | `SELECT SUM(total_amount), category FROM purchases GROUP BY category` |
| **RETRIEVE** | "Find employees who earn more than 100000" | `SELECT * FROM employees WHERE salary > 100000` |

## Optimization Rules

1. **Expand SELECT \*** → Explicit column list
2. **Rewrite non-sargable predicates** → `LOWER(col) = 'val'` → `col ILIKE 'val'`
3. **Add LIMIT** → To ORDER BY queries without LIMIT
4. **IN → EXISTS** → Rewrite IN subqueries
5. **CTE conversion** → Nested subqueries to CTEs
6. **Predicate pushdown** → Push WHERE into CTEs
7. **JOIN condition hints** → Suggest FK-based conditions

## LLM Integration

Set `ANTHROPIC_API_KEY` in `.env` to enable:
- NL → intent extraction (more accurate than rule-based)
- Explanation polishing (natural language output)

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

Without API key, falls back to `MockLLMClient` for testing.

## Testing

```bash
# Backend tests
cd backend
python test_intent.py
python test_optimizer.py
python test_api_integration.py

# Frontend linting
cd frontend
npm run lint
```

## Deployment

### Render (Recommended)

1. Connect GitHub repo to Render
2. Create Web Service:
   - Build: `cd backend && pip install -r requirements.txt`
   - Start: `cd backend && gunicorn sql_optimizer.wsgi`
3. Create PostgreSQL database
4. Set environment variables
5. Run `seed_db_setup.py` as a one-off job

### Docker Production

```dockerfile
# Backend
FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .
CMD ["gunicorn", "sql_optimizer.wsgi"]

# Frontend (nginx + static files)
FROM nginx:alpine
COPY frontend/dist/ /usr/share/nginx/html/
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
```

## Project Structure

```
SQL Query Optimizer/
├── backend/
│   ├── queries/
│   │   ├── services/
│   │   │   ├── sql_parser.py      # sqlglot wrapper
│   │   │   ├── validator.py       # Schema validation
│   │   │   ├── intent.py          # NL → StructuredIntent
│   │   │   ├── optimizer.py       # Candidate generation & scoring
│   │   │   ├── explanation.py     # Query explanations
│   │   │   └── llm_client.py      # Anthropic API wrapper
│   │   ├── views.py               # API endpoints
│   │   ├── serializers.py         # DRF serializers
│   │   └── urls.py
│   ├── sql_optimizer/             # Django settings
│   ├── seed_db_setup.py           # Seed DB creation
│   ├── test_*.py                  # Integration tests
│   ├── manage.py
│   └── .env
├── frontend/
│   ├── src/
│   │   ├── App.tsx                # Main UI (3 tabs: Analyze/Optimize/Generate)
│   │   ├── main.tsx
│   │   └── index.css
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── tailwind.config.js
└── README.md
```

## Contributing

1. Fork the repository
2. Create feature branch
3. Run tests: `python test_api_integration.py`
4. Submit PR

## License

MIT License - see LICENSE file for details.