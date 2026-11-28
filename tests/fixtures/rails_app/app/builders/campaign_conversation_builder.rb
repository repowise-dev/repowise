class CampaignConversationBuilder
  def initialize(event)
    @event = event
  end

  def perform
    @event
  end
end
