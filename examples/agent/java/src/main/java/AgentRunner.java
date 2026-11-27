import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.openai.client.okhttp.OpenAIOkHttpClient;
import java.util.List;

/**
 * 환경변수로 고른 API로 에이전트를 돌린다. Skills와 MCP 샘플이 함께 쓴다.
 *
 * <p>LLM_PROVIDER=openai 이면 OpenAI 호환 API, 그 외에는 Claude API다.
 */
public final class AgentRunner {

    private AgentRunner() {}

    static AgentResult runFromEnv(String inquiry, String system, List<OrderTools.Tool> tools) {
        return runFromEnv(inquiry, system, tools, OrderTools.LOG_TOOL_CALL);
    }

    /** system, tools, listener는 두 루프가 똑같이 받는다. */
    static AgentResult runFromEnv(String inquiry, String system, List<OrderTools.Tool> tools,
            OrderTools.ToolListener listener) {
        if ("openai".equals(System.getenv("LLM_PROVIDER"))) {
            String model = System.getenv("OPENAI_MODEL");
            if (model == null || model.isBlank()) {
                throw new IllegalStateException("OPENAI_MODEL 환경변수에 모델 이름을 넣어야 한다.");
            }
            return OpenAiCompatOrderAgent.runAgent(
                    OpenAIOkHttpClient.fromEnv(), model, inquiry, listener, system, tools);
        }
        return ClaudeOrderAgent.runAgent(
                AnthropicOkHttpClient.fromEnv(), inquiry, listener, system, tools);
    }
}
