import com.openai.client.OpenAIClient;
import com.openai.client.okhttp.OpenAIOkHttpClient;
import com.openai.core.JsonValue;
import com.openai.models.FunctionDefinition;
import com.openai.models.FunctionParameters;
import com.openai.models.chat.completions.ChatCompletion;
import com.openai.models.chat.completions.ChatCompletionCreateParams;
import com.openai.models.chat.completions.ChatCompletionFunctionTool;
import com.openai.models.chat.completions.ChatCompletionMessage;
import com.openai.models.chat.completions.ChatCompletionMessageFunctionToolCall;
import com.openai.models.chat.completions.ChatCompletionMessageToolCall;
import com.openai.models.chat.completions.ChatCompletionToolMessageParam;
import java.util.List;
import java.util.Map;
import tools.jackson.core.JacksonException;
import tools.jackson.core.type.TypeReference;

/**
 * OpenAI 호환 API(Chat Completions)로 만든 에이전트 루프.
 *
 * <p>OpenAI, vLLM, Ollama, LiteLLM처럼 /v1/chat/completions를 제공하는 서버에 쓴다. 환경변수:
 * OPENAI_API_KEY(키가 필요 없는 로컬 서버는 아무 값), OPENAI_BASE_URL(서버 주소, 빼면 OpenAI),
 * OPENAI_MODEL(서버가 제공하는 모델 이름. Tool 호출을 지원하는 모델이어야 한다).
 */
public class OpenAiCompatOrderAgent {

    /** OpenAI 호환 Tool 정의: function으로 감싸고 스키마를 parameters에 넣는다. */
    static ChatCompletionFunctionTool definition(OrderTools.Tool tool) {
        FunctionParameters.Builder parameters = FunctionParameters.builder();
        tool.parameters().forEach((key, value) -> parameters.putAdditionalProperty(key, JsonValue.from(value)));
        return ChatCompletionFunctionTool.builder()
                .function(FunctionDefinition.builder()
                        .name(tool.name())
                        .description(tool.description())
                        .parameters(parameters.build())
                        .build())
                .build();
    }

    static OrderTools.ToolOutput runTool(ChatCompletionMessageFunctionToolCall call, List<OrderTools.Tool> tools) {
        Map<String, Object> args;
        try {
            // 인자가 JSON 문자열로 온다
            args = Json.MAPPER.readValue(call.function().arguments(), new TypeReference<>() {});
        } catch (JacksonException e) {
            return new OrderTools.ToolOutput("오류: 인자가 JSON 객체가 아니다: " + e.getOriginalMessage(), true);
        }
        return OrderTools.run(call.function().name(), args, tools);
    }

    static AgentResult runAgent(OpenAIClient client, String model, String inquiry, OrderTools.ToolListener listener) {
        return runAgent(client, model, inquiry, listener, OrderTools.SYSTEM, OrderTools.TOOLS);
    }

    /** system과 tools를 바꿔 끼우면 같은 루프로 다른 에이전트가 된다. */
    static AgentResult runAgent(OpenAIClient client, String model, String inquiry, OrderTools.ToolListener listener,
            String system, List<OrderTools.Tool> tools) {
        // 빌더가 대화 기록을 들고 있다. 시스템 프롬프트는 첫 메시지로 넣는다.
        ChatCompletionCreateParams.Builder params = ChatCompletionCreateParams.builder()
                .model(model)
                .addSystemMessage(system)
                .addUserMessage("<inquiry>\n" + inquiry + "\n</inquiry>");
        tools.forEach(tool -> params.addTool(definition(tool)));

        for (int n = 0; n < OrderTools.MAX_MODEL_CALLS; n++) {
            ChatCompletion completion = client.chat().completions().create(params.build());
            ChatCompletion.Choice choice = completion.choices().get(0);
            ChatCompletion.Choice.FinishReason finish = choice.finishReason();
            if (ChatCompletion.Choice.FinishReason.LENGTH.equals(finish)
                    || ChatCompletion.Choice.FinishReason.CONTENT_FILTER.equals(finish)) {
                return AgentResult.stopped("finish_reason=" + finish);
            }

            // 모델의 응답을 통째로 기록에 붙인다 (tool_calls 포함)
            ChatCompletionMessage message = choice.message();
            params.addMessage(message);

            List<ChatCompletionMessageToolCall> toolCalls = message.toolCalls().orElse(List.of());
            if (toolCalls.isEmpty()) { // Tool을 더 부르지 않으면 끝
                return AgentResult.done(message.content().orElse(""));
            }

            // Tool 결과는 호출 하나마다 role이 tool인 메시지 하나로 돌려준다
            for (ChatCompletionMessageToolCall toolCall : toolCalls) {
                toolCall.function().ifPresent(call -> {
                    OrderTools.ToolOutput output = runTool(call, tools);
                    listener.onTool(call.function().name(), call.function().arguments(), output);
                    // 오류 표시 칸이 따로 없으므로 오류 여부는 content의 문장으로 알린다
                    params.addMessage(ChatCompletionToolMessageParam.builder()
                            .toolCallId(call.id())
                            .content(output.text())
                            .build());
                });
            }
        }
        return AgentResult.stopped("모델 호출 상한에 걸렸다.");
    }

    public static void main(String[] args) {
        String model = System.getenv("OPENAI_MODEL");
        if (model == null || model.isBlank()) {
            throw new IllegalStateException("OPENAI_MODEL 환경변수에 모델 이름을 넣어야 한다.");
        }
        OpenAIClient client = OpenAIOkHttpClient.fromEnv(); // OPENAI_API_KEY, OPENAI_BASE_URL을 읽는다
        AgentResult result = runAgent(client, model, "A-100이랑 A-200 주문 둘 다 취소해 주세요.", OrderTools.LOG_TOOL_CALL);
        System.out.println(result.display());
    }
}
