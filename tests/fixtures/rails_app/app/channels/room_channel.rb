class RoomChannel < ApplicationCable::Channel
  def subscribed
    stream_from 'room'
  end
end
