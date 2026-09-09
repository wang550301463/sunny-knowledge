package example;

import java.util.List;
import static java.util.Collections.emptyList;

/** Source text mentioning fakeMethod() is not a declaration. */
public class Service {
    public Service() {}
    public List<String> find(
        String query,
        int limit
    ) {
        return emptyList();
    }
    public String find(int id) { return ""; }
    public static class Nested {
        public void run() {}
    }
}