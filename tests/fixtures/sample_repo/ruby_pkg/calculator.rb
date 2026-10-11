# Calculator class for basic arithmetic
require_relative './models'

class Calculator < BaseCalculator
  def add(x, y)
    result = x + y
    record(x, y, result)
    result
  end

  def subtract(x, y)
    result = x - y
    record(x, y, result)
    result
  end

  private

  def record(x, y, result)
    @history << CalculationRecord.new(x, y, result)
  end
end
