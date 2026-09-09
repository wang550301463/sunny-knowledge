FROM golang:1.24.7-alpine3.22 AS build
WORKDIR /src
ENV GOPROXY=https://goproxy.cn,direct
RUN apk add --no-cache ca-certificates git
COPY services/go/go.mod services/go/go.sum ./
RUN go mod download
COPY services/go/ ./
ARG SERVICE_NAME
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/service ./cmd/${SERVICE_NAME}

FROM alpine:3.22.1
RUN apk add --no-cache ca-certificates wget && addgroup -g 10001 app && adduser -D -u 10001 -G app app
COPY --from=build /out/service /usr/local/bin/service
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/service"]