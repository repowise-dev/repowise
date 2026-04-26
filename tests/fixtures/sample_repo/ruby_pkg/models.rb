# Data models for the calculator
module Models
  # A record of a single calculation
  class CalculationRecord
    attr_reader :x, :y, :result

    def initialize(x, y, result)
      @x = x
      @y = y
      @result = result
    end
  end

  # Supported operations
  class Operation
    ADD = :add
    SUBTRACT = :subtract
  end
end
