Rails.application.configure do
  config.to_prepare do
    EventDispatcher.subscribe(ConversationListener.instance)
  end
end
