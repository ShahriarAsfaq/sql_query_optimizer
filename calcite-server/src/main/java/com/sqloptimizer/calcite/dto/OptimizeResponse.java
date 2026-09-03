package com.sqloptimizer.calcite.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

import java.util.List;
import java.util.Map;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class OptimizeResponse {
    @JsonProperty("original_sql")
    private String originalSql;

    @JsonProperty("optimized_sql")
    private String optimizedSql;

    @JsonProperty("original_cost")
    private Double originalCost;

    @JsonProperty("optimized_cost")
    private Double optimizedCost;

    @JsonProperty("rules_applied")
    private List<String> rulesApplied;

    @JsonProperty("explain_plan")
    private Map<String, Object> explainPlan;

    @JsonProperty("valid")
    private boolean valid = true;

    @JsonProperty("error")
    private String error;

    public static OptimizeResponse success(String originalSql, String optimizedSql,
                                           Double originalCost, Double optimizedCost,
                                           List<String> rulesApplied) {
        return new OptimizeResponse(originalSql, optimizedSql, originalCost, optimizedCost,
                rulesApplied, null, true, null);
    }

    public static OptimizeResponse successWithExplain(String originalSql, String optimizedSql,
                                                      Double originalCost, Double optimizedCost,
                                                      List<String> rulesApplied,
                                                      Map<String, Object> explainPlan) {
        return new OptimizeResponse(originalSql, optimizedSql, originalCost, optimizedCost,
                rulesApplied, explainPlan, true, null);
    }

    public static OptimizeResponse error(String originalSql, String error) {
        return new OptimizeResponse(originalSql, null, null, null, null, null, false, error);
    }
}