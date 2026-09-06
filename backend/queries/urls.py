"""
URL patterns for the queries API.
"""
from django.urls import path
from .views import (
    AnalyzeQueryView,
    OptimizeQueryView,
    GenerateQueryView,
    GenerateSQLView,
    health_check,
)

urlpatterns = [
    path('analyze/', AnalyzeQueryView.as_view(), name='analyze-query'),
    path('optimize/', OptimizeQueryView.as_view(), name='optimize-query'),
    path('generate/', GenerateQueryView.as_view(), name='generate-query'),
    path('generate-sql/', GenerateSQLView.as_view(), name='generate-sql'),
    path('health/', health_check, name='health-check'),
]