import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

/**
 * JSON 변환기 하나를 모든 샘플이 함께 쓴다. ObjectMapper는 만드는 비용이 크고 스레드에 안전하다.
 *
 * <p>Jackson 3을 쓴다. 변환 오류는 확인 예외가 아니라 JacksonException(RuntimeException)으로 나온다.
 */
public final class Json {

    private Json() {}

    static final ObjectMapper MAPPER = JsonMapper.builder().build();
}
