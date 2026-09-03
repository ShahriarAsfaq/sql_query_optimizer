package com.sqloptimizer.calcite.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

import java.util.Map;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class SchemaDefinition {
    @JsonProperty("tables")
    private Map<String, TableDefinition> tables;

    @JsonProperty("relationships")
    private java.util.List<RelationshipDefinition> relationships;

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class TableDefinition {
        @JsonProperty("columns")
        private Map<String, ColumnDefinition> columns;

        @JsonProperty("primary_key")
        private java.util.List<String> primaryKey;

        @JsonProperty("indexes")
        private java.util.List<IndexDefinition> indexes;
    }

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class ColumnDefinition {
        @JsonProperty("type")
        private String type;

        @JsonProperty("nullable")
        private boolean nullable = true;

        @JsonProperty("default_value")
        private Object defaultValue;
    }

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class IndexDefinition {
        @JsonProperty("name")
        private String name;

        @JsonProperty("columns")
        private java.util.List<String> columns;

        @JsonProperty("unique")
        private boolean unique = false;
    }

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class RelationshipDefinition {
        @JsonProperty("name")
        private String name;

        @JsonProperty("from_table")
        private String fromTable;

        @JsonProperty("from_column")
        private String fromColumn;

        @JsonProperty("to_table")
        private String toTable;

        @JsonProperty("to_column")
        private String toColumn;

        @JsonProperty("type")
        private String type = "MANY_TO_ONE"; // MANY_TO_ONE, ONE_TO_ONE, ONE_TO_MANY
    }
}