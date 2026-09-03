"""
LLM Client - interface for multiple LLM providers (Anthropic, Gemini), isolated for testing.
"""
from typing import Optional, Dict, Any
import logging
import os
import json

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Wrapper around LLM APIs for NL->intent and explanation polishing.
    Supports multiple providers: Anthropic (Claude), Google Gemini.
    Automatically detects which provider to use based on available API keys.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, provider: Optional[str] = None):
        """
        Initialize LLM client.

        Args:
            api_key: API key (defaults to env var based on provider)
            model: Model to use (defaults to env var based on provider)
            provider: Explicit provider ('anthropic', 'gemini', 'auto'). Defaults to 'auto'
        """
        self.provider = provider or self._detect_provider()
        self.api_key = api_key or self._get_api_key_from_env()
        self.model = model or self._get_model_from_env()
        self._client = None

        if self.api_key:
            self._init_client()

    def _detect_provider(self) -> str:
        """Auto-detect provider based on available environment variables."""
        # Check for Anthropic
        if os.getenv('ANTHROPIC_API_KEY') or os.getenv('ANTHROPIC_AUTH_TOKEN'):
            return 'anthropic'
        # Check for Gemini
        if os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY'):
            return 'gemini'
        # Default to anthropic if nothing found
        return 'anthropic'

    def _get_api_key_from_env(self) -> Optional[str]:
        """Get API key from environment based on provider."""
        if self.provider == 'anthropic':
            return os.getenv('ANTHROPIC_API_KEY') or os.getenv('ANTHROPIC_AUTH_TOKEN')
        elif self.provider == 'gemini':
            return os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
        return None

    def _get_model_from_env(self) -> str:
        """Get model name from environment based on provider."""
        if self.provider == 'anthropic':
            return os.getenv('ANTHROPIC_MODEL', 'claude-3-5-sonnet-20241022')
        elif self.provider == 'gemini':
            return os.getenv('GEMINI_MODEL', 'gemini-1.5-pro')
        return 'claude-3-5-sonnet-20241022'

    def _init_client(self):
        """Initialize the appropriate LLM client lazily."""
        try:
            if self.provider == 'anthropic':
                self._init_anthropic()
            elif self.provider == 'gemini':
                self._init_gemini()
        except ImportError as e:
            logger.warning(f"{self.provider.capitalize()} package not installed, LLM features disabled: {e}")
            self._client = None
        except Exception as e:
            logger.warning(f"Failed to initialize {self.provider} client: {e}")
            self._client = None

    def _init_anthropic(self):
        """Initialize Anthropic client."""
        import anthropic
        self._client = anthropic.Anthropic(api_key=self.api_key)

    def _init_gemini(self):
        """Initialize Gemini client."""
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        self._client = genai.GenerativeModel(self.model)

    def is_available(self) -> bool:
        """Check if LLM client is available."""
        return self._client is not None

    def complete(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.1) -> str:
        """
        Get a completion from the LLM.

        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature

        Returns:
            Response text

        Raises:
            Exception if client not available or API call fails
        """
        if not self._client:
            raise Exception(f"LLM client not available (missing API key or {self.provider} package)")

        try:
            if self.provider == 'anthropic':
                return self._complete_anthropic(prompt, max_tokens, temperature)
            elif self.provider == 'gemini':
                return self._complete_gemini(prompt, max_tokens, temperature)
        except Exception as e:
            logger.error(f"LLM completion failed: {e}")
            raise

    def _complete_anthropic(self, prompt: str, max_tokens: int, temperature: float) -> str:
        """Complete using Anthropic API."""
        # Anthropic SDK doesn't accept temperature in messages.create()
        # Temperature should be passed as a separate parameter if supported
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return response.content[0].text

    def _complete_gemini(self, prompt: str, max_tokens: int, temperature: float) -> str:
        """Complete using Gemini API."""
        generation_config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        response = self._client.generate_content(
            prompt,
            generation_config=generation_config
        )
        return response.text

    def extract_intent(self, intent_text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract structured intent from natural language using LLM.

        Args:
            intent_text: Natural language description
            schema: Database schema

        Returns:
            Structured intent dictionary
        """
        schema_summary = self._summarize_schema(schema)

        prompt = f"""Extract structured intent from this natural language query.

Schema:
{schema_summary}

User request: "{intent_text}"

Return JSON with these fields:
- category: one of RETRIEVE, FILTER, SORT, AGGREGATE, GROUP, TOP_N, JOIN, TREND, RANKING, DUPLICATE_DETECTION, COMPARISON, UNKNOWN
- operation: SELECT, COUNT, AVG, SUM, MIN, MAX, etc.
- entity: main table name from schema
- metric: column to aggregate (if any)
- filters: list of {{column, operator, value}}
- time_range: {{start, end, column}} if date filtering
- group_by: list of columns
- order_by: list of {{column, direction}} (ASC/DESC)
- limit: integer if top N
- ranking: RANK, DENSE_RANK, ROW_NUMBER if ranking
- comparison: {{type, entities}} if comparison

Return ONLY valid JSON."""

        response = self.complete(prompt, max_tokens=800, temperature=0.1)
        return self._parse_json_response(response)

    def polish_explanation(self, explanation: str, parsed_info: Dict[str, Any]) -> str:
        """
        Polish an explanation using LLM.

        Args:
            explanation: Raw rule-based explanation
            parsed_info: Parsed query info for context

        Returns:
            Polished explanation
        """
        prompt = f"""Polish this SQL query explanation to be more natural and readable.
Keep all technical details accurate. Don't add or remove information.

Explanation:
{explanation}

Query type: {parsed_info.get('operation_type', 'SELECT')}
Tables: {', '.join([t.get('name', '') for t in parsed_info.get('tables', [])])}

Return only the polished explanation."""

        response = self.complete(prompt, max_tokens=500, temperature=0.2)
        return response.strip()

    def _summarize_schema(self, schema: Dict[str, Any]) -> str:
        """Create a brief schema summary for LLM context."""
        lines = []
        for table_name, table_def in schema.get('tables', {}).items():
            cols = list(table_def.get('columns', {}).keys())
            lines.append(f"  {table_name}: {', '.join(cols)}")
        return "\n".join(lines)

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """Parse JSON from LLM response, handling markdown code blocks."""

        json_str = response
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            json_str = response.split('```')[1].split('```')[0]

        try:
            return json.loads(json_str.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}")
            logger.error(f"Response was: {response[:500]}")
            raise


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, responses: Optional[Dict[str, str]] = None):
        self.responses = responses or {}
        self.call_log = []

    def is_available(self) -> bool:
        return True

    def complete(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.1) -> str:
        self.call_log.append({'prompt': prompt, 'max_tokens': max_tokens, 'temperature': temperature})

        # Check for predefined responses based on prompt content
        for key, response in self.responses.items():
            if key in prompt:
                return response

        # Smart default responses for common prompts - parse the prompt to extract context
        if 'Extract structured intent' in prompt:
            # Extract the user request from the prompt
            import re
            request_match = re.search(r'User request: "([^"]+)"', prompt)
            user_request = request_match.group(1) if request_match else ""

            # Generate appropriate mock response based on request
            return self._generate_intent_response(user_request)

        if 'Polish this SQL query explanation' in prompt:
            return "This query retrieves the top 10 highest-paid employees from the employees table, ordered by salary in descending order."

        if 'Generate a natural language description of what this SQL query is trying to achieve' in prompt:
            # Extract query type from prompt
            import re

            # Extract parsed structure from prompt
            operation_match = re.search(r'Operation: (\w+)', prompt)
            operation = operation_match.group(1) if operation_match else ''

            tables_match = re.search(r'Tables: (\[.*?\])', prompt)
            tables = tables_match.group(1).lower() if tables_match else ''

            joins_match = re.search(r'Joins: (\[.*?\])', prompt)
            joins = joins_match.group(1).lower() if joins_match else ''

            where_match = re.search(r'WHERE conditions: (\[.*?\])', prompt)
            where = where_match.group(1).lower() if where_match else ''

            group_by_match = re.search(r'GROUP BY: (\[.*?\])', prompt)
            group_by = group_by_match.group(1).lower() if group_by_match else ''

            order_by_match = re.search(r'ORDER BY: (\[.*?\])', prompt)
            order_by = order_by_match.group(1).lower() if order_by_match else ''

            limit_match = re.search(r'LIMIT: (\d+|None)', prompt)
            limit = limit_match.group(1) if limit_match else ''

            aggregations_match = re.search(r'Aggregations: (\[.*?\])', prompt)
            aggregations = aggregations_match.group(1).lower() if aggregations_match else ''

            prompt_lower = prompt.lower()

            # TOP_N with aggregate (e.g., top 5 departments by average salary)
            if 'top_n' in prompt_lower or ('top' in prompt_lower and limit and limit != 'None'):
                if 'avg' in aggregations or 'average' in aggregations:
                    if 'department' in tables:
                        return "Find the top departments by average salary"
                if 'salary' in prompt_lower or 'highest' in prompt_lower:
                    return "Find the top highest-paid employees"
                return "Find the top records based on the specified criteria"

            # AGGREGATE with GROUP BY
            if 'aggregate' in prompt_lower or 'avg' in aggregations or 'sum' in aggregations:
                if 'salary' in prompt_lower and 'department' in tables:
                    return "Calculate the average salary for each department"
                if 'salary' in prompt_lower:
                    return "Calculate salary statistics"
                return "Compute aggregate statistics"

            # JOIN queries - check tables and joins specifically, not whole prompt
            # Only treat as JOIN if there's actually a JOIN in the parsed query
            has_join = 'inner join' in joins or 'left join' in joins or 'right join' in joins or 'join' in joins
            if has_join:
                # Check tables in the query, not in schema
                if 'customers' in tables and ('purchase' in tables or 'order' in tables):
                    return "Show customers with their purchases"
                elif 'employees' in tables and 'departments' in tables:
                    return "Show employees with their department information"
                elif 'employees' in tables and 'employees' in joins:
                    return "Show employees with their managers"
                return "Combine data from multiple related tables"

            # FILTER queries
            if 'filter' in prompt_lower or (where and where != '[]'):
                if 'new york' in prompt_lower or 'city' in where or 'new york' in where:
                    return "Show customers from New York"
                if 'salary' in prompt_lower and ('>' in where or 'gt' in where or 'gt' in prompt_lower):
                    return "Find employees with salary above a threshold"
                return "Filter records based on specified conditions"

            # Simple RETRIEVE
            return "Retrieve data from the database"

        return "Mock LLM response"

    def _generate_intent_response(self, user_request: str) -> str:
        """Generate a mock intent response based on the user request."""
        text_lower = user_request.lower()

        # Default response
        response = {
            'category': 'RETRIEVE',
            'operation': 'SELECT',
            'entity': 'employees',
            'metric': None,
            'filters': [],
            'group_by': [],
            'order_by': [],
            'limit': None,
            'ranking': None,
            'comparison': None
        }

        # TOP_N patterns
        if 'top' in text_lower and any(c.isdigit() for c in text_lower):
            import re
            limit_match = re.search(r'top\s+(\d+)', text_lower)
            limit = int(limit_match.group(1)) if limit_match else 10
            response['category'] = 'TOP_N'
            response['limit'] = limit

            if 'average' in text_lower or 'avg' in text_lower:
                response['operation'] = 'AVG'
                if 'salary' in text_lower:
                    response['metric'] = 'salary'
                if 'department' in text_lower:
                    response['entity'] = 'employees'
                    response['group_by'] = ['department']
                response['order_by'] = [{'column': 'avg_salary', 'direction': 'DESC'}]
            elif 'highest paid' in text_lower or 'salary' in text_lower:
                response['operation'] = 'SELECT'
                response['metric'] = 'salary'
                response['entity'] = 'employees'
                response['order_by'] = [{'column': 'salary', 'direction': 'DESC'}]
            elif 'revenue' in text_lower or 'sales' in text_lower:
                response['operation'] = 'SUM'
                response['metric'] = 'total_amount'
                response['entity'] = 'purchases'
                if 'category' in text_lower or 'product' in text_lower:
                    response['group_by'] = ['category']
                response['order_by'] = [{'column': 'sum_total_amount', 'direction': 'DESC'}]

        # AGGREGATE patterns
        elif any(kw in text_lower for kw in ['average', 'avg', 'sum', 'total', 'count']):
            response['category'] = 'AGGREGATE'
            if 'average' in text_lower or 'avg' in text_lower:
                response['operation'] = 'AVG'
            elif 'sum' in text_lower or 'total' in text_lower:
                response['operation'] = 'SUM'
            elif 'count' in text_lower:
                response['operation'] = 'COUNT'

            if 'salary' in text_lower:
                response['metric'] = 'salary'
                response['entity'] = 'employees'
            elif 'purchase' in text_lower or 'amount' in text_lower:
                response['metric'] = 'total_amount'
                response['entity'] = 'purchases'

            if 'per customer' in text_lower or 'by customer' in text_lower:
                response['group_by'] = ['customer_id']
            elif 'by category' in text_lower or 'by product category' in text_lower:
                response['group_by'] = ['category']

        # FILTER patterns
        elif 'from new york' in text_lower or 'where city' in text_lower:
            response['category'] = 'FILTER'
            response['entity'] = 'customers'
            response['filters'] = [{'column': 'city', 'operator': '=', 'value': 'New York'}]

        elif 'earn more than' in text_lower or 'salary >' in text_lower:
            response['category'] = 'FILTER'
            response['entity'] = 'employees'
            import re
            val_match = re.search(r'(\d+)', text_lower)
            val = int(val_match.group(1)) if val_match else 100000
            response['filters'] = [{'column': 'salary', 'operator': '>', 'value': val}]

        # JOIN patterns
        elif 'with their' in text_lower or 'and their' in text_lower:
            response['category'] = 'JOIN'
            if 'department' in text_lower:
                response['entity'] = 'employees'
                response['join_tables'] = ['departments']
            elif 'order' in text_lower or 'purchase' in text_lower:
                response['entity'] = 'customers'
                response['join_tables'] = ['purchases']
            elif 'manager' in text_lower:
                response['entity'] = 'employees'
                response['join_tables'] = ['employees_self']

        import json
        return json.dumps(response, indent=2)

    def extract_intent(self, intent_text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        self.call_log.append({'method': 'extract_intent', 'intent_text': intent_text})
        # Use the same logic as complete()
        response_json = self._generate_intent_response(intent_text)
        import json
        result = json.loads(response_json)
        result['raw_text'] = intent_text
        return result

    def polish_explanation(self, explanation: str, parsed_info: Dict[str, Any]) -> str:
        self.call_log.append({'method': 'polish_explanation', 'explanation': explanation})
        return explanation  # Return as-is for mock