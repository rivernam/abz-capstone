import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;

/**
 * 추적, 모니터링, 비용 집계.
 *
 * <ul>
 *   <li>추적: 요청 하나(trace)가 거친 단계(span)를 시간, 토큰, 오류와 함께 남긴다.
 *   <li>비용: 모델 호출마다 토큰 수에 단가를 곱해 span에 적고, 나중에 trace별로 합친다.
 * </ul>
 *
 * <p>여기서는 JSONL 파일에 남긴다. 실제 서비스에서는 같은 자리에 OpenTelemetry나 Langfuse 같은 도구를 붙인다.
 */
public class Tracing {

    /** 100만 토큰당 달러 {입력, 출력}. 쓰는 모델의 실제 단가로 채운다. 표에 없는 모델은 0으로 계산한다. */
    static final Map<String, double[]> PRICES = Map.of(
            "claude-opus-5-5", new double[] {4.00, 20.00});

    static final ObjectMapper MAPPER = new ObjectMapper();

    static double costUsd(String model, long inputTokens, long outputTokens) {
        // 서버가 돌려주는 모델 이름에 날짜 등이 붙을 수 있어 앞부분으로 찾는다
        double[] price = PRICES.entrySet().stream()
                .filter(entry -> model.startsWith(entry.getKey()))
                .map(Map.Entry::getValue)
                .findFirst()
                .orElse(new double[] {0.0, 0.0});
        return (inputTokens * price[0] + outputTokens * price[1]) / 1_000_000;
    }

    static class Tracer {
        private final Path path;

        Tracer(String path) {
            this.path = Path.of(path);
        }

        /** body 하나가 span 하나다. body 안에서 record에 값을 더 적을 수 있다. */
        <T> T span(String traceId, String name, Function<Map<String, Object>, T> body) {
            Map<String, Object> record = new LinkedHashMap<>();
            record.put("trace_id", traceId);
            record.put("name", name);
            long started = System.nanoTime();
            try {
                return body.apply(record);
            } catch (RuntimeException e) {
                record.put("error", e.getClass().getSimpleName() + ": " + e.getMessage());
                throw e;
            } finally {
                record.put("duration_ms", (System.nanoTime() - started) / 1_000_000);
                write(record);
            }
        }

        /** 모델 호출을 감싼다. 부르는 쪽 코드는 바꾸지 않고 호출마다 span이 남는다. */
        Llm traced(Llm llm, String traceId) {
            return (system, messages) -> span(traceId, "llm.call", record -> {
                Llm.Reply reply = llm.complete(system, messages);
                record.put("model", reply.model());
                record.put("input_tokens", reply.inputTokens());
                record.put("output_tokens", reply.outputTokens());
                record.put("cost_usd", costUsd(reply.model(), reply.inputTokens(), reply.outputTokens()));
                return reply;
            });
        }

        private synchronized void write(Map<String, Object> record) {
            try {
                if (path.getParent() != null) {
                    Files.createDirectories(path.getParent());
                }
                Files.writeString(path, MAPPER.writeValueAsString(record) + "\n", StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    }

    record Total(int llmCalls, long inputTokens, long outputTokens, double costUsd, int errors) {}

    /** trace별 합계: 모델 호출 수, 토큰, 비용, 오류 수. */
    static Map<String, Total> summarize(String path) throws IOException {
        Map<String, Total> totals = new LinkedHashMap<>();
        for (String line : Files.readAllLines(Path.of(path), StandardCharsets.UTF_8)) {
            Map<String, Object> span = MAPPER.readValue(line, new TypeReference<>() {});
            boolean isCall = "llm.call".equals(span.get("name"));
            Total add = new Total(
                    isCall ? 1 : 0,
                    isCall ? number(span, "input_tokens").longValue() : 0,
                    isCall ? number(span, "output_tokens").longValue() : 0,
                    isCall ? number(span, "cost_usd").doubleValue() : 0.0,
                    span.containsKey("error") ? 1 : 0);
            totals.merge((String) span.get("trace_id"), add, (a, b) -> new Total(
                    a.llmCalls() + b.llmCalls(), a.inputTokens() + b.inputTokens(),
                    a.outputTokens() + b.outputTokens(), a.costUsd() + b.costUsd(), a.errors() + b.errors()));
        }
        return totals;
    }

    private static Number number(Map<String, Object> span, String key) {
        return span.get(key) instanceof Number number ? number : 0;
    }

    public static void main(String[] args) throws IOException {
        Tracer tracer = new Tracer("data/traces.jsonl");
        String traceId = UUID.randomUUID().toString(); // 요청 하나에 ID 하나. 로그와 응답에도 같은 ID를 남기면 찾기 쉽다
        Llm llm = tracer.traced(Llm.fromEnv(), traceId);

        tracer.span(traceId, "request", record -> {
            record.put("user_id", "user-1");
            Llm.Reply first = llm.complete("한 문장으로 답한다.",
                    List.of(new Llm.Msg("user", "에이전트 추적이 왜 필요해?")));
            return llm.complete("한 단어로 답한다.",
                    List.of(new Llm.Msg("user", "이 문장의 핵심어는? " + first.text())));
        });

        System.out.println(summarize("data/traces.jsonl").get(traceId));
    }
}
