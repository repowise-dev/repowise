class ConversationListener
  def self.instance
    @instance ||= new
  end

  def conversation_created(event)
    CampaignConversationBuilder.new(event).perform
  end
end
