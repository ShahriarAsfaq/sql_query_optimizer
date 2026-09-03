"""
Calcite Client - HTTP client for Apache Calcite optimization microservice.
"""
import logging
import os
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import httpx

logger = logging.getLogger(__name__)


@dataclass
class CalciteOptimizeResult:
    """Result from Calcite optimization."""
    original_sql: str
    optimized_sql: str
    original_cost: Optional[float]
    optimized_cost: Optional[float]
    rules_applied: List[str]
    explain_plan: Optional[Dict[str, Any]]
    valid: bool
    error: Optional[str] = None


class CalciteClient:
    """
    HTTP client for communicating with the Calcite optimization server.

    The server is a Spring Boot application that uses Apache Calcite's
    HepPlanner for rule-based SQL optimization.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        enabled: bool = True
    ):
        """
        Initialize Calcite client.

        Args:
            base_url: Base URL of the Calcite server (e.g., "http://localhost:8080")
            timeout: Request timeout in seconds
            enabled: Whether to use Calcite (can be disabled for fallback)
        """
        self.base_url = base_url or os.getenv('CALCITE_SERVER_URL', 'http://localhost:8080')
        self.timeout = timeout
        self.enabled = enabled
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def is_available(self) -> bool:
        """Check if Calcite server is available."""
        if not self.enabled:
            return False
        try:
            response = self._get_client().get(f"{self.base_url}/api/calcite/health", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Calcite server not available: {e}")
            return False

    def optimize(
        self,
        sql: str,
        schema: Optional[Dict[str, Any]] = None,
        dialect: str = "postgresql",
        explain: bool = False
    ) -> CalciteOptimizeResult:
        """
        Optimize a SQL query using Calcite.

        Args:
            sql: SQL query to optimize
            schema: Database schema definition
            dialect: SQL dialect (postgresql, mysql, sqlite, oracle, sqlserver)
            explain: Whether to include EXPLAIN plan in response

        Returns:
            CalciteOptimizeResult with optimized query and metadata
        """
        if not self.enabled:
            return CalciteOptimizeResult(
                original_sql=sql,
                optimized_sql=sql,
                original_cost=None,
                optimized_cost=None,
                rules_applied=[],
                explain_plan=None,
                valid=False,
                error="Calcite client disabled"
            )

        payload = {
            "sql": sql,
            "dialect": dialect,
            "explain": explain
        }
        if schema:
            payload["schema"] = schema

        try:
            response = self._get_client().post(
                f"{self.base_url}/api/calcite/optimize",
                json=payload
            )
            response.raise_for_status()
            data = response.json()

            return CalciteOptimizeResult(
                original_sql=data.get("original_sql", sql),
                optimized_sql=data.get("optimized_sql", sql),
                original_cost=data.get("original_cost"),
                optimized_cost=data.get("optimized_cost"),
                rules_applied=data.get("rules_applied", []),
                explain_plan=data.get("explain_plan"),
                valid=data.get("valid", True),
                error=data.get("error")
            )

        except httpx.TimeoutException:
            logger.warning("Calcite optimization timed out")
            return CalciteOptimizeResult(
                original_sql=sql,
                optimized_sql=sql,
                original_cost=None,
                optimized_cost=None,
                rules_applied=[],
                explain_plan=None,
                valid=False,
                error="Calcite server timeout"
            )
        except httpx.HTTPStatusError as e:
            logger.warning(f"Calcite optimization failed: {e.response.status_code}")
            return CalciteOptimizeResult(
                original_sql=sql,
                optimized_sql=sql,
                original_cost=None,
                optimized_cost=None,
                rules_applied=[],
                explain_plan=None,
                valid=False,
                error=f"HTTP {e.response.status_code}: {e.response.text}"
            )
        except Exception as e:
            logger.error(f"Calcite optimization error: {e}")
            return CalciteOptimizeResult(
                original_sql=sql,
                optimized_sql=sql,
                original_cost=None,
                optimized_cost=None,
                rules_applied=[],
                explain_plan=None,
                valid=False,
                error=str(e)
            )

    def validate(self, sql: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Validate a SQL query against schema using Calcite.

        Args:
            sql: SQL query to validate
            schema: Database schema definition

        Returns:
            Dict with 'valid' (bool) and 'error' (str or None)
        """
        if not self.enabled:
            return {"valid": True, "error": None}

        payload = {"sql": sql}
        if schema:
            payload["schema"] = schema

        try:
            response = self._get_client().post(
                f"{self.base_url}/api/calcite/validate",
                json=payload
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Calcite validation failed: {e}")
            return {"valid": False, "error": str(e)}