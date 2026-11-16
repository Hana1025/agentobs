package dev.agentobs.ingest;

import com.google.protobuf.InvalidProtocolBufferException;
import com.google.protobuf.util.JsonFormat;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import io.opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest;
import io.opentelemetry.proto.collector.trace.v1.ExportTraceServiceResponse;
import io.swagger.v3.oas.annotations.Hidden;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * OTLP/HTTP traces 入库（只对 collector）。
 * 200 + 空 ExportTraceServiceResponse；请求体损坏 400；数据库不可用 503（collector 会重试）。
 */
@RestController
@Hidden
public class OtlpTracesController {

    private static final Logger log = LoggerFactory.getLogger(OtlpTracesController.class);
    private static final MediaType PROTOBUF = MediaType.parseMediaType("application/x-protobuf");

    private final OtlpSpanMapper mapper;
    private final SpanRepository repo;
    private final Counter okCounter;
    private final Counter dupCounter;
    private final Counter errCounter;
    private final Timer batchTimer;

    public OtlpTracesController(OtlpSpanMapper mapper, SpanRepository repo, MeterRegistry registry) {
        this.mapper = mapper;
        this.repo = repo;
        this.okCounter = registry.counter("agentobs_ingest_spans_total", "result", "ok");
        this.dupCounter = registry.counter("agentobs_ingest_spans_total", "result", "duplicate");
        this.errCounter = registry.counter("agentobs_ingest_spans_total", "result", "error");
        this.batchTimer = registry.timer("agentobs_ingest_batch_seconds");
    }

    @PostMapping(value = "/v1/traces", consumes = {"application/x-protobuf", MediaType.APPLICATION_JSON_VALUE})
    public ResponseEntity<byte[]> export(
            @RequestHeader(value = "Content-Type", required = false) String contentType,
            @RequestBody byte[] body) {
        ExportTraceServiceRequest req;
        try {
            req = parse(contentType, body);
        } catch (InvalidProtocolBufferException | IllegalArgumentException e) {
            log.warn("bad OTLP payload: {}", e.getMessage());
            return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                    .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                    .body(("{\"title\":\"Bad Request\",\"status\":400,\"detail\":\"invalid OTLP payload\"}")
                            .getBytes(StandardCharsets.UTF_8));
        }
        List<SpanRecord> spans = mapper.map(req);
        Timer.Sample sample = Timer.start();
        try {
            int inserted = repo.insertAll(spans);
            okCounter.increment(inserted);
            dupCounter.increment(spans.size() - inserted);
        } catch (DataAccessException e) {
            errCounter.increment(spans.size());
            log.error("ingest failed for {} spans: {}", spans.size(), e.getMessage());
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                    .body("{\"title\":\"Service Unavailable\",\"status\":503,\"detail\":\"database unavailable\"}"
                            .getBytes(StandardCharsets.UTF_8));
        } finally {
            sample.stop(batchTimer);
        }
        return ResponseEntity.ok()
                .contentType(PROTOBUF)
                .body(ExportTraceServiceResponse.getDefaultInstance().toByteArray());
    }

    static ExportTraceServiceRequest parse(String contentType, byte[] body) throws InvalidProtocolBufferException {
        if (contentType != null && contentType.contains("json")) {
            ExportTraceServiceRequest.Builder b = ExportTraceServiceRequest.newBuilder();
            JsonFormat.parser().ignoringUnknownFields().merge(new String(body, StandardCharsets.UTF_8), b);
            return b.build();
        }
        return ExportTraceServiceRequest.parseFrom(body);
    }
}
