require_relative 'boot'

module RailsApp
  class Application < Rails::Application
    config.eager_load_paths << Rails.root.join('enterprise/lib')
    config.eager_load_paths += Dir["#{Rails.root}/enterprise/app/**"]
  end
end
