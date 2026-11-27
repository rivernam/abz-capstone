import io.modelcontextprotocol.json.McpJsonDefaults;
import io.modelcontextprotocol.server.McpServer;
import io.modelcontextprotocol.server.McpServerFeatures.SyncToolSpecification;
import io.modelcontextprotocol.server.transport.StdioServerTransportProvider;
import io.modelcontextprotocol.spec.McpSchema;
import java.util.List;
import java.util.concurrent.CountDownLatch;

/**
 * 주문 Tool을 MCP 서버로 내놓는다. 에이전트와 다른 프로세스에서 돈다.
 *
 * <p>같은 서버를 여러 에이전트(직접 만든 루프, Claude Code, 사내 챗봇 등)가 함께 쓸 수 있다. 안전 검사는 여전히 Tool
 * 구현(OrderTools) 안에 있다. 서버를 누가 부르든 같은 검사가 걸린다.
 *
 * <p>직접 실행할 일은 없다. MCP 클라이언트(McpTools)가 이 클래스를 하위 프로세스로 띄우고 표준 입출력으로 대화한다. 표준
 * 출력은 프로토콜 전용이라서 여기에 다른 글을 찍으면 안 된다. 기록은 표준 오류로 나간다.
 */
public class McpOrderServer {

    static SyncToolSpecification specification(OrderTools.Tool tool) {
        McpSchema.Tool definition = McpSchema.Tool.builder()
                .name(tool.name())
                .description(tool.description())
                .inputSchema(tool.parameters())
                .build();
        return new SyncToolSpecification(definition, (exchange, request) -> {
            try {
                return result(OrderTools.call(tool, request.arguments()), false);
            } catch (OrderTools.ToolException e) { // 의도한 거절: 사유를 모델에게 돌려준다
                return result(e.getMessage(), true);
            } catch (RuntimeException e) { // 버그: 기록을 남기고 내용은 숨긴다
                OrderTools.LOGGER.log(System.Logger.Level.ERROR, "Tool " + tool.name() + " 실행 중 예상하지 못한 오류", e);
                return result(OrderTools.INTERNAL_ERROR, true);
            }
        });
    }

    static McpSchema.CallToolResult result(String text, boolean isError) {
        return McpSchema.CallToolResult.builder().addTextContent(text).isError(isError).build();
    }

    public static void main(String[] args) throws InterruptedException {
        List<SyncToolSpecification> tools = OrderTools.TOOLS.stream().map(McpOrderServer::specification).toList();
        McpServer.sync(new StdioServerTransportProvider(McpJsonDefaults.getMapper()))
                .serverInfo("orders", "1.0")
                .capabilities(McpSchema.ServerCapabilities.builder().tools(false).build())
                .tools(tools)
                .build();
        new CountDownLatch(1).await(); // 클라이언트가 프로세스를 끝낼 때까지 요청을 받는다
    }
}
