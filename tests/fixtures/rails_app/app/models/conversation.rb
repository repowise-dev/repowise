class Conversation < ApplicationRecord
  include AutoAssignmentHandler

  STATUSES = %w[open resolved].freeze

  after_save_commit :run_auto_assignment

  def toggle_status
    self.status = open? ? :resolved : :open
    save
  end
end

Conversation.prepend_mod_with('Conversation')
