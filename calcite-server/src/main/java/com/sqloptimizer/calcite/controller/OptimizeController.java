package com.sqloptimizer.calcite.controller;

import com.sqloptimizer.calcite.dto.OptimizeRequest;
import com.sqloptimizer.calcite.dto.OptimizeResponse;
import com.sqloptimizer.calcite.service.CalciteOptimizerService;
import jakarta.validation.Valid;
import org.apache.calcite.sql.parser.SqlParser;
import org.apache.calcite.tools.Planner;
import org.apache.calcite.tools.Programs;
import org.apache.calcite.schema.SchemaPlus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/calcite")
public class OptimizeController {

    private static final Logger log = LoggerFactory.getLogger(OptimizeController.class);

    @Autowired
    private CalciteOptimizerService optimizerService;

    @PostMapping("/optimize")
    public ResponseEntity<OptimizeResponse> optimize(@Valid @RequestBody OptimizeRequest request) {
        log.info("Optimize request received: dialect={}, explain={}, sql length={}",
                request.getDialect(), request.isExplain(), request.getSql().length());

        OptimizeResponse response = optimizerService.optimize(request);

        if (!response.isValid()) {
            log.warn("Optimization failed: {}", response.getError());
            return ResponseEntity.badRequest().body(response);
        }

        log.info("Optimization successful: original_cost={}, optimized_cost={}",
                response.getOriginalCost(), response.getOptimizedCost());

        return ResponseEntity.ok(response);
    }

    @PostMapping("/validate")
    public ResponseEntity<ValidateResponse> validate(@Valid @RequestBody OptimizeRequest request) {
        log.info("Validate request received: sql length={}", request.getSql().length());

        // For validation, we just parse and validate without optimization
        try {
            // Simple validation using Calcite parser
            SqlParser parser = SqlParser.create(request.getSql(),
                    SqlParser.config().withCaseSensitive(false));
            parser.parseQuery();

            return ResponseEntity.ok(new ValidateResponse(true, null));
        } catch (Exception e) {
            log.warn("Validation failed: {}", e.getMessage());
            return ResponseEntity.ok(new ValidateResponse(false, e.getMessage()));
        }
    }

    @GetMapping("/health")
    public ResponseEntity<HealthResponse> health() {
        return ResponseEntity.ok(new HealthResponse("UP", "Calcite Optimizer Server"));
    }

    // DTOs for validate and health endpoints
    public static class ValidateResponse {
        private boolean valid;
        private String error;

        public ValidateResponse(boolean valid, String error) {
            this.valid = valid;
            this.error = error;
        }

        public boolean isValid() { return valid; }
        public String getError() { return error; }
    }

    public static class HealthResponse {
        private String status;
        private String service;

        public HealthResponse(String status, String service) {
            this.status = status;
            this.service = service;
        }

        public String getStatus() { return status; }
        public String getService() { return service; }
    }
}