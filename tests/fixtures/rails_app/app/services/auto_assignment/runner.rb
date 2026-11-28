class AutoAssignment::Runner
  def initialize(conversation)
    @conversation = conversation
  end

  def perform
    @conversation.toggle_status
  end
end
