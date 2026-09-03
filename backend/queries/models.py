"""
Models for the queries app.
"""
from django.db import models
from django.conf import settings


class QueryHistory(models.Model):
    """Store history of analyzed queries."""
    sql = models.TextField()
    intent_text = models.TextField(blank=True, default='')
    operation_type = models.CharField(max_length=50)
    parsed_structure = models.JSONField(default=dict)
    validation_result = models.JSONField(default=dict, blank=True)
    intent_match = models.JSONField(default=dict, blank=True, null=True)
    explanation = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Optional: link to user if auth is added later
    # user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['operation_type']),
        ]

    def __str__(self):
        return f"{self.operation_type} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"