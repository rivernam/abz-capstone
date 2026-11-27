import io.modelcontextprotocol.client.McpClient;
import io.modelcontextprotocol.client.McpSyncClient;
import io.modelcontextprotocol.client.transport.ServerParameters;
import io.modelcontextprotocol.client.transport.StdioClientTransport;
import io.modelcontextprotocol.json.McpJsonDefaults;
import io.modelcontextprotocol.spec.McpSchema;
import java.io.File;
import java.net.URISyntaxException;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * MCP: Tool을 별도 서버에서 받아 온다.
 *
 * <ul>
 *   <li>시작할 때 서버에 Tool 목록을 물어 Tool 정의로 바꾼다.
 *   <li>모델이 Tool을 요청하면 메서드를 직접 부르는 대신 서버에 호출을 보낸다.
 *   <li>에이전트 루프는 바뀌지 않는다. tools 자리에 서버에서 받은 Tool을 넣을 뿐이다.
 * </ul>
 */
public final class McpTools implements AutoCloseable {

    private final McpSyncClient client;

    /** 서버를 하위 프로세스로 띄우고 연결한다. */
    McpTools(ServerParameters server) {
        client = McpClient.sync(new StdioClientTransport(server, McpJsonDefaults.getMapper()))
                .requestTimeout(Duration.ofSeconds(30))
                .build();
        client.initialize();
    }

    /** 서버의 Tool을 부른다. 서버가 오류라고 답하면 ToolException으로 바꿔 모델에게 사유가 가게 한다. */
    String call(String name, Map<String, Object> args) {
        McpSchema.CallToolResult result = client.callTool(new McpSchema.CallToolRequest(name, args));
        String text = result.content().stream()
                .filter(McpSchema.TextContent.class::isInstance)
                .map(content -> ((McpSchema.TextContent) content).text())
                .collect(Collectors.joining("\n"));
        if (Boolean.TRUE.equals(result.isError())) {
            throw new OrderTools.ToolException(text);
        }
        return text;
    }

    /** 서버가 알려 준 Tool을 에이전트가 쓰는 Tool로 바꾼다. */
    List<OrderTools.Tool> tools() {
        List<OrderTools.Tool> tools = new ArrayList<>();
        for (McpSchema.Tool tool : client.listTools().tools()) {
            String description = tool.description() == null ? "" : tool.description();
            // 스키마는 서버가 만든 것이라 strict 조건을 맞춘다는 보장이 없다. 인자는 서버가 다시 검사한다
            tools.add(new OrderTools.Tool(tool.name(), description, tool.inputSchema(), false,
                    args -> call(tool.name(), args)));
        }
        return tools;
    }

    @Override
    public void close() {
        client.closeGracefully();
    }

    /** 주문 서버(McpOrderServer)를 지금과 같은 클래스 경로의 하위 프로세스로 띄우는 방법. */
    static ServerParameters orderServer() {
        String java = ProcessHandle.current().info().command().orElse("java");
        return ServerParameters.builder(java)
                .args("-Dfile.encoding=UTF-8", "-cp", currentClasspath(), "McpOrderServer")
                .build();
    }

    /**
     * 지금 프로세스의 클래스 경로. Maven의 exec:java로 실행하면 java.class.path에는 Maven 자신의 경로만 있으므로,
     * 클래스 로더가 들고 있는 경로를 먼저 본다.
     */
    static String currentClasspath() {
        List<String> entries = new ArrayList<>();
        for (ClassLoader loader = McpTools.class.getClassLoader(); loader != null; loader = loader.getParent()) {
            if (loader instanceof URLClassLoader urls) {
                for (URL url : urls.getURLs()) {
                    try {
                        entries.add(Path.of(url.toURI()).toString());
                    } catch (URISyntaxException e) {
                        throw new IllegalStateException(e);
                    }
                }
            }
        }
        return entries.isEmpty() ? System.getProperty("java.class.path") : String.join(File.pathSeparator, entries);
    }

    public static void main(String[] args) {
        AgentResult result;
        try (McpTools mcp = new McpTools(orderServer())) {
            result = AgentRunner.runFromEnv("A-100이랑 A-200 주문 둘 다 취소해 주세요.", OrderTools.SYSTEM, mcp.tools());
        }
        System.out.println(result.display());
    }
}
