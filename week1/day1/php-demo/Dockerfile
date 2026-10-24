# php-demo/Dockerfile
FROM composer:2 AS build
WORKDIR /app
ENV COMPOSER_ALLOW_SUPERUSER=1
RUN composer config -g repos.packagist composer https://repo.packagist.org
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --optimize-autoloader --ignore-platform-reqs
COPY . .

FROM php:8.3-cli-alpine AS runtime
WORKDIR /app
COPY --from=build /app /app
USER www-data
EXPOSE 8080
CMD ["php","-S","0.0.0.0:8080","-t","public"]
