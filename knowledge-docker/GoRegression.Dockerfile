FROM golang:1.24.7-alpine3.22
RUN apk add --no-cache build-base
WORKDIR /workspace/services/go
COPY services/go/go.mod services/go/go.sum ./
RUN go mod download
COPY services/go ./
CMD ["go", "test", "-race", "./...", "-count=1"]
