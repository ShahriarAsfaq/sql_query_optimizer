package com.sqloptimizer.calcite.service;

import com.sqloptimizer.calcite.dto.SchemaDefinition;
import org.apache.calcite.plan.RelOptUtil;
import org.apache.calcite.rel.type.RelDataType;
import org.apache.calcite.rel.type.RelDataTypeFactory;
import org.apache.calcite.schema.Schema;
import org.apache.calcite.schema.SchemaPlus;
import org.apache.calcite.schema.Table;
import org.apache.calcite.schema.impl.AbstractSchema;
import org.apache.calcite.schema.impl.AbstractTable;
import org.apache.calcite.schema.Statistic;
import org.apache.calcite.schema.Statistics;
import org.apache.calcite.sql.type.SqlTypeName;
import org.apache.calcite.tools.Frameworks;
import org.springframework.stereotype.Component;

import java.util.*;

@Component
public class CalciteSchemaBuilder {

    public SchemaPlus buildSchema(SchemaDefinition schemaDef) {
        // In Calcite 1.36, Frameworks.createRootSchema(false) creates a proper root schema
        SchemaPlus rootSchema = Frameworks.createRootSchema(false);

        if (schemaDef == null || schemaDef.getTables() == null) {
            return rootSchema;
        }

        Map<String, SchemaDefinition.TableDefinition> tables = schemaDef.getTables();

        // Build tables
        for (Map.Entry<String, SchemaDefinition.TableDefinition> entry : tables.entrySet()) {
            String tableName = entry.getKey();
            SchemaDefinition.TableDefinition tableDef = entry.getValue();

            Table table = buildTable(tableDef);
            rootSchema.add(tableName, table);
        }

        // Add foreign key relationships as constraints (for join optimization)
        if (schemaDef.getRelationships() != null) {
            addRelationships(rootSchema, schemaDef.getRelationships());
        }

        return rootSchema;
    }

    private Table buildTable(SchemaDefinition.TableDefinition tableDef) {
        return new AbstractTable() {
            @Override
            public RelDataType getRowType(RelDataTypeFactory typeFactory) {
                RelDataTypeFactory.Builder builder = typeFactory.builder();

                if (tableDef.getColumns() != null) {
                    for (Map.Entry<String, SchemaDefinition.ColumnDefinition> colEntry : tableDef.getColumns().entrySet()) {
                        String colName = colEntry.getKey();
                        SchemaDefinition.ColumnDefinition colDef = colEntry.getValue();

                        SqlTypeName sqlType = parseSqlType(colDef.getType());
                        RelDataType colType = typeFactory.createSqlType(sqlType);

                        if (!colDef.isNullable()) {
                            colType = typeFactory.createTypeWithNullability(colType, false);
                        }

                        builder.add(colName, colType);
                    }
                }

                return builder.build();
            }

            @Override
            public Statistic getStatistic() {
                // Provide basic statistics for cost-based optimization
                // Statistics.of with just row count
                return Statistics.of(10000, Collections.emptyList());
            }
        };
    }

    private SqlTypeName parseSqlType(String type) {
        if (type == null) return SqlTypeName.VARCHAR;

        String upper = type.toUpperCase();
        if (upper.contains("INT")) return SqlTypeName.INTEGER;
        if (upper.contains("BIGINT")) return SqlTypeName.BIGINT;
        if (upper.contains("SMALLINT")) return SqlTypeName.SMALLINT;
        if (upper.contains("DOUBLE") || upper.contains("FLOAT") || upper.contains("REAL")) return SqlTypeName.DOUBLE;
        if (upper.contains("DECIMAL") || upper.contains("NUMERIC")) return SqlTypeName.DECIMAL;
        if (upper.contains("BOOLEAN") || upper.contains("BOOL")) return SqlTypeName.BOOLEAN;
        if (upper.contains("DATE")) return SqlTypeName.DATE;
        if (upper.contains("TIMESTAMP")) return SqlTypeName.TIMESTAMP;
        if (upper.contains("TIME")) return SqlTypeName.TIME;
        if (upper.contains("JSON")) return SqlTypeName.VARCHAR; // Calcite treats JSON as VARCHAR
        return SqlTypeName.VARCHAR;
    }

    private void addRelationships(SchemaPlus rootSchema, List<SchemaDefinition.RelationshipDefinition> relationships) {
        // Calcite uses constraints for join optimization
        // For now, we rely on the planner's join rules which don't strictly need explicit FKs
        // but having them helps with join elimination and cardinality estimation
    }
}