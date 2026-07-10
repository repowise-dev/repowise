# Perf-dialect fixture: block-iteration loops, the sink lexicon, string
# concat (+= flagged, << not), regex/resource construction, lock scopes.

require "net/http"

def sync_fetch_each(urls)
  urls.each do |url|
    Net::HTTP.get(URI(url))
  end
end

def read_files(paths)
  paths.map { |p| File.read(p) }
end

def nested_read(dirs)
  dirs.each do |d|
    Dir.glob(File.join(d, "*")).each do |f|
      File.read(f)
    end
  end
end

def orders_report(ids)
  ids.each do |id|
    Order.where(id: id)
  end
end

def constant_bound
  3.times do
    File.read("config.txt")
  end
  [1, 2].each do |i|
    File.read("f#{i}")
  end
end

def build_report(lines)
  out = ""
  lines.each do |line|
    out += "row: #{line}\n"
    buf = +""
    buf << line
  end
  out
end

def bounded_concat(lines)
  lines.each do |line|
    msg = "start"
    msg += " more"
    emit(msg)
  end
end

def regex_scan(rows, pattern)
  hoisted = Regexp.new(pattern)
  rows.each do |row|
    re = Regexp.new(pattern)
    re.match?(row) && hoisted.match?(row)
  end
end

def fresh_clients(hosts)
  hosts.each do |h|
    conn = Faraday.new(url: h)
    conn.get("/health")
  end
end

def shell_out(names)
  names.each do |n|
    `grep #{n} data.txt`
  end
end

def locked_write(mutex, items)
  items.each do |it|
    mutex.synchronize do
      File.write("log.txt", it)
    end
  end
end

def helper_calls(items)
  items.each { |i| transform(i) }
end
