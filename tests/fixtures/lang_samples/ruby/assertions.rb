require "minitest/autorun"

class TestFoo < Minitest::Test
  def test_many_asserts
    result = compute
    assert_equal 1, result
    assert result
    assert_includes [1, 2], result
    assert_equal 2, result + 1
    assert_operator result, :<, 10
  end

  def test_few_asserts
    result = compute
    assert_equal 1, result
    puts result
    refute_nil result
  end
end
