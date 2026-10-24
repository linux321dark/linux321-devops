require "sinatra"
require "json"
set :bind, "0.0.0.0"
set :port, 4567
get "/" do
  content_type :json
  { msg: "DevOps Journey Day1 - Ruby", framework: "sinatra" }.to_json
end
