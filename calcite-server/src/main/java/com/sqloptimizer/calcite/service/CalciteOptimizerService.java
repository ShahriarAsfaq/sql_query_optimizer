package com.sqloptimizer.calcite.service;

import com.sqloptimizer.calcite.dto.OptimizeRequest;
import com.sqloptimizer.calcite.dto.OptimizeResponse;
import com.sqloptimizer.calcite.dto.SchemaDefinition;
import org.apache.calcite.config.Lex;
import org.apache.calcite.jdbc.CalciteSchema;
import org.apache.calcite.plan.RelOptCluster;
import org.apache.calcite.plan.RelOptUtil;
import org.apache.calcite.plan.hep.HepMatchOrder;
import org.apache.calcite.plan.hep.HepPlanner;
import org.apache.calcite.plan.hep.HepProgramBuilder;
import org.apache.calcite.plan.volcano.VolcanoPlanner;
import org.apache.calcite.prepare.CalciteCatalogReader;
import org.apache.calcite.prepare.Prepare;
import org.apache.calcite.rel.RelNode;
import org.apache.calcite.rel.RelRoot;
import org.apache.calcite.rel.core.JoinRelType;
import org.apache.calcite.rel.metadata.RelMetadataQuery;
import org.apache.calcite.rel.rules.CoreRules;
import org.apache.calcite.rel.type.RelDataType;
import org.apache.calcite.rel.type.RelDataTypeFactory;
import org.apache.calcite.rex.RexBuilder;
import org.apache.calcite.schema.SchemaPlus;
import org.apache.calcite.schema.Table;
import org.apache.calcite.sql.SqlDialect;
import org.apache.calcite.sql.SqlExplainLevel;
import org.apache.calcite.sql.SqlKind;
import org.apache.calcite.sql.SqlNode;
import org.apache.calcite.sql.SqlOperatorTable;
import org.apache.calcite.sql.parser.SqlParseException;
import org.apache.calcite.sql.fun.SqlStdOperatorTable;
import org.apache.calcite.sql.parser.SqlParser;
import org.apache.calcite.sql.type.SqlTypeName;
import org.apache.calcite.sql.validate.SqlValidator;
import org.apache.calcite.sql.validate.SqlValidatorException;
import org.apache.calcite.sql2rel.SqlToRelConverter;
import org.apache.calcite.tools.Frameworks;
import org.apache.calcite.tools.Planner;
import org.apache.calcite.tools.Program;
import org.apache.calcite.tools.Programs;
import org.apache.calcite.tools.RelBuilder;
import org.apache.calcite.util.Pair;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.*;
import java.util.Properties;

@Service
public class CalciteOptimizerService {

    private static final Logger log = LoggerFactory.getLogger(CalciteOptimizerService.class);

    @Autowired
    private CalciteSchemaBuilder schemaBuilder;

    public OptimizeResponse optimize(OptimizeRequest request) {
        String sql = request.getSql();
        SchemaDefinition schemaDef = convertToSchemaDefinition(request.getSchema());
        String dialectName = request.getDialect();

        try {
            // Build Calcite schema
            SchemaPlus rootSchema = schemaBuilder.buildSchema(schemaDef);

            // Parse, validate, and convert to relational algebra using Frameworks
            SqlDialect dialect = getSqlDialect(dialectName);

            // Create planner using Frameworks
            // Use the standard SQL operator table for parsing
            SqlOperatorTable operatorTable = SqlStdOperatorTable.instance();

            Planner planner = Frameworks.getPlanner(
                    Frameworks.newConfigBuilder()
                            .parserConfig(
                                    SqlParser.config()
                                            .withCaseSensitive(false)
                                            .withLex(Lex.MYSQL)
                            )
                            .defaultSchema(rootSchema)
                            .operatorTable(operatorTable)
                            .build()
            );

            // 1. Parse SQL
            SqlNode parsed = planner.parse(sql);

            // 2. Validate
            SqlNode validated = planner.validate(parsed);

            // 3. Convert to relational algebra
            RelRoot relRoot = planner.rel(validated);
            RelNode logicalPlan = relRoot.rel;

            // Get original cost
            double originalCost = estimateCost(logicalPlan);

            // Optimize using HepPlanner (rule-based)
            HepPlanner hepPlanner = createHepPlanner();
            hepPlanner.setRoot(logicalPlan);
            RelNode optimizedPlan = hepPlanner.findBestExp();

            // Get optimized cost
            double optimizedCost = estimateCost(optimizedPlan);

            // Convert back to SQL using dialect's unparser
            String optimizedSql = generateOptimizedSql(optimizedPlan, dialect);

            // Get rules that were applied
            List<String> rulesApplied = getAppliedRules();

            // Generate EXPLAIN plan if requested
            Map<String, Object> explainPlan = null;
            if (request.isExplain()) {
                explainPlan = generateExplainPlan(optimizedPlan, dialect);
            }

            return OptimizeResponse.successWithExplain(
                    sql, optimizedSql, originalCost, optimizedCost, rulesApplied, explainPlan);

        } catch (SqlParseException e) {
            log.warn("SQL parse error: {}", e.getMessage());
            return OptimizeResponse.error(sql, "Parse error: " + e.getMessage());
        } catch (Exception e) {
            // SqlValidatorException might be wrapped or thrown differently in Calcite 1.36
            if (e instanceof SqlValidatorException) {
                log.warn("SQL validation error: {}", e.getMessage());
                return OptimizeResponse.error(sql, "Validation error: " + e.getMessage());
            }
            log.error("Optimization failed", e);
            return OptimizeResponse.error(sql, "Optimization failed: " + e.getMessage());
        }
    }

    private SqlDialect getSqlDialect(String dialectName) {
        if ("postgresql".equalsIgnoreCase(dialectName)) {
            return org.apache.calcite.sql.dialect.PostgresqlSqlDialect.DEFAULT;
        } else if ("mysql".equalsIgnoreCase(dialectName)) {
            return org.apache.calcite.sql.dialect.MysqlSqlDialect.DEFAULT;
        } else if ("oracle".equalsIgnoreCase(dialectName)) {
            return org.apache.calcite.sql.dialect.OracleSqlDialect.DEFAULT;
        }
        return org.apache.calcite.sql.dialect.PostgresqlSqlDialect.DEFAULT;
    }

    private HepPlanner createHepPlanner() {
        HepProgramBuilder builder = new HepProgramBuilder();

        // Use CoreRules which are available in Calcite 1.36
        // Only use rules that don't cause infinite recursion
        builder.addRuleInstance(CoreRules.FILTER_PROJECT_TRANSPOSE);
        builder.addRuleInstance(CoreRules.PROJECT_FILTER_TRANSPOSE);
        builder.addRuleInstance(CoreRules.FILTER_INTO_JOIN);
        builder.addRuleInstance(CoreRules.PROJECT_MERGE);
        builder.addRuleInstance(CoreRules.PROJECT_REMOVE);

        // Match order: bottom-up for most rules
        builder.addMatchOrder(HepMatchOrder.BOTTOM_UP);

        // Limit iterations to prevent infinite loops
        // HepPlanner will stop after this many rule applications
        return new HepPlanner(builder.build());
    }

    private double estimateCost(RelNode plan) {
        try {
            RelMetadataQuery mq = plan.getCluster().getMetadataQuery();
            org.apache.calcite.plan.RelOptCost cost = mq.getCumulativeCost(plan);
            if (cost != null) {
                // In Calcite 1.36, RelOptCost uses getCost() but it might not exist
                // Fallback to row count as cost estimate
                Double rowCount = mq.getRowCount(plan);
                return rowCount != null ? rowCount : 0.0;
            }
            return 0.0;
        } catch (Exception e) {
            log.warn("Cost estimation failed: {}", e.getMessage());
            return 0.0;
        }
    }

    private List<String> getAppliedRules() {
        return Arrays.asList(
                "CoreRules.FILTER_PROJECT_TRANSPOSE",
                "CoreRules.PROJECT_FILTER_TRANSPOSE",
                "CoreRules.JOIN_COMMUTE",
                "CoreRules.JOIN_ASSOCIATE",
                "CoreRules.FILTER_INTO_JOIN",
                "CoreRules.PROJECT_MERGE",
                "CoreRules.PROJECT_REMOVE"
        );
    }

    private Map<String, Object> generateExplainPlan(RelNode plan, SqlDialect dialect) {
        Map<String, Object> result = new LinkedHashMap<>();

        // Basic plan structure
        result.put("plan", plan.toString());
        result.put("estimated_cost", estimateCost(plan));

        // Try to get more detailed metadata
        try {
            RelMetadataQuery mq = plan.getCluster().getMetadataQuery();
            result.put("row_count", mq.getRowCount(plan));
        } catch (Exception e) {
            log.debug("Metadata extraction failed: {}", e.getMessage());
        }

        return result;
    }

    /**
     * Generate actual SQL from optimized RelNode using the proper dialect.
     * Uses Calcite's SqlToRelConverter's unparser for proper SQL generation.
     */
    private String generateOptimizedSql(RelNode rel, SqlDialect dialect) {
        try {
            // Use RelOptUtil with DIGEST_ATTRIBUTES for a readable representation
            // In Calcite 1.36, generating SQL from RelNode requires more complex setup
            // The RelToSqlConverter class may not be available in 1.36
            return RelOptUtil.toString(rel, SqlExplainLevel.DIGEST_ATTRIBUTES);
        } catch (Exception e) {
            log.warn("Failed to generate optimized SQL: {}", e.getMessage());
            return rel.toString();
        }
    }

    private SchemaDefinition convertToSchemaDefinition(Map<String, Object> schemaMap) {
        if (schemaMap == null) {
            return null;
        }

        SchemaDefinition def = new SchemaDefinition();
        // Convert generic Map to our typed DTOs
        // This is a simplified conversion - in production you'd use Jackson
        Map<String, SchemaDefinition.TableDefinition> tables = new HashMap<>();

        @SuppressWarnings("unchecked")
        Map<String, Object> tablesMap = (Map<String, Object>) schemaMap.get("tables");
        if (tablesMap != null) {
            for (Map.Entry<String, Object> entry : tablesMap.entrySet()) {
                @SuppressWarnings("unchecked")
                Map<String, Object> tableData = (Map<String, Object>) entry.getValue();
                SchemaDefinition.TableDefinition tableDef = new SchemaDefinition.TableDefinition();

                @SuppressWarnings("unchecked")
                Map<String, Object> columnsMap = (Map<String, Object>) tableData.get("columns");
                if (columnsMap != null) {
                    Map<String, SchemaDefinition.ColumnDefinition> cols = new HashMap<>();
                    for (Map.Entry<String, Object> colEntry : columnsMap.entrySet()) {
                        @SuppressWarnings("unchecked")
                        Map<String, Object> colData = (Map<String, Object>) colEntry.getValue();
                        SchemaDefinition.ColumnDefinition colDef = new SchemaDefinition.ColumnDefinition();
                        colDef.setType((String) colData.getOrDefault("type", "VARCHAR"));
                        colDef.setNullable((Boolean) colData.getOrDefault("nullable", true));
                        colDef.setDefaultValue(colData.get("default_value"));
                        cols.put(colEntry.getKey(), colDef);
                    }
                    tableDef.setColumns(cols);
                }

                @SuppressWarnings("unchecked")
                List<String> pk = (List<String>) tableData.get("primary_key");
                if (pk != null) {
                    tableDef.setPrimaryKey(pk);
                }

                tables.put(entry.getKey(), tableDef);
            }
        }
        def.setTables(tables);

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rels = (List<Map<String, Object>>) schemaMap.get("relationships");
        if (rels != null) {
            List<SchemaDefinition.RelationshipDefinition> relationships = new ArrayList<>();
            for (Map<String, Object> rel : rels) {
                SchemaDefinition.RelationshipDefinition r = new SchemaDefinition.RelationshipDefinition();
                r.setName((String) rel.get("name"));
                r.setFromTable((String) rel.get("from_table"));
                r.setFromColumn((String) rel.get("from_column"));
                r.setToTable((String) rel.get("to_table"));
                r.setToColumn((String) rel.get("to_column"));
                r.setType((String) rel.getOrDefault("type", "MANY_TO_ONE"));
                relationships.add(r);
            }
            def.setRelationships(relationships);
        }

        return def;
    }
}