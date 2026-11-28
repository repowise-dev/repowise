module AutoAssignmentHandler
  extend ActiveSupport::Concern

  private

  def run_auto_assignment
    AutoAssignment::Runner.new(self).perform
  end
end
