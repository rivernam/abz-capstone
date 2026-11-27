import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;

/**
 * 인증과 권한, 동시 실행 처리, 재시도 큐. 에이전트를 여러 사람이 쓰는 서비스로 만들 때 루프 바깥에 두는 것들이다.
 *
 * <ul>
 *   <li>인증: 요청을 보낸 사람이 누구인지 확인한다.
 *   <li>권한: 그 사람이 쓸 수 있는 Tool만 실행한다. 프롬프트가 아니라 Tool을 실행하는 코드에서 막는다.
 *   <li>동시 실행: 작업자 수로 동시에 도는 에이전트 수를 제한하고, 같은 세션의 요청은 한 번에 하나만 처리한다.
 *   <li>재시도 큐: 일시적 오류는 간격을 늘려 가며 다시 시도하고, 끝까지 실패하면 따로 모아 둔다.
 *   <li>멱등 키: 같은 요청이 두 번 들어와도 한 번만 처리한다.
 * </ul>
 *
 * <p>main은 LLM 없이 돈다. 가짜 에이전트로 동작만 보여 준다.
 */
public class AgentService {

    record User(String userId, String role) {}

    // 시연용 표. 실제로는 키의 해시를 DB에 두거나 사내 SSO(OIDC) 토큰을 검증한다
    static final Map<String, User> API_KEYS = Map.of(
            "key-viewer", new User("u-1", "viewer"),
            "key-agent", new User("u-2", "agent"));
    static final Map<String, Set<String>> ROLE_TOOLS = Map.of(
            "viewer", Set.of("get_order"),
            "agent", Set.of("get_order", "cancel_order"));

    static class AuthException extends RuntimeException {
        AuthException(String message) {
            super(message);
        }
    }

    /** 다시 시도하면 될 수 있는 오류: 시간 초과, 호출 한도(429), 서버 오류(5xx). */
    static class TransientException extends RuntimeException {
        TransientException(String message) {
            super(message);
        }
    }

    static User authenticate(String apiKey) {
        User user = API_KEYS.get(apiKey);
        if (user == null) {
            throw new AuthException("알 수 없는 API 키다.");
        }
        return user;
    }

    /** Tool을 실행하기 직전에 부른다. 모델이 요청했더라도 권한이 없으면 실행하지 않는다. */
    static void authorize(User user, String toolName) {
        if (!ROLE_TOOLS.get(user.role()).contains(toolName)) {
            throw new SecurityException(user.role() + " 역할은 Tool " + toolName + "을(를) 쓸 수 없다.");
        }
    }

    @FunctionalInterface
    interface AgentRunner {
        String run(User user, String sessionId, String text);
    }

    static final class Job {
        final String jobId;
        final User user;
        final String sessionId;
        final String text;
        final CountDownLatch finished = new CountDownLatch(1);
        volatile int attempts;
        volatile String status = "queued"; // queued -> done | retrying -> ... | failed | dead
        volatile String result;
        volatile String error;

        Job(String jobId, User user, String sessionId, String text) {
            this.jobId = jobId;
            this.user = user;
            this.sessionId = sessionId;
            this.text = text;
        }
    }

    private final AgentRunner runner;
    private final int maxAttempts;
    private final long baseDelayMillis;
    private final ExecutorService workers; // 작업자 수가 동시에 도는 에이전트 수의 상한이다
    private final ScheduledExecutorService timer = Executors.newSingleThreadScheduledExecutor();
    private final Map<String, Job> jobs = new ConcurrentHashMap<>(); // 멱등 키 -> job
    private final Map<String, ReentrantLock> sessionLocks = new ConcurrentHashMap<>();
    final List<Job> dead = new CopyOnWriteArrayList<>(); // 끝까지 실패한 작업. 사람이 확인한다

    AgentService(AgentRunner runner, int workerCount, int maxAttempts, long baseDelayMillis) {
        this.runner = runner;
        this.maxAttempts = maxAttempts;
        this.baseDelayMillis = baseDelayMillis;
        this.workers = Executors.newFixedThreadPool(workerCount);
    }

    Job submit(String apiKey, String sessionId, String text, String idempotencyKey) {
        User user = authenticate(apiKey); // 인증에 실패하면 큐에 넣지 않는다
        Job job = new Job(idempotencyKey, user, sessionId, text);
        Job existing = jobs.putIfAbsent(idempotencyKey, job);
        if (existing != null) { // 같은 요청이 다시 왔다: 새로 만들지 않는다
            return existing;
        }
        workers.submit(() -> attempt(job));
        return job;
    }

    private void attempt(Job job) {
        ReentrantLock lock = sessionLocks.computeIfAbsent(job.sessionId, key -> new ReentrantLock());
        lock.lock(); // 같은 세션은 한 번에 하나만
        try {
            job.attempts++;
            job.result = runner.run(job.user, job.sessionId, job.text);
            finish(job, "done", null);
        } catch (TransientException e) {
            if (job.attempts < maxAttempts) {
                long delay = baseDelayMillis << (job.attempts - 1); // 200ms, 400ms, ... 간격을 늘린다
                job.status = "retrying";
                timer.schedule(() -> workers.submit(() -> attempt(job)), delay, TimeUnit.MILLISECONDS);
            } else {
                dead.add(job);
                finish(job, "dead", e.getMessage());
            }
        } catch (RuntimeException e) { // 권한 없음, 잘못된 입력: 다시 해도 같으므로 재시도하지 않는다
            finish(job, "failed", e.getClass().getSimpleName() + ": " + e.getMessage());
        } finally {
            lock.unlock();
        }
    }

    private static void finish(Job job, String status, String error) {
        job.status = status;
        job.error = error;
        job.finished.countDown();
    }

    void shutdown() {
        workers.shutdown();
        timer.shutdown();
    }

    public static void main(String[] args) throws InterruptedException {
        Map<String, Integer> failuresLeft = new ConcurrentHashMap<>(Map.of("불안정", 2, "계속 실패", 99));

        // 에이전트 루프 자리. 실제로는 여기서 에이전트를 돌리고, Tool 실행 직전에 authorize를 부른다
        AgentRunner fakeAgent = (user, sessionId, text) -> {
            for (String word : failuresLeft.keySet()) {
                if (text.contains(word) && failuresLeft.get(word) > 0) {
                    failuresLeft.merge(word, -1, Integer::sum);
                    throw new TransientException("모델 서버 시간 초과");
                }
            }
            String tool = text.contains("취소") ? "cancel_order" : "get_order";
            authorize(user, tool);
            return user.userId() + "가 " + tool + " 실행";
        };

        AgentService service = new AgentService(fakeAgent, 4, 3, 200);
        try {
            service.submit("key-wrong", "s-0", "조회", "req-0");
        } catch (AuthException e) {
            System.out.println("인증 실패: " + e.getMessage());
        }

        List<Job> jobs = List.of(
                service.submit("key-agent", "s-1", "A-100 취소", "req-1"),
                service.submit("key-agent", "s-1", "A-100 취소", "req-1"),         // 같은 멱등 키: 위와 같은 job
                service.submit("key-viewer", "s-2", "A-100 취소", "req-2"),        // 권한 없음
                service.submit("key-agent", "s-3", "불안정한 조회", "req-3"),       // 두 번 실패하고 세 번째에 성공
                service.submit("key-agent", "s-4", "계속 실패하는 조회", "req-4")); // 끝까지 실패
        for (Job job : jobs) {
            job.finished.await();
        }
        service.shutdown();

        System.out.println("멱등 키가 같으면 같은 job: " + (jobs.get(0) == jobs.get(1)));
        for (Job job : jobs.subList(1, jobs.size())) {
            System.out.println(job.jobId + ": " + job.status + ", 시도 " + job.attempts + "회, "
                    + (job.result != null ? job.result : job.error));
        }
        System.out.println("끝까지 실패한 작업: " + service.dead.stream().map(job -> job.jobId).toList());
    }
}
