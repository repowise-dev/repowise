// Fixtures for Apex class-level (LCOM4 / god-class) walker tests. Apex reads
// its own members bare or through `this.`; both count as member references.
public class Classes {
    public class Cohesive {
        private Integer total = 0;
        private Integer count = 0;

        public void add(Integer n) {
            total += n;
            this.count += 1;
        }

        public Integer average() {
            return count == 0 ? 0 : total / count;
        }

        public void reset() {
            total = 0;
            count = 0;
        }
    }

    public class Splintered {
        private Integer a = 0;
        private Integer a2 = 0;
        private Integer b = 0;
        private Integer b2 = 0;

        public void setA(Integer v) {
            a = v;
            a2 = v;
        }

        public Integer getA() {
            return a + a2;
        }

        public void setB(Integer v) {
            this.b = v;
            this.b2 = v;
        }

        public Integer getB() {
            return b + b2;
        }
    }

    public class WithProperties {
        public String label { get; set; }
        public Integer size {
            get {
                return items.size();
            }
        }
        private List<String> items = new List<String>();

        public void add(String item) {
            items.add(item);
        }
    }

    public interface Shape {
        Decimal area();
    }

    public enum Mode { SYNC, ASYNC }
}
