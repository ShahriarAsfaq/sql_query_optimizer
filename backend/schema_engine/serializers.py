"""
Serializers for the schema engine API.
"""
from rest_framework import serializers
from .models import SchemaDefinition


class SchemaDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchemaDefinition
        fields = ['id', 'name', 'description', 'schema_json', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class SchemaTableSerializer(serializers.Serializer):
    name = serializers.CharField()
    columns = serializers.DictField(child=serializers.DictField())
    primary_key = serializers.ListField(child=serializers.CharField(), required=False)
    indexes = serializers.ListField(child=serializers.DictField(), required=False)


class SchemaRelationshipSerializer(serializers.Serializer):
    from_table = serializers.CharField()
    from_column = serializers.CharField()
    to_table = serializers.CharField()
    to_column = serializers.CharField()
    type = serializers.CharField(default='many_to_one')  # many_to_one, one_to_one, many_to_many


class FullSchemaSerializer(serializers.Serializer):
    tables = SchemaTableSerializer(many=True)
    relationships = SchemaRelationshipSerializer(many=True, required=False)


class SeedDataSummarySerializer(serializers.Serializer):
    table = serializers.CharField()
    row_count = serializers.IntegerField()
    columns = serializers.ListField(child=serializers.CharField())