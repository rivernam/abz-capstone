import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 여러 에이전트로 나누는 구조: 지휘자와 작업자(orchestrator-workers).
 *
 * <p>지휘자가 일을 독립된 하위 작업으로 나누고, 작업자들이 각자 새 대화에서 동시에 처리한 뒤, 지휘자가 결과를 모아 답을 쓴다.
 *
 * <ul>
 *   <li>작업자는 지휘자의 대화를 보지 못한다. 넘겨받은 지시문이 아는 것의 전부다.
 *   <li>작업자의 긴 중간 과정은 버리고 결과만 지휘자에게 돌아온다. 지휘자의 대화가 짧게 유지된다.
 *   <li>모델 호출 수가 (작업자 수 + 2)번으로 늘어난다. 하위 작업이 서로 독립이고 한 대화에 담기 벅찰 때만 쓴다.
 * </ul>
 */
public class MultiAgent {

    static final int MAX_SUBTASKS = 4; // 작업자 수 상한: 비용이 작업자 수만큼 늘어난다

    static final String PLAN_SYSTEM =
            "너는 지휘자다. <task>를 서로 독립적으로 조사할 수 있는 하위 작업 2~4개로 나눈다. "
                    + "각 하위 작업은 그것만 읽어도 무엇을 해야 하는지 알 수 있게 쓴다. JSON 문자열 배열로만 답한다.";
    static final String WORKER_SYSTEM = "너는 작업자다. 주어진 하위 작업 하나만 처리하고, 결과를 다섯 문장 이내로 정리한다.";
    static final String SYNTHESIS_SYSTEM =
            "너는 지휘자다. 작업자들의 결과를 모아 <task>에 대한 답 하나를 쓴다. "
                    + "결과끼리 어긋나면 그 사실을 밝히고, 실패한 하위 작업은 빠졌다고 알린다.";

    static List<String> plan(Llm llm, String task) {
        Llm.Reply reply = llm.complete(PLAN_SYSTEM, List.of(new Llm.Msg("user", "<task>\n" + task + "\n</task>")));
        List<String> subtasks = Llm.parseJsonList(reply.text());
        return subtasks.subList(0, Math.min(subtasks.size(), MAX_SUBTASKS));
    }

    /** 작업자 하나. 새 대화로 시작하고, 실패해도 다른 작업자를 막지 않는다. */
    static String work(Llm llm, String subtask) {
        try {
            return llm.complete(WORKER_SYSTEM, List.of(new Llm.Msg("user", subtask))).text();
        } catch (RuntimeException e) {
            return "[실패] " + e.getClass().getSimpleName() + ": " + e.getMessage();
        }
    }

    static String run(Llm llm, String task) {
        List<String> subtasks = plan(llm, task);
        if (subtasks.isEmpty()) { // 나누지 못하면 혼자 처리한다
            return work(llm, task);
        }

        ExecutorService pool = Executors.newFixedThreadPool(MAX_SUBTASKS); // 작업자는 동시에 돈다
        List<String> results = new ArrayList<>();
        try {
            List<CompletableFuture<String>> futures = subtasks.stream()
                    .map(subtask -> CompletableFuture.supplyAsync(() -> work(llm, subtask), pool))
                    .toList();
            futures.forEach(future -> results.add(future.join()));
        } finally {
            pool.shutdown();
        }

        StringBuilder report = new StringBuilder("<task>\n" + task + "\n</task>");
        for (int i = 0; i < subtasks.size(); i++) {
            report.append("\n\n[하위 작업 ").append(i + 1).append("] ").append(subtasks.get(i))
                    .append("\n[결과] ").append(results.get(i));
        }
        return llm.complete(SYNTHESIS_SYSTEM, List.of(new Llm.Msg("user", report.toString()))).text();
    }

    public static void main(String[] args) {
        System.out.println(run(Llm.fromEnv(), "사내 문의 응대에 에이전트를 도입할 때의 장점, 위험, 필요한 준비를 정리해 줘."));
    }
}
