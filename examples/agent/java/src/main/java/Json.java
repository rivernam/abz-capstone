import com.fasterxml.jackson.databind.ObjectMapper;

/** JSON 변환기 하나를 모든 샘플이 함께 쓴다. ObjectMapper는 만드는 비용이 크고 스레드에 안전하다. */
public final class Json {

    private Json() {}

    static final ObjectMapper MAPPER = new ObjectMapper();
}
