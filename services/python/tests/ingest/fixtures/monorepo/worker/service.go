//go:build ignore

// 该目录是 ingest 语言分析器的测试语料，不是可编译代码：
// 故意 import 不存在的 example.com/storage/client 用于验证依赖提取，
// 加 ignore 约束以免根模块 go build/test ./... 被它打断。

package worker

import (
	"context"
	db "example.com/storage/client"
)

type Service struct{ Client *db.Client }

func (s *Service) Find(
	ctx context.Context,
	query string,
) (string, error) {
	return query, nil
}

func New() *Service { return &Service{} }
