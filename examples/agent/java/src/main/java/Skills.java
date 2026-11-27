import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

/**
 * Skills: 지침을 필요할 때만 읽게 한다.
 *
 * <p>스킬은 SKILL.md가 든 폴더다. SKILL.md 맨 위에 name과 description을 적는다.
 *
 * <ul>
 *   <li>시스템 프롬프트에는 스킬 목록(이름과 설명)만 넣는다. 스킬이 많아도 평소 프롬프트가 짧다.
 *   <li>모델이 지금 일에 맞는 스킬을 고르면 read_skill Tool로 본문을 읽는다.
 *   <li>에이전트 루프는 바뀌지 않는다. 시스템 프롬프트에 목록을 붙이고 Tool 하나를 더할 뿐이다.
 * </ul>
 *
 * <p>main은 java/ 폴더에서 실행한다(스킬 폴더를 ../skills에서 찾는다).
 */
public final class Skills {

    private Skills() {}

    static final Path SKILLS_DIR = Path.of("..", "skills");

    /** path는 SKILL.md 위치다. */
    record Skill(String name, String description, Path path) {}

    /** SKILL.md를 나눈 결과. meta는 --- 사이의 "키: 값" 줄이다. */
    record Parsed(Map<String, String> meta, String body) {}

    static Parsed parse(String text) {
        text = text.replace("\r\n", "\n");
        Map<String, String> meta = new LinkedHashMap<>();
        if (!text.startsWith("---")) {
            return new Parsed(meta, text);
        }
        int end = text.indexOf("\n---", 3);
        if (end < 0) {
            return new Parsed(meta, text);
        }
        for (String line : text.substring(3, end).split("\n")) {
            int colon = line.indexOf(':');
            if (colon > 0) {
                meta.put(line.substring(0, colon).strip(), line.substring(colon + 1).strip());
            }
        }
        return new Parsed(meta, text.substring(end + 4).stripLeading());
    }

    private static String read(Path path) {
        try {
            return Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** 폴더마다 SKILL.md의 머리말을 읽어 목록을 만든다. 이름이나 설명이 없는 스킬은 뺀다. */
    static List<Skill> load(Path directory) {
        List<Skill> skills = new ArrayList<>();
        try (Stream<Path> folders = Files.list(directory)) {
            for (Path folder : folders.sorted().toList()) {
                Path path = folder.resolve("SKILL.md");
                if (!Files.isRegularFile(path)) {
                    continue;
                }
                Map<String, String> meta = parse(read(path)).meta();
                String name = meta.getOrDefault("name", "");
                String description = meta.getOrDefault("description", "");
                if (!name.isEmpty() && !description.isEmpty()) {
                    skills.add(new Skill(name, description, path));
                }
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return skills;
    }

    /** 시스템 프롬프트에 붙일 스킬 목록. 본문은 넣지 않는다. */
    static String catalog(List<Skill> skills) {
        StringBuilder text = new StringBuilder(
                "\n\n쓸 수 있는 스킬 목록이다. 문의에 맞는 스킬이 있으면 답하기 전에 read_skill로 본문을 읽고 그 절차를 따른다.");
        skills.forEach(skill -> text.append("\n- ").append(skill.name()).append(": ").append(skill.description()));
        return text.toString();
    }

    /** 스킬 본문을 돌려주는 Tool. 목록에 있는 이름만 읽으므로 임의의 파일을 읽게 할 수 없다. */
    static OrderTools.Tool readSkillTool(List<Skill> skills) {
        Map<String, Skill> byName = new LinkedHashMap<>();
        skills.forEach(skill -> byName.put(skill.name(), skill));
        Map<String, Object> schema = Map.of(
                "type", "object",
                "properties", Map.of("name", Map.of("type", "string", "description", "스킬 목록에 있는 이름")),
                "required", List.of("name"),
                "additionalProperties", false);
        return new OrderTools.Tool("read_skill", "스킬 이름으로 그 스킬의 본문(절차와 안내 문구)을 읽는다.", schema, args -> {
            Skill skill = byName.get((String) args.get("name"));
            if (skill == null) {
                throw new OrderTools.ToolException("스킬 " + args.get("name") + "이(가) 없다. 쓸 수 있는 스킬: "
                        + String.join(", ", byName.keySet()));
            }
            return parse(read(skill.path())).body();
        });
    }

    /** 문의마다 필요한 스킬이 다르다. 모델이 어떤 스킬을 골라 읽는지 Tool 호출 순서로 확인한다. */
    static final List<String> INQUIRIES = List.of(
            "A-100 주문 금액이 얼마였죠?",                         // 스킬이 필요 없는 문의
            "A-200 주문 언제 도착하나요? 늦어지면 보상이 있나요?",   // delivery-delay
            "A-100 주문 취소해 주세요. 환불은 언제 되나요?");        // order-cancel

    public static void main(String[] args) {
        List<Skill> skills = load(SKILLS_DIR);
        String system = OrderTools.SYSTEM + catalog(skills); // 목록만 붙인다
        List<OrderTools.Tool> tools = new ArrayList<>(OrderTools.TOOLS);
        tools.add(readSkillTool(skills)); // Tool 하나를 더한다
        for (String inquiry : INQUIRIES) {
            List<String> calls = new ArrayList<>();
            AgentResult result = AgentRunner.runFromEnv(inquiry, system, tools,
                    (name, toolArgs, output) -> calls.add(name + "(" + toolArgs + ")"));
            System.out.println("문의: " + inquiry);
            System.out.println("Tool 호출: " + (calls.isEmpty() ? "없음" : String.join(" → ", calls)));
            System.out.println("답: " + result.display() + "\n");
        }
    }
}
