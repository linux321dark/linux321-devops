# go-demo/Dockerfile
# 注意：基础镜像必须钉死到具体补丁版本（golang:1.26 这种浮动 tag 经镜像加速常解析为 not found）。
# 此处 1.26.0 与 §0.4 主机安装的 GO_VER 保持一致（你 ECS 实际为 Go 1.26）。
FROM golang:1.26.0 AS build
# 国内 Go 代理（ECS 直连 proxy.golang.org 超时），保证 build 内 go mod download 可用
ENV GOPROXY=https://mirrors.aliyun.com/goproxy/,https://goproxy.cn,direct
ENV GOSUMDB=sum.golang.google.cn
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /bin/app .

FROM scratch
COPY --from=build /bin/app /bin/app
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
# gin 需 HTTPS/TLS 时带 CA
USER 65532:65532
EXPOSE 8080
CMD ["/bin/app"]
