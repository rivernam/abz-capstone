import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.anthropic.core.JsonValue;
import com.anthropic.models.messages.ContentBlock;
import com.anthropic.models.messages.ContentBlockParam;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.StopReason;
import com.anthropic.models.messages.Tool;
import com.anthropic.models.messages.ToolResultBlockParam;
import com.fasterxml.jackson.core.type.TypeReference;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/** Claude API(Messages API)로 만든 에이전트 루프. ANTHROPIC_API_KEY 환경변수가 필요하다. */
public class ClaudeOrderAgent {

    static final String MODEL = "claude-opus-5-5";

    /** Claude의 Tool 정의: 스키마를 inputSchema에 넣는다. */
    static Tool definition(OrderTools.Tool tool) {
        Tool.InputSchema.Properties.Builder properties = Tool.InputSchema.Properties.builder();
        tool.properties().forEach((key, schema) -> properties.putAdditionalProperty(key, JsonValue.from(schema)));
        return Tool.builder()
                .name(tool.name())
                .description(tool.description())
                .inputSchema(Tool.InputSchema.builder()
                        .properties(properties.build())
                        .required(tool.required())
                        .putAdditionalProperty("additionalProperties", JsonValue.from(false))
                        .build())
                .strict(true)
                .build();
    }

    /** 인자는 JSON 객체로 온다. 객체가 아니면 null을 돌려주고, Tool 실행 쪽이 오류로 처리한다. */
    static Map<String, Object> toArgs(JsonValue input) {
        try {
            return input.convert(new TypeReference<Map<String, Object>>() {});
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    static AgentResult runAgent(AnthropicClient client, String inquiry, OrderTools.ToolListener listener) {
        // 빌더가 대화 기록을 들고 있다. 매 호출마다 메시지를 덧붙인다.
        // 시스템 프롬프트는 메시지가 아니라 system으로 넘긴다.
        MessageCreateParams.Builder params = MessageCreateParams.builder()
                .model(MODEL)
                .maxTokens(16000L)
                .system(OrderTools.SYSTEM)
                .addUserMessage("<inquiry>\n" + inquiry + "\n</inquiry>");
        OrderTools.TOOLS.forEach(tool -> params.addTool(definition(tool)));

        for (int call = 0; call < OrderTools.MAX_MODEL_CALLS; call++) {
            Message response = client.messages().create(params.build());
            StopReason stop = response.stopReason().orElse(null);
            if (StopReason.REFUSAL.equals(stop) || StopReason.MAX_TOKENS.equals(stop)) {
                return AgentResult.stopped("stop_reason=" + stop);
            }

            // 모델의 응답을 통째로 기록에 붙인다 (tool_use 블록 포함)
            params.addMessage(response);

            if (!StopReason.TOOL_USE.equals(stop)) { // Tool을 더 부르지 않으면 끝
                return AgentResult.done(response.content().stream()
                        .flatMap(block -> block.text().stream())
                        .map(text -> text.text())
                        .collect(Collectors.joining()));
            }

            // 모델이 요청한 Tool을 모두 실행하고, 결과를 user 메시지 하나에 담아 돌려준다
            List<ContentBlockParam> results = new ArrayList<>();
            for (ContentBlock block : response.content()) {
                block.toolUse().ifPresent(toolUse -> {
                    OrderTools.ToolOutput output = OrderTools.run(toolUse.name(), toArgs(toolUse._input()));
                    listener.onTool(toolUse.name(), toolUse._input(), output);
                    results.add(ContentBlockParam.ofToolResult(ToolResultBlockParam.builder()
                            .toolUseId(toolUse.id())
                            .content(output.text())
                            .isError(output.isError())
                            .build()));
                });
            }
            params.addUserMessageOfContentBlockParams(results);
        }
        return AgentResult.stopped("모델 호출 상한에 걸렸다.");
    }

    public static void main(String[] args) {
        AnthropicClient client = AnthropicOkHttpClient.fromEnv();
        AgentResult result = runAgent(client, "A-100이랑 A-200 주문 둘 다 취소해 주세요.", OrderTools.LOG_TOOL_CALL);
        System.out.println(result.display());
    }
}
