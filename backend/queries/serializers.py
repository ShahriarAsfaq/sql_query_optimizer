"""
Serializers for the queries API.
"""
from rest_framework import serializers
from typing import Dict, Any, List, Optional


class ParseErrorSerializer(serializers.Serializer):
    message = serializers.CharField()
    line = serializers.IntegerField(allow_null=True, required=False)
    column = serializers.IntegerField(allow_null=True, required=False)
    context = serializers.CharField(allow_null=True, required=False)


class TableReferenceSerializer(serializers.Serializer):
    name = serializers.CharField()
    alias = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    schema = serializers.CharField(allow_null=True, required=False)


class ColumnReferenceSerializer(serializers.Serializer):
    name = serializers.CharField()
    table = serializers.CharField(allow_null=True, required=False)
    alias = serializers.CharField(allow_null=True, required=False)


class JoinInfoSerializer(serializers.Serializer):
    type = serializers.CharField()
    table = TableReferenceSerializer()
    condition = serializers.CharField(allow_null=True, required=False)


class WhereConditionSerializer(serializers.Serializer):
    column = serializers.CharField()
    operator = serializers.CharField()
    value = serializers.JSONField(allow_null=True)
    table = serializers.CharField(allow_null=True, required=False)


class OrderBySerializer(serializers.Serializer):
    column = serializers.CharField()
    direction = serializers.CharField()


class ParsedQuerySerializer(serializers.Serializer):
    operation_type = serializers.CharField()
    tables = TableReferenceSerializer(many=True)
    columns = ColumnReferenceSerializer(many=True)
    joins = JoinInfoSerializer(many=True)
    where_conditions = WhereConditionSerializer(many=True)
    group_by = serializers.ListField(child=serializers.CharField())
    having_conditions = serializers.ListField(child=serializers.CharField())
    order_by = OrderBySerializer(many=True)
    limit = serializers.IntegerField(allow_null=True)
    window_functions = serializers.ListField(child=serializers.CharField())
    aggregations = serializers.ListField(child=serializers.CharField())
    subqueries = serializers.ListField(child=serializers.CharField())
    cte_names = serializers.ListField(child=serializers.CharField())
    is_valid = serializers.BooleanField()
    errors = ParseErrorSerializer(many=True)
    raw_sql = serializers.CharField()
    dialect = serializers.CharField()


class AnalyzeRequestSerializer(serializers.Serializer):
    sql = serializers.CharField()
    schema = serializers.JSONField(required=False, allow_null=True)
    intent_text = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    explain = serializers.BooleanField(required=False, default=False)


class AnalyzeResponseSerializer(serializers.Serializer):
    parsed_query = ParsedQuerySerializer()
    validation = serializers.JSONField()
    intent_match = serializers.JSONField(required=False, allow_null=True)
    explanation = serializers.CharField(required=False, allow_null=True)
    history_id = serializers.IntegerField(required=False, allow_null=True)
    complexity_score = serializers.FloatField(required=False, allow_null=True)
    explain_plan = serializers.CharField(required=False, allow_null=True)


class OptimizeRequestSerializer(serializers.Serializer):
    sql = serializers.CharField()
    schema = serializers.JSONField(required=False, allow_null=True)
    use_calcite = serializers.BooleanField(required=False, default=True)


class CandidateQuerySerializer(serializers.Serializer):
    sql = serializers.CharField()
    description = serializers.CharField()
    cost = serializers.FloatField(allow_null=True)
    startup_cost = serializers.FloatField(allow_null=True)
    plan_rows = serializers.FloatField(allow_null=True)
    plan_width = serializers.IntegerField(allow_null=True)
    complexity_score = serializers.FloatField(allow_null=True)
    validation_passed = serializers.BooleanField()
    validation_errors = serializers.ListField(child=serializers.CharField(), required=False)
    cost_source = serializers.CharField(allow_null=True, required=False)
    semantic_safety = serializers.CharField(allow_null=True, required=False)
    semantic_details = serializers.ListField(child=serializers.DictField(), required=False)
    optimization_reasons = serializers.ListField(child=serializers.CharField(), required=False)
    confidence = serializers.CharField(allow_null=True, required=False)
    performance_score = serializers.FloatField(allow_null=True)
    cost_change_percent = serializers.FloatField(allow_null=True)
    rewrite_rules_applied = serializers.ListField(child=serializers.CharField(), required=False)
    plan_analysis = serializers.DictField(allow_null=True, required=False)


class OptimizeResponseSerializer(serializers.Serializer):
    original_sql = serializers.CharField()
    original_cost = serializers.FloatField(allow_null=True)
    original_plan_analysis = serializers.DictField(allow_null=True, required=False)
    candidates = CandidateQuerySerializer(many=True)
    best_candidate = CandidateQuerySerializer(allow_null=True)


class GenerateRequestSerializer(serializers.Serializer):
    intent_text = serializers.CharField()
    schema = serializers.JSONField(required=False, allow_null=True)
    answers = serializers.JSONField(required=False, default=dict)


class GenerateResponseSerializer(serializers.Serializer):
    # For success response
    intent = serializers.JSONField(required=False, allow_null=True)
    candidates = CandidateQuerySerializer(many=True, required=False)
    # For needs_clarification response
    status = serializers.ChoiceField(choices=['success', 'needs_clarification'], required=False)
    questions = serializers.ListField(child=serializers.DictField(), required=False)
    partial_intent = serializers.JSONField(required=False, allow_null=True)
    sql = serializers.CharField(required=False, allow_null=True)
    explanation = serializers.CharField(required=False, allow_null=True)
    confidence = serializers.FloatField(required=False, allow_null=True)
    warnings = serializers.ListField(child=serializers.CharField(), required=False)


class HistoryItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sql = serializers.CharField()
    intent_text = serializers.CharField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()
    operation_type = serializers.CharField()


class SchemaDefinitionSerializer(serializers.Serializer):
    tables = serializers.DictField(
        child=serializers.DictField(
            child=serializers.JSONField()
        )
    )
    relationships = serializers.ListField(
        child=serializers.DictField(),
        required=False
    )