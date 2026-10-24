# ruby-demo/Dockerfile
FROM ruby:3.2.3-slim AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY Gemfile ./
RUN bundle config set --global mirror.https://rubygems.org https://mirrors.aliyun.com/rubygems/ \
 && bundle config set --local without 'development test' \
 && bundle install --jobs 4
COPY . .

FROM ruby:3.2.3-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bundle /usr/local/bundle
COPY --from=build /app /app
USER 1000
EXPOSE 4567
CMD ["ruby","app.rb"]
