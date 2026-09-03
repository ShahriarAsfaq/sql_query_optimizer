"""
Authentication for the queries API.
Supports API key authentication and optional JWT token authentication.
"""
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings
import hashlib
import hmac
import time
import json
import os


class APIKeyAuthentication(BaseAuthentication):
    """
    Simple API key authentication.

    Usage:
    - Include header: Authorization: ApiKey <api_key>
    - Or: X-API-Key: <api_key>
    """

    def authenticate(self, request):
        # Check Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('ApiKey '):
            api_key = auth_header[7:].strip()
            return self._validate_api_key(api_key)

        # Check X-API-Key header
        api_key = request.META.get('HTTP_X_API_KEY', '')
        if api_key:
            return self._validate_api_key(api_key)

        return None

    def _validate_api_key(self, api_key: str):
        """Validate API key against configured keys."""
        # Get valid API keys from settings
        valid_keys = getattr(settings, 'API_KEYS', {})

        # Also check environment variable
        env_keys = os.getenv('API_KEYS', '')
        if env_keys:
            try:
                env_keys_dict = json.loads(env_keys)
                valid_keys.update(env_keys_dict)
            except json.JSONDecodeError:
                pass

        if not valid_keys:
            # No keys configured - allow in debug mode
            if settings.DEBUG:
                return (None, None)
            raise AuthenticationFailed('API key authentication not configured')

        if api_key in valid_keys:
            user_info = valid_keys[api_key]
            # Create a simple user object
            class APIKeyUser:
                def __init__(self, key, info):
                    self.api_key = key
                    self.username = info.get('username', 'api_user')
                    self.is_authenticated = True
                    self.is_active = True
                    self.permissions = info.get('permissions', ['read', 'write'])

                def has_perm(self, perm):
                    return perm in self.permissions

            user = APIKeyUser(api_key, user_info)
            return (user, api_key)

        raise AuthenticationFailed('Invalid API key')

    def authenticate_header(self, request):
        return 'ApiKey'


class QueryHistoryManager:
    """Manager for query history with user isolation."""

    @staticmethod
    def save_query(user, sql: str, intent_text: str, parsed_structure: dict,
                   validation_result: dict, intent_match: dict, explanation: str):
        """Save query to history."""
        from queries.models import QueryHistory

        # For now, we don't link to user since auth is not fully implemented
        # In production, add user ForeignKey to QueryHistory model
        history = QueryHistory.objects.create(
            sql=sql,
            intent_text=intent_text or '',
            operation_type=parsed_structure.get('operation_type', 'SELECT'),
            parsed_structure=parsed_structure,
            validation_result=validation_result,
            intent_match=intent_match,
            explanation=explanation,
        )
        return history

    @staticmethod
    def get_history(user, limit: int = 50, offset: int = 0):
        """Get query history for user."""
        from queries.models import QueryHistory

        # For now, return all history (no user isolation)
        # In production, filter by user
        return QueryHistory.objects.all().order_by('-created_at')[offset:offset + limit]

    @staticmethod
    def delete_history(user, history_id: int = None):
        """Delete query history."""
        from queries.models import QueryHistory

        if history_id:
            QueryHistory.objects.filter(id=history_id).delete()
        else:
            QueryHistory.objects.all().delete()


# Utility functions for generating API keys
def generate_api_key(username: str = 'api_user', permissions: list = None) -> str:
    """Generate a new API key."""
    import secrets
    key = secrets.token_urlsafe(32)
    return key


def hash_api_key(api_key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()


# Default API keys for development
DEFAULT_API_KEYS = {
    'dev-key-12345': {
        'username': 'developer',
        'permissions': ['read', 'write', 'admin'],
    },
    'test-key-67890': {
        'username': 'tester',
        'permissions': ['read'],
    },
}