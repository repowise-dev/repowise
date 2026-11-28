class Api::V1::Accounts::BulkActionsController < ApplicationController
  def create
    ::BulkActionsJob.perform_later(current_account, params)
    head :ok if EnterpriseBilling.enabled?
  end
end
