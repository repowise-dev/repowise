class ConversationFinder
  def initialize(user, params)
    @user = user
    @params = params
  end

  def perform
    Conversation.where(status: Conversation::STATUSES)
  end
end

ConversationFinder.prepend_mod_with('ConversationFinder')
