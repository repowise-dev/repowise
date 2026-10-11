# Class-metrics fixture: method counts / god-class facts work off class_kinds;
# LCOM4 deliberately sits at its "no signal" valve for Ruby (receiver-less
# @ivar idiom), so even the splintered class must report lcom4 == 1.

class Cohesive
  def initialize
    @x = 0
  end

  def bump
    @x += 1
  end

  def read
    @x
  end
end

class Splintered
  def a
    @x = 1
  end

  def b
    @x
  end

  def c
    @y = 1
  end

  def d
    @y
  end

  def e
    if @y
      42
    else
      7
    end
  end
end

module Util
  def self.helper_one
    1
  end

  def helper_two
    2
  end
end
