Rails.application.routes.draw do
  root to: "home#index"

  get "/404" => "about#four_oh_four", :via => :all

  get "/rss" => "home#index", :format => "rss"
  get "/hottest" => "home#index", :format => "json"

  get "/page/:page" => "home#index"
  get "/active" => "home#active"

  # get "/commented_out" => "home#commented"

  match "/login/set_new_password" => "login#set_new_password",
    :as => "set_new_password", :via => [:get, :post]

  resources :stories, except: [:index] do
    post "upvote"
    resources :suggestions, only: [:new, :create]
  end

  resources :comments, except: [:new, :destroy] do
    member do
      get "reply"
      post "delete"
    end
    collection do
      get "search"
    end
  end

  namespace :admin do
    resources :users, only: [:index, :show]
    get "/dashboard" => "dashboard#index"
  end

  scope "/v1" do
    get "/status" => "status#show"
  end
end
