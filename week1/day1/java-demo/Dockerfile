# java-demo/Dockerfile
FROM maven:3.9-eclipse-temurin-17 AS builder
WORKDIR /app
# 阿里云 Maven 镜像（直连 repo1.maven.org 超时）
RUN mkdir -p /root/.m2 && printf '%s' \
  '<settings><mirrors><mirror><id>aliyun</id><mirrorOf>central</mirrorOf><name>aliyun maven</name><url>https://maven.aliyun.com/repository/public</url></mirror></mirrors></settings>' \
  > /root/.m2/settings.xml
COPY pom.xml .
RUN mvn dependency:go-offline -q
COPY src ./src
RUN mvn package -DskipTests -q

FROM eclipse-temurin:17-jre-alpine AS runtime
WORKDIR /app
COPY --from=builder /app/target/*.jar app.jar
USER 1000
EXPOSE 8080
ENTRYPOINT ["java","-jar","/app/app.jar"]
