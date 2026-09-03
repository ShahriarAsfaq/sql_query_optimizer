"""
URL patterns for the schema engine API.
"""
from django.urls import path
from .views import (
    SchemaDefinitionView,
    SchemaDetailView,
    SeedSchemaView,
    SeedDataSummaryView,
    ValidateSchemaView,
)

urlpatterns = [
    path('', SchemaDefinitionView.as_view(), name='schema-list'),
    path('<int:pk>/', SchemaDetailView.as_view(), name='schema-detail'),
    path('seed/', SeedSchemaView.as_view(), name='seed-schema'),
    path('seed/summary/', SeedDataSummaryView.as_view(), name='seed-summary'),
    path('validate/', ValidateSchemaView.as_view(), name='validate-schema'),
]