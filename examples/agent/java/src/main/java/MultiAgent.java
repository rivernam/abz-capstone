import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

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
    static final long WORKER_TIMEOUT_MILLIS = 120_000; // 작업자 전체를 기다리는 시간. 한 작업자가 멈춰도 전체가 멈추지 않는다

    static final String PLAN_SYSTEM =
            "너는 지휘자다. <task>를 서로 독립적으로 조사할 수 있는 하위 작업 2~4개로 나눈다. "
                    + "각 하위 작업은 그것만 읽어도 무엇을 해야 하는지 알 수 있게 쓴다. JSON 문자열 배열로만 답한다.";
    static final String WORKER_SYSTEM = "너는 작업자다. 주어진 하위 작업 하나만 처리하고, 결과를 다섯 문장 이내로 정리한다.";
    static final String SYNTHESIS_SYSTEM =
            "너는 지휘자다. 작업자들의 결과를 모아 <task>에 대한 답 하나를 쓴다. "
                    + "결과끼리 어긋나면 그 사실을 밝히고, 실패한 하위 작업은 빠졌다고 알린다.";

    /** ok이면 text가 결과, 아니면 실패 사유다. */
    record WorkerResult(String subtask, boolean ok, String text) {}

    static List<String> plan(Llm llm, String task) {
        Llm.Reply reply = llm.complete(PLAN_SYSTEM, List.of(new Llm.Msg("user", "<task>\n" + task + "\n</task>")));
        List<String> subtasks = Llm.parseJsonList(reply.text());
        return List.copyOf(subtasks.subList(0, Math.min(subtasks.size(), MAX_SUBTASKS)));
    }

    /** 작업자 하나. 새 대화로 시작한다. */
    static String work(Llm llm, String subtask) {
        return llm.complete(WORKER_SYSTEM, List.of(new Llm.Msg("user", subtask))).text();
    }

    /** 작업자를 동시에 돌린다. 한 작업자가 실패하거나 늦어도 나머지 결과는 살린다. */
    static List<WorkerResult> runWorkers(Llm llm, List<String> subtasks, long timeoutMillis) {
        ExecutorService pool = Executors.newFixedThreadPool(MAX_SUBTASKS);
        try {
            List<Future<String>> futures = new ArrayList<>();
            for (String subtask : subtasks) {
                futures.add(pool.submit(() -> work(llm, subtask)));
            }
            long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
            List<WorkerResult> results = new ArrayList<>();
            for (int i = 0; i < subtasks.size(); i++) {
                String subtask = subtasks.get(i);
                try {
                    long remaining = Math.max(0, deadline - System.nanoTime());
                    results.add(new WorkerResult(subtask, true, futures.get(i).get(remaining, TimeUnit.NANOSECONDS)));
                } catch (TimeoutException e) {
                    results.add(new WorkerResult(subtask, false, "시간 안에 끝나지 않았다."));
                } catch (ExecutionException e) {
                    Throwable cause = e.getCause();
                    results.add(new WorkerResult(subtask, false,
                            cause.getClass().getSimpleName() + ": " + cause.getMessage()));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    results.add(new WorkerResult(subtask, false, "중단되었다."));
                }
            }
            return results;
        } finally {
            pool.shutdownNow(); // 늦은 작업자를 기다리지 않는다
        }
    }

    static String run(Llm llm, String task) {
        return run(llm, task, WORKER_TIMEOUT_MILLIS);
    }

    static String run(Llm llm, String task, long timeoutMillis) {
        List<String> subtasks = plan(llm, task);
        if (subtasks.isEmpty()) { // 나누지 못하면 혼자 처리한다
            return work(llm, task);
        }

        List<WorkerResult> results = runWorkers(llm, subtasks, timeoutMillis);
        StringBuilder report = new StringBuilder("<task>\n" + task + "\n</task>");
        for (int i = 0; i < results.size(); i++) {
            WorkerResult result = results.get(i);
            report.append("\n\n[하위 작업 ").append(i + 1).append("] ").append(result.subtask())
                    .append(result.ok() ? "\n[결과] " : "\n[실패] ").append(result.text());
        }
        return llm.complete(SYNTHESIS_SYSTEM, List.of(new Llm.Msg("user", report.toString()))).text();
    }

    public static void main(String[] args) {
        System.out.println(run(Llm.fromEnv(), "사내 문의 응대에 에이전트를 도입할 때의 장점, 위험, 필요한 준비를 정리해 줘."));
    }
}
