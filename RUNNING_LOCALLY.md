# Running SQL Query Optimizer Locally

## Prerequisites

- Docker Desktop (or Docker Engine + Docker Compose)
- Git (to clone the repository)
- Node.js 18+ (for frontend development outside Docker)
- Python 3.11+ (for backend development outside Docker)
- Java 17 + Maven 3.9+ (for Calcite server development outside Docker)

## Quick Start with Docker Compose (Recommended)

This is the easiest way to run all services.

### 1. Clone and Navigate
```bash
cd "d:\AI projects\SQL Query Optimizer"
```

### 2. Start All Services
```bash
docker compose up -d
```

This starts 5 services:
- **seed-db** (PostgreSQL 15): Port 5432
- **calcite-server** (Spring Boot + Calcite): Port 8080
- **backend** (Django REST API): Port 8000
- **frontend-dev** (Vite + React): Port 5173
- **frontend-prod** (Nginx + built React): Port 80

### 3. Wait for Services to be Healthy
```bash
docker compose ps
```

Wait until all services show `Up (healthy)` status. This may take 1-2 minutes on first run.

### 4. Verify Health Endpoints
```bash
# Backend health
curl http://localhost:8000/api/queries/health/

# Calcite server health
curl http://localhost:8080/api/calcite/health

# Frontend (dev)
curl http://localhost:5173

# Frontend (prod)
curl http://localhost:80
```

### 5. Access the Application
- **Frontend (Development)**: http://localhost:5173
- **Frontend (Production)**: http://localhost:80
- **Backend API**: http://localhost:8000/api/
- **Calcite Server**: http://localhost:8080/api/calcite/

---

## Manual Development Setup (Without Docker)

### Backend (Django)

```bash
cd backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Set environment variables
set DJANGO_SETTINGS_MODULE=config.settings  # Windows
# export DJANGO_SETTINGS_MODULE=config.settings  # Linux/Mac

# Run migrations
python manage.py migrate

# Seed the database
python seed_db_setup.py

# Start development server
python manage.py runserver 0.0.0.0:8000
```

### Calcite Server (Spring Boot)

```bash
cd calcite-server

# Build with Maven
mvn clean package -DskipTests

# Run
java -jar target/calcite-server-1.0.0-SNAPSHOT.jar
```

### Frontend (React + Vite)

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev

# Or build for production
npm run build
npm run preview
```

---

## Running Tests

### Backend Tests
```bash
cd backend
python -m pytest queries/tests/ -v
```

### Frontend Tests
```bash
cd frontend
npm test
```

### Calcite Server Tests
```bash
cd calcite-server
mvn test
```

---

## API Usage Examples

### Analyze a Query
```bash
curl -X POST http://localhost:8000/api/queries/analyze/ \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT * FROM employees WHERE department = '\''Engineering'\''",
    "schema": {
      "tables": {
        "employees": {
          "columns": {
            "id": {"type": "INTEGER"},
            "name": {"type": "VARCHAR"},
            "department": {"type": "VARCHAR"},
            "salary": {"type": "INTEGER"}
          },
          "primary_key": ["id"]
        }
      }
    },
    "dialect": "postgresql"
  }'
```

### Optimize a Query
```bash
curl -X POST http://localhost:8000/api/queries/optimize/ \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT * FROM employees WHERE department = '\''Engineering'\''",
    "schema": {
      "tables": {
        "employees": {
          "columns": {
            "id": {"type": "INTEGER"},
            "name": {"type": "VARCHAR"},
            "department": {"type": "VARCHAR"},
            "salary": {"type": "INTEGER"}
          },
          "primary_key": ["id"]
        }
      }
    },
    "dialect": "postgresql",
    "use_calcite": true
  }'
```

### Generate Query from Intent
```bash
curl -X POST http://localhost:8000/api/queries/generate/ \
  -H "Content-Type: application/json" \
  -d '{
    "intent_text": "Show me employees in Engineering department",
    "schema": {
      "tables": {
        "employees": {
          "columns": {
            "id": {"type": "INTEGER"},
            "name": {"type": "VARCHAR"},
            "department": {"type": "VARCHAR"},
            "salary": {"type": "INTEGER"}
          },
          "primary_key": ["id"]
        }
      }
    },
    "dialect": "postgresql"
  }'
```

### Direct Calcite Server Call
```bash
curl -X POST http://localhost:8080/api/calcite/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT * FROM employees WHERE department = '\''Engineering'\''",
    "schema": {
      "tables": {
        "employees": {
          "columns": {
            "id": {"type": "INTEGER"},
            "name": {"type": "VARCHAR"},
            "department": {"type": "VARCHAR"},
            "salary": {"type": "INTEGER"}
          },
          "primary_key": ["id"]
        }
      }
    },
    "dialect": "postgresql",
    "explain": true
  }'
```

---

## Project Structure

```
SQL Query Optimizer/
├── backend/                 # Django REST API
│   ├── queries/             # Main queries app
│   │   ├── services/        # Core services
│   │   │   ├── sql_parser.py      # SQL parsing with sqlglot
│   │   │   ├── validator.py       # Schema validation
│   │   │   ├── explanation.py     # Query explanations
│   │   │   ├── intent.py          # Intent extraction
│   │   │   ├── optimizer.py       # Query optimization
│   │   │   └── calcite_client.py  # Calcite HTTP client
│   │   ├── views.py         # API views
│   │   ├── serializers.py   # DRF serializers
│   │   └── models.py        # QueryHistory model
│   ├── config/              # Django settings
│   └── seed_db_setup.py     # Database seeding
├── calcite-server/          # Spring Boot + Calcite
│   └── src/main/java/.../
│       ├── service/
│       │   ├── CalciteOptimizerService.java  # Core optimization
│       │   └── CalciteSchemaBuilder.java     # Schema builder
│       └── controller/
│           └── OptimizeController.java       # REST endpoints
├── frontend/                # React + TypeScript + Vite
│   ├── src/
│   │   ├── pages/
│   │   │   ├── AnalyzePage.tsx
│   │   │   ├── OptimizePage.tsx
│   │   │   └── GeneratePage.tsx
│   │   └── components/
│   └── package.json
├── docker-compose.yml       # Multi-container orchestration
└── RUNNING_LOCALLY.md       # This file
```

---

## Troubleshooting

### Services Won't Start
```bash
# Check logs
docker compose logs backend
docker compose logs calcite-server
docker compose logs frontend-dev

# Rebuild images
docker compose build --no-cache
docker compose up -d
```

### Database Connection Issues
```bash
# Check seed DB is running
docker compose logs seed-db

# Manually seed database
docker exec -it sql_optimizer_backend python seed_db_setup.py
```

### Calcite Server Build Fails
```bash
cd calcite-server
mvn clean package -DskipTests -X
# Check for Java 17 and Maven 3.9+
java -version
mvn -version
```

### Port Conflicts
- Backend: 8000
- Calcite: 8080
- Frontend Dev: 5173
- Frontend Prod: 80
- PostgreSQL: 5432

Change ports in `docker-compose.yml` if needed.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CALCITE_SERVER_URL` | `http://localhost:8080` | Calcite server URL for backend |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/seed_db` | Seed DB connection |
| `ANTHROPIC_API_KEY` | (empty) | Optional LLM API key for intent extraction |
| `DJANGO_SECRET_KEY` | (generated) | Django secret key |

---

## Features

✅ **SQL Analysis**: Parse, validate, and explain queries  
✅ **Syntax Error Display**: Detailed error messages with location pointers  
✅ **Query Optimization**: Apache Calcite + built-in rule-based optimizer  
✅ **Query Generation**: Natural language → SQL with intent extraction  
✅ **Schema Validation**: Table/column validation against provided schema  
✅ **EXPLAIN Plan Integration**: PostgreSQL cost estimates  
✅ **History Tracking**: Query history with timestamps  
✅ **Multiple Dialects**: PostgreSQL, MySQL, Oracle, SQL Server  

---

## License

Internal project - SQL Query Optimizer