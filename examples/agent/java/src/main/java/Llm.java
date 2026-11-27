import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.StopReason;
import com.openai.client.OpenAIClient;
import com.openai.client.okhttp.OpenAIOkHttpClient;
import com.openai.models.chat.completions.ChatCompletion;
import com.openai.models.chat.completions.ChatCompletionCreateParams;
import com.openai.models.completions.CompletionUsage;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;

/**
 * 운영 요소 샘플이 함께 쓰는 모델 호출 인터페이스. 글을 넣으면 글이 나오는 가장 단순한 모양이다.
 *
 * <p>complete(system, messages) 하나로 맞춰 두면 나머지 코드는 어떤 API를 쓰는지 몰라도 된다.
 * 환경변수 LLM_PROVIDER=openai 이면 OpenAI 호환 API(OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL),
 * 그 외에는 Claude API(ANTHROPIC_API_KEY)를 쓴다.
 */
@FunctionalInterface
public interface Llm {

    /** role은 "user" 또는 "assistant"다. */
    record Msg(String role, String content) {}

    record Reply(String text, String model, long inputTokens, long outputTokens) {}

    Reply complete(String system, List<Msg> messages);

    static Llm claude(String model) {
        AnthropicClient client = AnthropicOkHttpClient.fromEnv(); // 클라이언트는 한 번만 만든다
        return (system, messages) -> {
            MessageCreateParams.Builder params = MessageCreateParams.builder()
                    .model(model)
                    .maxTokens(16000L)
                    .system(system);
            for (Msg message : messages) {
                if (message.role().equals("assistant")) {
                    params.addAssistantMessage(message.content());
                } else {
                    params.addUserMessage(message.content());
                }
            }
            Message response = client.messages().create(params.build());
            if (response.stopReason().filter(StopReason.REFUSAL::equals).isPresent()) {
                throw new IllegalStateException("모델이 응답을 거절했다.");
            }
            String text = response.content().stream()
                    .flatMap(block -> block.text().stream())
                    .map(block -> block.text())
                    .collect(Collectors.joining());
            return new Reply(text, response.model().asString(),
                    response.usage().inputTokens(), response.usage().outputTokens());
        };
    }

    static Llm openAiCompat(String model) {
        OpenAIClient client = OpenAIOkHttpClient.fromEnv();
        return (system, messages) -> {
            ChatCompletionCreateParams.Builder params = ChatCompletionCreateParams.builder()
                    .model(model)
                    .addSystemMessage(system);
            for (Msg message : messages) {
                if (message.role().equals("assistant")) {
                    params.addAssistantMessage(message.content());
                } else {
                    params.addUserMessage(message.content());
                }
            }
            ChatCompletion completion = client.chat().completions().create(params.build());
            Optional<CompletionUsage> usage = completion.usage(); // 호환 서버에 따라 없을 수 있다
            return new Reply(
                    completion.choices().get(0).message().content().orElse(""),
                    completion.model(),
                    usage.map(CompletionUsage::promptTokens).orElse(0L),
                    usage.map(CompletionUsage::completionTokens).orElse(0L));
        };
    }

    static Llm fromEnv() {
        if ("openai".equals(System.getenv("LLM_PROVIDER"))) {
            String model = System.getenv("OPENAI_MODEL");
            if (model == null || model.isBlank()) {
                throw new IllegalStateException("OPENAI_MODEL 환경변수에 모델 이름을 넣어야 한다.");
            }
            return openAiCompat(model);
        }
        return claude("claude-opus-5-5");
    }

    /** 모델 답에서 JSON 문자열 배열을 꺼낸다. 앞뒤에 다른 글이 붙어도 읽고, 못 읽으면 빈 목록. */
    static List<String> parseJsonList(String text) {
        int start = text.indexOf('[');
        int end = text.lastIndexOf(']');
        List<String> items = new ArrayList<>();
        if (start < 0 || end < start) {
            return items;
        }
        try {
            JsonNode node = Json.MAPPER.readTree(text.substring(start, end + 1));
            if (node.isArray()) {
                for (JsonNode item : node) {
                    if (item.isString()) {
                        items.add(item.asString());
                    }
                }
            }
        } catch (JacksonException e) {
            // 읽지 못하면 빈 목록
        }
        return items;
    }
}
