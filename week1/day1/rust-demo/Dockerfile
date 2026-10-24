# rust-demo/Dockerfile
FROM rust:1.99.1-alpine AS build
WORKDIR /app
RUN apk add --no-cache musl-dev \
 && mkdir -p /usr/local/cargo \
 && printf '%s\n' '[source.crates-io]' 'replace-with = "rsproxy-sparse"' '[source.rsproxy-sparse]' 'registry = "sparse+https://rsproxy.cn/index/"' '[net]' 'git-fetch-with-cli = true' > /usr/local/cargo/config.toml
COPY . .
RUN cargo build --release

FROM scratch
COPY --from=build /app/target/release/rust-demo /app
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
USER 65532:65532
EXPOSE 8080
CMD ["/app"]
