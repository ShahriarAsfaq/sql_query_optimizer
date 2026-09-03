package com.sqloptimizer.calcite.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

import java.util.Map;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class OptimizeRequest {
    @NotBlank
    @JsonProperty("sql")
    private String sql;

    @JsonProperty("schema")
    private Map<String, Object> schema;

    @JsonProperty("dialect")
    private String dialect = "postgresql";

    @JsonProperty("explain")
    private boolean explain = false;
}