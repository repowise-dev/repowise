class Cart
  def initialize
    @items = []
  end

  def add(item)
    @items << item
  end

  def total
    @items.sum do |item|
      item * 2
    end
  rescue StandardError => e
    0
  end

  def self.empty
    new
  end
end

cart = Cart.empty
cart.add(10)
cart.total
