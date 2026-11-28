class Api::V1::Accounts::ConversationsController < ApplicationController
  def index
    @conversations = conversation_finder.perform
  end

  def toggle_status
    @conversation = Conversation.find(params[:id])
    @status = @conversation.toggle_status
  end

  private

  def conversation_finder
    @conversation_finder ||= ConversationFinder.new(current_user, params)
  end
end
