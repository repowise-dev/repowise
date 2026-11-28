namespace :api do
  namespace :v1 do
    resources :accounts, only: [:index]
  end
end
