# Control-flow fixture for the Ruby complexity walker.

def deeply_nested(x)
  if x > 0
    while x > 0
      if x > 1
        if x > 2
          puts x
        end
      end
      x -= 1
    end
  end
end

def many_branches(a, b)
  return 0 if a.nil?
  if a > 0 && b > 0
    1
  elsif a > 0 || b > 0
    2
  elsif a.zero?
    3
  else
    4
  end
end

def wordy(a, b)
  a and b or a
end

def shallow(x)
  x
end

def block_heavy(items)
  items.each do |group|
    group.map { |x| x * 2 }
  end
end

def flat_case(x)
  case x
  when 1 then :one
  when 2 then :two
  else :many
  end
end

def heavy_case(x)
  case x
  when 1
    if x.odd?
      :odd_one
    end
  when 2 then :two
  when 3 then :three
  end
end

def pattern_match(x)
  case x
  in { a: Integer => n }
    n
  in [b]
    b
  end
end

def risky_io(path)
  begin
    File.read(path)
  rescue ArgumentError => e
    puts e
  rescue TypeError
    puts "type"
  ensure
    puts "done"
  end
end

def implicit_rescue
  compute
rescue StandardError
  nil
end

def modifier_loops(x)
  x += 1 until x > 10
  x -= 1 while x > 0
  x
end
