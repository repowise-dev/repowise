Rails.application.routes.draw do
  namespace :api do
    namespace :v1 do
      resources :accounts, only: [:show] do
        scope module: :accounts do
          resources :conversations, only: [:index] do
            member do
              post :toggle_status
            end
          end
          resources :bulk_actions, only: [:create]
        end
      end
    end
  end

  namespace :admin do
    resources :conversations, only: [:index]
  end

  scope module: :public do
    resources :portals, only: [:show]
  end
end
