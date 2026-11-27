import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 샘플 시험. 시험 도구 없이 main으로 돈다. 가짜 모델을 쓰므로 API 키가 필요 없다.
 *
 * <p>에이전트 루프(ClaudeOrderAgent, OpenAiCompatOrderAgent)는 SDK 응답 객체가 필요해서 여기서 다루지 않는다.
 */
public class SampleTests {

    interface Test {
        void run() throws Exception;
    }

    static int failed = 0;

    static void test(String name, Test body) {
        try {
            body.run();
            System.out.println("통과  " + name);
        } catch (Throwable e) {
            failed++;
            System.out.println("실패  " + name + ": " + e);
        }
    }

    static void check(boolean condition, String what) {
        if (!condition) {
            throw new AssertionError(what);
        }
    }

    static void eq(Object actual, Object expected) {
        check(expected.equals(actual), "기대 " + expected + ", 실제 " + actual);
    }

    static void throwsType(Class<? extends Throwable> type, Test body) {
        try {
            body.run();
        } catch (Throwable e) {
            check(type.isInstance(e), "기대한 예외 " + type.getSimpleName() + ", 실제 " + e);
            return;
        }
        throw new AssertionError(type.getSimpleName() + " 예외가 나지 않았다");
    }

    static Llm.Reply reply(String text) {
        return new Llm.Reply(text, "claude-opus-5-5", 1000, 500);
    }

    static void await(AgentService.Job... jobs) throws InterruptedException {
        for (AgentService.Job job : jobs) {
            check(job.finished.await(10, TimeUnit.SECONDS), job.jobId + "가 끝나지 않았다");
        }
    }

    public static void main(String[] args) throws Exception {
        Path tmp = Files.createTempDirectory("agent-sample-tests");

        // ── OrderTools ──
        test("Tool 거절은 사유와 함께 오류로 돌아온다", () -> {
            OrderTools.resetOrders();
            eq(OrderTools.run("get_order", Map.of("order_id", "A-100")),
                    new OrderTools.ToolOutput("{\"order_id\":\"A-100\",\"status\":\"결제완료\",\"amount\":32000}", false));
            eq(OrderTools.run("cancel_order", Map.of("order_id", "A-200")),
                    new OrderTools.ToolOutput("오류: 상태가 '배송중'인 주문은 취소할 수 없다.", true));
            eq(OrderTools.run("cancel_order", Map.of("order_id", "A-100")).isError(), false);
            eq(OrderTools.run("get_order", Map.of("order_id", "A-100")).text().contains("취소"), true);
            check(OrderTools.run("nope", Map.of()).isError(), "없는 Tool");
            eq(OrderTools.run("get_order", Map.of()).text(), "오류: 인자 order_id가 없다.");
            check(OrderTools.run("get_order", Map.of("order_id", 1)).isError(), "문자열이 아닌 인자");
            check(OrderTools.run("get_order", Map.of("order_id", "A-100", "x", "y")).isError(), "모르는 인자");
            check(OrderTools.run("get_order", null).isError(), "객체가 아닌 인자");
        });
        test("Tool 구현의 버그는 내부 사정을 숨기고 오류로 돌아온다", () -> {
            OrderTools.Tool broken = new OrderTools.Tool("get_order", "d", OrderTools.ORDER_ID_PROPERTIES,
                    List.of("order_id"), a -> { throw new IllegalStateException("secret-internal-detail"); });
            OrderTools.ToolOutput output = OrderTools.execute(broken, Map.of("order_id", "A-100"));
            check(output.isError() && !output.text().contains("secret-internal-detail"), output.text());
        });
        test("AgentResult는 성공과 중단을 status로 구분한다", () -> {
            check(AgentResult.done("답").ok() && AgentResult.done("답").display().equals("답"), "done");
            AgentResult stopped = AgentResult.stopped("상한");
            check(!stopped.ok() && stopped.text().isEmpty() && stopped.display().equals("[중단] 상한"), "stopped");
        });

        // ── Llm ──
        test("parseJsonList는 앞뒤 글이 붙어도 읽는다", () -> {
            eq(Llm.parseJsonList("x [\"a\", \"b\", 3] y"), List.of("a", "b"));
            eq(Llm.parseJsonList("없음"), List.of());
            eq(Llm.parseJsonList("[깨진"), List.of());
        });

        // ── MemoryChat ──
        test("대화 기록은 세션 안에, 장기 메모리는 세션을 넘어 이어진다", () -> {
            List<String> systems = Collections.synchronizedList(new ArrayList<>());
            Llm fake = (system, messages) -> {
                systems.add(system);
                String last = messages.get(messages.size() - 1).content();
                if (system.equals(MemoryChat.EXTRACT_SYSTEM)) {
                    return reply(last.split("<message>")[1].contains("민수") ? "[\"이름은 민수\"]" : "[]");
                }
                return reply("답 " + messages.size());
            };
            MemoryChat chat = new MemoryChat(fake, tmp + "/m1");
            chat.chat("s-1", "u-1", "내 이름은 민수야");
            eq(chat.chat("s-1", "u-1", "뭐였지?"), "답 3");
            eq(chat.chat("s-2", "u-1", "새 세션"), "답 1");
            eq(chat.memories.load("u-1"), List.of("이름은 민수"));
            eq(chat.sessions.load("s-1").size(), 4);
            check(systems.get(systems.size() - 2).contains("이름은 민수"), "새 세션 시스템 프롬프트에 메모리가 없다");
        });
        test("저장소는 폴더를 벗어날 수 있는 키를 거절한다", () -> {
            MemoryChat chat = new MemoryChat((s, m) -> reply("답"), tmp + "/m2/deep");
            for (String key : new String[] {"../../escaped", "a/b", "a\\b", "", "x".repeat(65), "s 1"}) {
                throwsType(IllegalArgumentException.class, () -> chat.sessions.save(key, List.of()));
                throwsType(IllegalArgumentException.class, () -> chat.sessions.load(key));
            }
            check(!Files.exists(tmp.resolve("m2/escaped.json")) && !Files.exists(tmp.resolve("escaped.json")), "폴더 밖 파일");
        });
        test("메모리 추출이 실패해도 답은 돌려준다", () -> {
            Llm fake = (system, messages) -> {
                if (system.equals(MemoryChat.EXTRACT_SYSTEM)) {
                    throw new IllegalStateException("추출 호출 실패");
                }
                return reply("답");
            };
            MemoryChat chat = new MemoryChat(fake, tmp + "/m3");
            eq(chat.chat("s-1", "u-1", "안녕"), "답");
            eq(chat.sessions.load("s-1").size(), 2);
        });
        test("recent는 복사본을 돌려주고 user로 시작한다", () -> {
            List<Llm.Msg> history = new ArrayList<>();
            history.add(new Llm.Msg("assistant", "a"));
            for (int i = 0; i < 10; i++) {
                history.add(new Llm.Msg("user", "u"));
                history.add(new Llm.Msg("assistant", "a"));
            }
            history.add(new Llm.Msg("user", "u"));
            List<Llm.Msg> window = MemoryChat.recent(history);
            eq(window.get(0).role(), "user");
            check(window.size() < MemoryChat.MAX_HISTORY, "창 크기");
            history.add(new Llm.Msg("assistant", "a")); // 원본이 바뀌어도 window는 그대로다
            eq(window.get(window.size() - 1).role(), "user");
        });

        // ── Tracing ──
        test("비용은 가장 길게 맞는 단가를 쓴다", () -> {
            Map<String, double[]> prices = Map.of("gpt-4o", new double[] {2.5, 10.0}, "gpt-4o-mini", new double[] {0.15, 0.6});
            check(Math.abs(Tracing.costUsd(prices, "gpt-4o-mini-2024-07-18", 1_000_000, 0) - 0.15) < 1e-9, "mini");
            check(Math.abs(Tracing.costUsd(prices, "gpt-4o-2024-08-06", 1_000_000, 1_000_000) - 12.5) < 1e-9, "4o");
            eq(Tracing.costUsd(prices, "unknown", 1_000_000, 1_000_000), 0.0);
        });
        test("span에 시각, 부모, 비용, 오류가 남는다", () -> {
            String path = tmp + "/t/traces.jsonl";
            Tracing.Tracer tracer = new Tracing.Tracer(path);
            Llm llm = tracer.traced((s, m) -> reply("답"), "T1");
            tracer.span("T1", "request", record -> {
                llm.complete("s", List.of());
                return llm.complete("s", List.of());
            });
            throwsType(IllegalStateException.class,
                    () -> tracer.span("T1", "broken", record -> { throw new IllegalStateException("x"); }));

            List<Map<String, Object>> spans = Tracing.readSpans(path);
            Map<String, Object> request = spans.stream().filter(s -> s.get("name").equals("request")).findFirst().orElseThrow();
            check(request.get("parent_id") == null, "최상위 span의 부모");
            for (Map<String, Object> span : spans) {
                check(span.containsKey("started_at") && span.containsKey("duration_ms"), "시각");
                if (span.get("name").equals("llm.call")) {
                    eq(span.get("parent_id"), request.get("span_id"));
                }
            }
            Tracing.Total total = Tracing.summarize(path).get("T1");
            eq(total.llmCalls(), 2);
            eq(total.inputTokens(), 2000L);
            eq(total.errors(), 1);
            check(Math.abs(total.costUsd() - 0.028) < 1e-9, "비용 " + total.costUsd());
        });

        // ── AgentService ──
        test("인증, 권한, 멱등 키", () -> {
            AgentService service = new AgentService((user, session, text) -> {
                AgentService.authorize(user, "cancel_order");
                return "취소함";
            }, 4, 3, 10);
            try {
                throwsType(AgentService.AuthException.class, () -> service.submit("key-wrong", "s-0", "x", "r-0"));
                AgentService.Job allowed = service.submit("key-agent", "s-1", "x", "r-1");
                AgentService.Job same = service.submit("key-agent", "s-1", "x", "r-1");
                AgentService.Job denied = service.submit("key-viewer", "s-2", "x", "r-2");
                await(allowed, denied);
                check(same == allowed && allowed.attempts.get() == 1, "멱등 키");
                eq(allowed.status, "done");
                check(denied.status.equals("failed") && denied.error.contains("SecurityException"), denied.error);
            } finally {
                service.shutdown();
            }
        });
        test("일시적 오류는 재시도하고 끝까지 실패하면 따로 모은다", () -> {
            Map<String, Integer> failures = new ConcurrentHashMap<>(Map.of("flaky", 2, "down", 99));
            AgentService service = new AgentService((user, session, text) -> {
                if (failures.get(text) > 0) {
                    failures.merge(text, -1, Integer::sum);
                    throw new AgentService.TransientException("시간 초과");
                }
                return "ok";
            }, 4, 3, 5);
            try {
                AgentService.Job flaky = service.submit("key-agent", "s-1", "flaky", "r-1");
                AgentService.Job down = service.submit("key-agent", "s-2", "down", "r-2");
                await(flaky, down);
                check(flaky.status.equals("done") && flaky.attempts.get() == 3, flaky.status);
                check(down.status.equals("dead") && down.attempts.get() == 3, down.status);
                eq(service.dead, List.of(down));
            } finally {
                service.shutdown();
            }
        });
        test("재시도 대기가 작업자를 붙잡지 않는다", () -> {
            CountDownLatch firstTry = new CountDownLatch(1);
            AtomicBoolean failedOnce = new AtomicBoolean();
            AgentService service = new AgentService((user, session, text) -> {
                if (text.equals("retry") && failedOnce.compareAndSet(false, true)) {
                    firstTry.countDown();
                    throw new AgentService.TransientException("한 번 실패");
                }
                return "ok";
            }, 1, 3, 1000); // 작업자 하나, 재시도 대기 1초
            try {
                AgentService.Job retrying = service.submit("key-agent", "s-1", "retry", "r-1");
                check(firstTry.await(5, TimeUnit.SECONDS), "첫 시도");
                long started = System.nanoTime();
                AgentService.Job fast = service.submit("key-agent", "s-2", "fast", "r-2");
                await(fast);
                check(System.nanoTime() - started < 500_000_000L, "빠른 작업이 재시도 대기를 기다렸다");
                await(retrying);
                eq(retrying.status, "done");
            } finally {
                service.shutdown();
            }
        });
        test("같은 세션은 순서대로 하나씩 처리하고 세션 기록을 남기지 않는다", () -> {
            Set<String> running = ConcurrentHashMap.newKeySet();
            List<String> order = Collections.synchronizedList(new ArrayList<>());
            AtomicReference<String> overlap = new AtomicReference<>();
            AgentService service = new AgentService((user, session, text) -> {
                if (!running.add(session)) {
                    overlap.set(text);
                }
                try {
                    Thread.sleep(20);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                order.add(text);
                running.remove(session);
                return text;
            }, 4, 3, 10);
            try {
                AgentService.Job[] jobs = new AgentService.Job[6];
                for (int i = 0; i < jobs.length; i++) {
                    jobs[i] = service.submit("key-agent", "s-1", "m" + i, "r-" + i);
                }
                await(jobs);
                check(overlap.get() == null, "겹쳐 실행됨: " + overlap.get());
                eq(order, List.of("m0", "m1", "m2", "m3", "m4", "m5"));
                eq(service.waitingSessions(), 0);
            } finally {
                service.shutdown();
            }
        });
        test("Error가 나도 작업은 끝난다", () -> {
            AgentService service = new AgentService((user, session, text) -> {
                throw new StackOverflowError("버그");
            }, 2, 3, 10);
            try {
                AgentService.Job job = service.submit("key-agent", "s-1", "x", "r-1");
                await(job);
                check(job.status.equals("failed") && job.error.contains("StackOverflowError"), job.error);
            } finally {
                service.shutdown();
            }
        });
        test("끝난 작업은 기억 시간이 지나면 잊는다", () -> {
            AgentService service = new AgentService((user, session, text) -> "ok", 2, 3, 10, java.time.Duration.ofMillis(50));
            try {
                AgentService.Job first = service.submit("key-agent", "s-1", "x", "r-1");
                await(first);
                Thread.sleep(100);
                AgentService.Job second = service.submit("key-agent", "s-1", "x", "r-1");
                await(second);
                check(second != first, "새 작업이어야 한다");
                eq(service.rememberedJobs(), 1);
            } finally {
                service.shutdown();
            }
        });

        // ── MultiAgent ──
        test("작업자 결과와 실패가 지휘자에게 전달된다", () -> {
            AtomicReference<String> report = new AtomicReference<>();
            Llm fake = (system, messages) -> {
                String content = messages.get(messages.size() - 1).content();
                if (system.equals(MultiAgent.PLAN_SYSTEM)) {
                    return reply("[\"a\", \"b\", \"c\"]");
                }
                if (system.equals(MultiAgent.WORKER_SYSTEM)) {
                    if (content.equals("b")) {
                        throw new IllegalStateException("작업자 오류");
                    }
                    return reply(content + " 결과");
                }
                report.set(content);
                return reply("종합");
            };
            eq(MultiAgent.run(fake, "큰 일"), "종합");
            check(report.get().contains("[하위 작업 1] a\n[결과] a 결과"), report.get());
            check(report.get().contains("[하위 작업 2] b\n[실패] IllegalStateException: 작업자 오류"), report.get());
        });
        test("늦은 작업자는 시간 초과로 처리하고 나머지는 살린다", () -> {
            Llm fake = (system, messages) -> {
                String content = messages.get(messages.size() - 1).content();
                if (content.equals("slow")) {
                    try {
                        Thread.sleep(1000);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                    }
                }
                return reply(content + " 결과");
            };
            long started = System.nanoTime();
            List<MultiAgent.WorkerResult> results = MultiAgent.runWorkers(fake, List.of("a", "slow"), 200);
            check(System.nanoTime() - started < 800_000_000L, "전체가 늦은 작업자를 기다렸다");
            check(results.get(0).ok() && !results.get(1).ok(), results.toString());
        });
        test("나눌 수 없으면 혼자 처리하고, 하위 작업 수에 상한이 있다", () -> {
            eq(MultiAgent.run((system, messages) -> system.equals(MultiAgent.PLAN_SYSTEM)
                    ? reply("나눌 수 없음") : reply("혼자 처리"), "작은 일"), "혼자 처리");
            eq(MultiAgent.plan((s, m) -> reply("[\"1\",\"2\",\"3\",\"4\",\"5\",\"6\"]"), "x").size(), MultiAgent.MAX_SUBTASKS);
        });

        System.out.println(failed == 0 ? "모두 통과" : failed + "개 실패");
        System.exit(failed == 0 ? 0 : 1);
    }
}
