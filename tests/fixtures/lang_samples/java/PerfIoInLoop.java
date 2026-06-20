// Fixture for the Java performance dialect (io_in_loop / string_concat /
// regex_compile_in_loop). Every POSITIVE is a genuine per-iteration I/O
// boundary or wasted recompile; every NEGATIVE is a shape the dialect must NOT
// flag. Hand-counted expectations live in test_perf_java.py. NOTE: this file
// imports a db library, so file-level db evidence is present; ambiguous-verb
// gating (find/get without a db import) is tested inline instead.
import java.nio.file.Files;
import java.util.List;
import java.util.regex.Pattern;
import org.springframework.data.jpa.repository.JpaRepository;

class PerfIoInLoop {

    // --- POSITIVES ---------------------------------------------------------

    void springDataN1(List<String> ids) {
        for (String id : ids) {
            repo.findByName(id);            // POSITIVE: Spring-Data derived query
        }
    }

    void jdbcInLoop(List<String> ids, java.sql.Statement st) {
        for (String id : ids) {
            st.executeQuery(id);            // POSITIVE: JDBC round-trip
        }
    }

    void filesInLoop(List<String> paths) {
        for (String p : paths) {
            Files.readString(java.nio.file.Path.of(p));  // POSITIVE: filesystem
        }
    }

    void newStreamInLoop(List<String> paths) {
        for (String p : paths) {
            new java.io.FileInputStream(p);  // POSITIVE: filesystem constructor
        }
    }

    void regexCompileInLoop(List<String> ids) {
        for (String id : ids) {
            Pattern.compile(id);             // POSITIVE: recompiled per iteration
        }
    }

    String stringConcatInLoop(List<String> rows) {
        String out = "";
        for (String r : rows) {
            out += "line";                   // POSITIVE: quadratic string build
        }
        return out;
    }

    // --- NEGATIVES ---------------------------------------------------------

    void pureCallInLoop(List<String> items) {
        int total = 0;
        for (String x : items) {
            total += x.length();             // NEGATIVE: numeric +=, pure call
        }
    }

    void sinkOutsideLoop(java.sql.Statement st) {
        st.executeQuery("SELECT 1");         // NEGATIVE: runs once, not in a loop
    }

    void regexCompileOnce(List<String> ids) {
        Pattern p = Pattern.compile("[0-9]+"); // NEGATIVE: compiled once, hoisted
        for (String id : ids) {
            p.matcher(id).matches();           // NEGATIVE: matches() is not a sink
        }
    }
}
