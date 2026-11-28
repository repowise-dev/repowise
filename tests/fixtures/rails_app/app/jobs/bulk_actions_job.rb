class BulkActionsJob < ApplicationJob
  def perform(account, params)
    Conversation.where(id: params[:ids]).each(&:toggle_status)
  end
end
